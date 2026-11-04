"""Prometheus HTTP API client (instant + range queries).

Kept dependency-light and fully mockable: ``PrometheusClient`` accepts any
object exposing an ``httpx``-style ``get`` so tests can inject fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class PrometheusError(RuntimeError):
    pass


@dataclass(slots=True)
class Sample:
    labels: dict[str, str]
    value: float
    timestamp: float

    @property
    def instance(self) -> str:
        return self.labels.get("instance", "")

    @property
    def hostname(self) -> str:
        return self.labels.get("node") or self.labels.get("hostname") or self.instance.split(":")[0]


class PrometheusClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None, transport: Any = None):
        self.base_url = (base_url or settings.prometheus_url).rstrip("/")
        self.timeout = timeout or settings.request_timeout_seconds
        self._transport = transport

    # -- low level ---------------------------------------------------------
    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self._transport)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:  # network / status errors
            raise PrometheusError(f"prometheus request failed: {exc}") from exc
        if payload.get("status") != "success":
            raise PrometheusError(f"prometheus returned {payload.get('status')}: {payload.get('error')}")
        return payload.get("data", {})

    # -- queries -----------------------------------------------------------
    def query(self, promql: str) -> list[Sample]:
        data = self._get("/api/v1/query", {"query": promql})
        return _parse_vector(data)

    def query_range(self, promql: str, start: datetime, end: datetime, step: str = "60s") -> list[list[tuple[float, float]]]:
        data = self._get(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )
        return _parse_matrix(data)

    def value(self, promql: str) -> float | None:
        samples = self.query(promql)
        return samples[0].value if samples else None

    def health(self) -> bool:
        try:
            with self._client() as client:
                response = client.get("/-/healthy")
                return response.status_code == 200
        except httpx.HTTPError:
            return False


def _parse_vector(data: dict[str, Any]) -> list[Sample]:
    out: list[Sample] = []
    for item in data.get("result", []) or []:
        try:
            ts, raw = item["value"]
            out.append(Sample(labels=item.get("metric", {}), value=float(raw), timestamp=float(ts)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _parse_matrix(data: dict[str, Any]) -> list[list[tuple[float, float]]]:
    series: list[list[tuple[float, float]]] = []
    for item in data.get("result", []) or []:
        points: list[tuple[float, float]] = []
        for ts, raw in item.get("values", []) or []:
            try:
                points.append((float(ts), float(raw)))
            except (TypeError, ValueError):
                continue
        series.append(points)
    return series


# --------------------------------------------------------------------------- #
# Canonical PromQL used by the detector and the dashboard.
# All queries work against node_exporter and against the bundled simulator,
# which emits node_exporter-compatible metric names.
# --------------------------------------------------------------------------- #
QUERIES: dict[str, str] = {
    "availability": 'up{job="node"}',
    "cpu": '100 - (avg without (cpu) (rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)',
    "memory": "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))",
    "disk": '100 * (1 - (min without (device, fstype) (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"}) / min without (device, fstype) (node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs"})))',
    "network": 'sum by (instance) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*"}[2m])) * 8',
}

METRIC_LABELS: dict[str, dict[str, str]] = {
    "cpu": {"display_name": "CPU Utilisation", "unit": "percent", "category": "cpu"},
    "memory": {"display_name": "Memory Utilisation", "unit": "percent", "category": "memory"},
    "disk": {"display_name": "Disk Utilisation", "unit": "percent", "category": "disk"},
    "network": {"display_name": "Network Throughput", "unit": "bps", "category": "network"},
    "availability": {"display_name": "Host Availability", "unit": "ratio", "category": "availability"},
}


def default_range() -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(hours=1), end
