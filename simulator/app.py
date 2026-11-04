"""node_exporter-compatible metrics simulator with fault injection.

Purpose
-------
The platform must be demonstrable and testable without a fleet of Linux VMs.
This service exposes *real* node_exporter metric names for three virtual hosts
and lets you inject CPU / memory / disk / network faults (or take a host down)
through a small HTTP API, so Prometheus -> Alertmanager -> backend -> Ansible
can be exercised end to end.

Endpoints
---------
GET    /metrics          Prometheus exposition (node_exporter compatible)
GET    /healthz          liveness
GET    /sim/hosts        virtual host inventory + current values
GET    /sim/faults       active injected faults
POST   /sim/faults       {"hostname","metric","value","duration_seconds"}
DELETE /sim/faults       clear all faults (optional ?hostname=)
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOCK = threading.RLock()


class VirtualHost:
    def __init__(self, hostname: str, instance: str, role: str) -> None:
        self.hostname = hostname
        self.instance = instance
        self.role = role
        self.base_cpu = 18.0
        self.base_memory = 42.0
        self.base_disk = 46.0
        self.base_net_mbps = 1.5
        self.mem_total = 8 * 1024**3
        self.disk_total = 100 * 1024**3
        self.cpu_cores = 4
        self.cpu_seconds = {f"{core}": {"idle": 0.0, "user": 0.0, "system": 0.0, "iowait": 0.0} for core in range(self.cpu_cores)}
        self.net_rx_bytes = 0.0
        self.net_tx_bytes = 0.0
        self.up = True
        self.last_tick = time.time()
        self.faults: dict[str, dict] = {}

    # -- fault handling ----------------------------------------------------
    def set_fault(self, metric: str, value: float | None, duration: int) -> dict:
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(1, duration))
        fault = {
            "hostname": self.hostname,
            "metric": metric,
            "value": value,
            "expires_at": expires.isoformat(),
        }
        self.faults[metric] = fault
        if metric == "down":
            self.up = False
        return fault

    def clear_faults(self) -> None:
        self.faults.clear()
        self.up = True

    def _active_fault(self, metric: str) -> dict | None:
        fault = self.faults.get(metric)
        if fault is None:
            return None
        if datetime.fromisoformat(fault["expires_at"]) <= datetime.now(timezone.utc):
            self.faults.pop(metric, None)
            if metric == "down":
                self.up = True
            return None
        return fault

    # -- values ------------------------------------------------------------
    def cpu_percent(self) -> float:
        fault = self._active_fault("cpu")
        if fault and fault["value"] is not None:
            return max(0.0, min(100.0, float(fault["value"])))
        # deterministic-ish jitter so dashboards are not flat lines
        wave = (time.time() % 60) / 60.0
        return max(0.0, min(100.0, self.base_cpu + 6.0 * wave))

    def memory_percent(self) -> float:
        fault = self._active_fault("memory")
        if fault and fault["value"] is not None:
            return max(0.0, min(100.0, float(fault["value"])))
        return self.base_memory

    def disk_percent(self) -> float:
        fault = self._active_fault("disk")
        if fault and fault["value"] is not None:
            return max(0.0, min(100.0, float(fault["value"])))
        return self.base_disk

    def net_mbps(self) -> float:
        fault = self._active_fault("network")
        if fault and fault["value"] is not None:
            return max(0.0, float(fault["value"]))
        wave = (time.time() % 120) / 120.0
        return self.base_net_mbps + 0.9 * wave

    def tick(self) -> None:
        now = time.time()
        elapsed = max(0.0, now - self.last_tick)
        self.last_tick = now
        if elapsed <= 0:
            return
        utilization = self.cpu_percent() / 100.0
        per_core_total = elapsed
        busy = per_core_total * utilization
        idle = per_core_total * (1.0 - utilization)
        for core in self.cpu_seconds:
            self.cpu_seconds[core]["idle"] += idle
            self.cpu_seconds[core]["user"] += busy * 0.7
            self.cpu_seconds[core]["system"] += busy * 0.2
            self.cpu_seconds[core]["iowait"] += busy * 0.1
        rate_bytes = self.net_mbps() * 1_000_000 / 8.0
        self.net_rx_bytes += rate_bytes * elapsed
        self.net_tx_bytes += rate_bytes * 0.6 * elapsed

    def snapshot(self) -> dict:
        return {
            "hostname": self.hostname,
            "instance": self.instance,
            "role": self.role,
            "up": self.up,
            "cpu_percent": round(self.cpu_percent(), 2),
            "memory_percent": round(self.memory_percent(), 2),
            "disk_percent": round(self.disk_percent(), 2),
            "network_mbps": round(self.net_mbps(), 2),
            "faults": list(self.faults.keys()),
        }

    # -- exposition --------------------------------------------------------
    def render(self) -> str:
        node = self.hostname
        lines: list[str] = []
        add = lines.append

        add("# HELP node_cpu_seconds_total Seconds the CPUs spent in each mode.")
        add("# TYPE node_cpu_seconds_total counter")
        for core, modes in self.cpu_seconds.items():
            for mode, value in modes.items():
                add(f'node_cpu_seconds_total{{node="{node}",cpu="{core}",mode="{mode}"}} {value:.3f}')

        mem_total = self.mem_total
        mem_used = mem_total * self.memory_percent() / 100.0
        add("# HELP node_memory_MemTotal_bytes Memory information field MemTotal_bytes.")
        add("# TYPE node_memory_MemTotal_bytes gauge")
        add(f'node_memory_MemTotal_bytes{{node="{node}"}} {mem_total}')
        add("# HELP node_memory_MemAvailable_bytes Memory information field MemAvailable_bytes.")
        add("# TYPE node_memory_MemAvailable_bytes gauge")
        add(f'node_memory_MemAvailable_bytes{{node="{node}"}} {mem_total - mem_used:.0f}')

        disk_total = self.disk_total
        disk_used = disk_total * self.disk_percent() / 100.0
        add("# HELP node_filesystem_size_bytes Filesystem size in bytes.")
        add("# TYPE node_filesystem_size_bytes gauge")
        add(f'node_filesystem_size_bytes{{node="{node}",device="/dev/sda1",mountpoint="/",fstype="ext4"}} {disk_total}')
        add("# HELP node_filesystem_avail_bytes Filesystem space available to non-root users in bytes.")
        add("# TYPE node_filesystem_avail_bytes gauge")
        add(
            f'node_filesystem_avail_bytes{{node="{node}",device="/dev/sda1",mountpoint="/",fstype="ext4"}} '
            f"{disk_total - disk_used:.0f}"
        )

        add("# HELP node_network_receive_bytes_total Network device statistic receive_bytes.")
        add("# TYPE node_network_receive_bytes_total counter")
        add(f'node_network_receive_bytes_total{{node="{node}",device="eth0"}} {self.net_rx_bytes:.0f}')
        add("# HELP node_network_transmit_bytes_total Network device statistic transmit_bytes.")
        add("# TYPE node_network_transmit_bytes_total counter")
        add(f'node_network_transmit_bytes_total{{node="{node}",device="eth0"}} {self.net_tx_bytes:.0f}')

        add("# HELP node_load1 1m load average.")
        add("# TYPE node_load1 gauge")
        add(f'node_load1{{node="{node}"}} {self.cpu_percent() / 100.0 * self.cpu_cores:.2f}')

        add("# HELP sim_host_up Simulated host reachability (1 up, 0 down).")
        add("# TYPE sim_host_up gauge")
        add(f'sim_host_up{{node="{node}"}} {1 if self.up else 0}')

        return "\n".join(lines) + "\n"


HOSTS: dict[str, VirtualHost] = {
    "web-01": VirtualHost("web-01", "10.10.0.11:9100", "web"),
    "api-01": VirtualHost("api-01", "10.10.0.12:9100", "api"),
    "db-01": VirtualHost("db-01", "10.10.0.13:9100", "database"),
}
HOSTS["db-01"].base_memory = 58.0
HOSTS["db-01"].base_disk = 62.0


class Handler(BaseHTTPRequestHandler):
    server_version = "ship-simulator/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        if os.environ.get("SIM_VERBOSE") == "1":
            print(fmt % args)

    # -- helpers -----------------------------------------------------------
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, body: str, content_type: str = "text/plain; version=0.0.4") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/metrics", "/"):
            with LOCK:
                chunks = []
                for host in HOSTS.values():
                    host.tick()
                    chunks.append(host.render())
            self._text(200, "\n".join(chunks))
            return
        if path.startswith("/metrics/"):
            hostname = path.rsplit("/", 1)[-1]
            host = HOSTS.get(hostname)
            if host is None:
                self._json(404, {"detail": f"unknown hostname '{hostname}'"})
                return
            with LOCK:
                host.tick()
                if not host.up:
                    # a real node_exporter on a dead host is unreachable, so
                    # the scrape must fail for `up` to become 0
                    self._text(503, f"simulated host {hostname} is down\n")
                    return
                body = host.render()
            self._text(200, body)
            return
        if path == "/healthz":
            self._json(200, {"status": "ok", "hosts": len(HOSTS)})
            return
        if path == "/sim/hosts":
            with LOCK:
                self._json(200, {"hosts": [host.snapshot() for host in HOSTS.values()]})
            return
        if path == "/sim/faults":
            with LOCK:
                faults = [fault for host in HOSTS.values() for fault in host.faults.values()]
            self._json(200, {"faults": faults})
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/sim/faults":
            self._json(404, {"detail": "not found"})
            return
        payload = self._read_json()
        hostname = payload.get("hostname")
        metric = (payload.get("metric") or "").lower()
        if hostname not in HOSTS:
            self._json(404, {"detail": f"unknown hostname '{hostname}'; known: {sorted(HOSTS)}"})
            return
        if metric not in ("cpu", "memory", "disk", "network", "down"):
            self._json(422, {"detail": "metric must be one of cpu, memory, disk, network, down"})
            return
        value = payload.get("value")
        duration = int(payload.get("duration_seconds") or 120)
        with LOCK:
            fault = HOSTS[hostname].set_fault(metric, None if value is None else float(value), duration)
            snapshot = [f for host in HOSTS.values() for f in host.faults.values()]
        self._json(201, {"fault": fault, "expires_at": fault["expires_at"], "faults": snapshot})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/sim/faults":
            self._json(404, {"detail": "not found"})
            return
        params = parse_qs(urlparse(self.path).query)
        hostname = (params.get("hostname") or [None])[0]
        with LOCK:
            targets = [HOSTS[hostname]] if hostname in HOSTS else list(HOSTS.values())
            for host in targets:
                host.clear_faults()
        self._json(200, {"detail": "faults cleared", "hostname": hostname, "faults": []})


def main() -> None:
    port = int(os.environ.get("SIM_PORT", "9101"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"simulator listening on :{port} for hosts {sorted(HOSTS)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
