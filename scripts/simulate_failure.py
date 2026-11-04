#!/usr/bin/env python3
"""Inject a failure and (optionally) watch the platform detect and recover it.

Examples
--------
    python scripts/simulate_failure.py --host web-01 --metric cpu --value 97 --duration 180
    python scripts/simulate_failure.py --host db-01 --metric disk --value 94 --duration 300 --watch
    python scripts/simulate_failure.py --host api-01 --metric down --duration 120 --watch
    python scripts/simulate_failure.py --clear

Uses only the standard library so it runs anywhere Python 3.9+ is available.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_API = "http://localhost:8000/api/v1"
DEFAULT_SIM = "http://localhost:9101"


def request(method: str, url: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {url}: {exc.reason}") from exc


def get(url: str, timeout: float = 15.0) -> dict:
    return request("GET", url, None, timeout)


def inject(api: str, sim: str, host: str, metric: str, value: float | None, duration: int) -> None:
    payload = {"hostname": host, "metric": metric, "value": value, "duration_seconds": duration}
    try:
        result = request("POST", f"{api}/simulation/faults", payload)
        print(f"[inject] backend accepted fault: {json.dumps(payload)}")
        print(f"[inject] expires at {result.get('expires_at')}")
    except SystemExit:
        print("[inject] backend unavailable, posting directly to the simulator", file=sys.stderr)
        result = request("POST", f"{sim}/sim/faults", payload)
        print(f"[inject] expires at {result.get('expires_at')}")


def clear(api: str, sim: str, host: str | None) -> None:
    query = f"?hostname={host}" if host else ""
    try:
        request("DELETE", f"{api}/simulation/faults{query}")
    except SystemExit:
        request("DELETE", f"{sim}/sim/faults{query}")
    print(f"[clear] faults cleared{f' for {host}' if host else ''}")


def watch(api: str, host: str, metric: str, timeout: int = 300) -> int:
    """Poll the platform until the incident appears, then until it resolves."""
    deadline = time.time() + timeout
    incident = None
    print(f"[watch] waiting for an incident on {host} ({metric}) ...")
    while time.time() < deadline:
        data = get(f"{api}/incidents?status=active&page_size=100")
        for item in data.get("items", []):
            if item.get("hostname") == host and item.get("metric") == metric:
                incident = item
                break
        if incident:
            break
        time.sleep(5)

    if not incident:
        print("[watch] no incident detected within the timeout", file=sys.stderr)
        return 1

    print(
        f"[watch] incident {incident['reference']} detected: "
        f"{incident['severity'].upper()} / {incident['category']} / value={incident['observed_value']}"
    )
    detail = get(f"{api}/incidents/{incident['id']}")
    for action in detail.get("recovery_actions", []):
        print(
            f"[watch] recovery #{action['attempt']} {action['playbook']} -> "
            f"{action['status']} (dry_run={action['dry_run']})"
            + (f" blocked: {action['safeguard_reason']}" if action.get("safeguard_reason") else "")
        )

    print("[watch] waiting for resolution ...")
    while time.time() < deadline:
        detail = get(f"{api}/incidents/{incident['id']}")
        if detail["status"] == "resolved":
            mttr = detail.get("mttr_seconds") or 0
            print(f"[watch] incident {detail['reference']} resolved, MTTR {mttr:.0f}s")
            return 0
        time.sleep(5)
    print("[watch] incident still active at timeout", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Inject infrastructure faults into the simulator")
    parser.add_argument("--host", default="web-01", help="virtual host name (web-01, api-01, db-01)")
    parser.add_argument("--metric", default="cpu", choices=["cpu", "memory", "disk", "network", "down"])
    parser.add_argument("--value", type=float, default=95.0, help="target value (percent or Mbit/s)")
    parser.add_argument("--duration", type=int, default=180, help="fault duration in seconds")
    parser.add_argument("--watch", action="store_true", help="wait for detection and recovery")
    parser.add_argument("--timeout", type=int, default=300, help="watch timeout in seconds")
    parser.add_argument("--clear", action="store_true", help="clear all active faults and exit")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--sim", default=DEFAULT_SIM)
    args = parser.parse_args()

    if args.clear:
        clear(args.api, args.sim, None)
        return 0

    value = None if args.metric == "down" else args.value
    inject(args.api, args.sim, args.host, args.metric, value, args.duration)
    if args.watch:
        return watch(args.api, args.host, args.metric, args.timeout)
    print("[done] fault injected; run with --watch to follow detection and recovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
