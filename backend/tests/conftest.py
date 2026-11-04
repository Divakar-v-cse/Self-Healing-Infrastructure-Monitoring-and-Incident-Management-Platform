"""Pytest fixtures.

Environment is configured *before* importing the application so the cached
Settings object never points at the developer's real database or integrations.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_DB = Path(tempfile.gettempdir()) / f"ship_test_{os.getpid()}.db"
os.environ["SHIP_DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["SHIP_ENABLE_MONITOR_LOOP"] = "false"
os.environ["SHIP_ANSIBLE_DRY_RUN"] = "true"
os.environ["SHIP_ENVIRONMENT"] = "testing"
os.environ["SHIP_PROMETHEUS_URL"] = "http://127.0.0.1:9"
os.environ["SHIP_ALERTMANAGER_URL"] = "http://127.0.0.1:9"
os.environ["SHIP_SIMULATOR_URL"] = "http://127.0.0.1:9"
os.environ["SHIP_MAX_ATTEMPTS_PER_INCIDENT"] = "3"
os.environ["SHIP_MAX_ATTEMPTS_PER_WINDOW"] = "5"
os.environ["SHIP_RECOVERY_COOLDOWN_SECONDS"] = "300"
os.environ["SHIP_CIRCUIT_BREAKER_THRESHOLD"] = "3"
os.environ["SHIP_CIRCUIT_BREAKER_COOLDOWN_SECONDS"] = "1800"
os.environ["SHIP_MAX_CONCURRENT_RECOVERIES"] = "2"
os.environ["SHIP_INCIDENT_AUTORESOLVE_MINUTES"] = "10"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import configure_database, get_session_factory, reset_database  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Host,
    HostStatus,
    Incident,
    IncidentStatus,
    RecoveryAction,
    RecoveryStatus,
    Threshold,
)
from app.seed import seed_all  # noqa: E402
from app.services.prometheus import QUERIES, PrometheusError, Sample  # noqa: E402


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def db():
    configure_database()
    reset_database()
    session = get_session_factory()()
    seed_all(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    configure_database()
    reset_database()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def host(db) -> Host:
    host = db.query(Host).filter(Host.hostname == "web-01").first()
    assert host is not None
    return host


@pytest.fixture()
def incident(db, host) -> Incident:
    incident = Incident(
        reference="INC-90001",
        title="Test incident",
        category="cpu",
        severity="critical",
        status=IncidentStatus.OPEN,
        host_id=host.id,
        fingerprint="test-fingerprint",
        metric="cpu",
        observed_value=95.0,
        threshold_value=90.0,
        recommended_playbook="recover_high_cpu.yml",
        detected_at=utcnow(),
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def make_action(
    db,
    *,
    host_id: int | None = None,
    incident_id: int | None = None,
    action_type: str = "cpu",
    status: str = RecoveryStatus.SUCCESS,
    attempt: int = 1,
    created_minutes_ago: float = 120,
    finished_minutes_ago: float | None = None,
) -> RecoveryAction:
    created = utcnow() - timedelta(minutes=created_minutes_ago)
    finished = None
    if status in RecoveryStatus.TERMINAL:
        minutes = created_minutes_ago if finished_minutes_ago is None else finished_minutes_ago
        finished = utcnow() - timedelta(minutes=minutes)
    action = RecoveryAction(
        host_id=host_id,
        incident_id=incident_id,
        action_type=action_type,
        playbook="recover_high_cpu.yml",
        status=status,
        attempt=attempt,
        triggered_by="test",
        dry_run=True,
        created_at=created,
        started_at=created,
        finished_at=finished,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def set_threshold(db, metric: str, **kwargs) -> Threshold:
    threshold = db.query(Threshold).filter(Threshold.metric == metric).first()
    assert threshold is not None
    for key, value in kwargs.items():
        setattr(threshold, key, value)
    db.commit()
    db.refresh(threshold)
    return threshold


class FakePrometheus:
    """Minimal stand-in for :class:`PrometheusClient`."""

    def __init__(self, values: dict[str, dict[str, float]] | None = None, fail: bool = False):
        self.values = values or {}
        self.fail = fail
        self.queries: list[str] = []

    def query(self, promql: str):
        self.queries.append(promql)
        if self.fail:
            raise PrometheusError("prometheus is unreachable (simulated)")
        metric = next((name for name, query in QUERIES.items() if query == promql), None)
        if metric is None:
            raise AssertionError(f"unexpected query: {promql}")
        return [
            Sample(
                labels={"instance": instance, "node": instance.split(":")[0]},
                value=float(value),
                timestamp=0.0,
            )
            for instance, value in self.values.get(metric, {}).items()
        ]


def baseline_metrics(cpu: float = 20.0, memory: float = 40.0, disk: float = 45.0, network: float = 12_000_000.0):
    instances = ["10.10.0.11:9100", "10.10.0.12:9100", "10.10.0.13:9100"]
    return {
        "availability": {instance: 1.0 for instance in instances},
        "cpu": {instance: cpu for instance in instances},
        "memory": {instance: memory for instance in instances},
        "disk": {instance: disk for instance in instances},
        "network": {instance: network for instance in instances},
    }


@pytest.fixture()
def fake_prometheus():
    return FakePrometheus(baseline_metrics())


@pytest.fixture(autouse=True)
def _restore_settings():
    snapshot = {key: getattr(settings, key) for key in (
        "recovery_enabled",
        "ansible_enabled",
        "ansible_dry_run",
        "max_attempts_per_incident",
        "max_attempts_per_window",
        "recovery_cooldown_seconds",
        "circuit_breaker_threshold",
        "circuit_breaker_cooldown_seconds",
        "max_concurrent_recoveries",
        "incident_autoresolve_minutes",
    )}
    yield
    for key, value in snapshot.items():
        setattr(settings, key, value)
