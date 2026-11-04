"""Detection cycle tests against a fake Prometheus."""

from __future__ import annotations

from datetime import timedelta

from app.models import (
    Host,
    HostStatus,
    Incident,
    IncidentStatus,
    MetricSample,
    RecoveryAction,
    RecoveryStatus,
    Severity,
    Threshold,
)
from app.services import incidents as incident_service
from app.services.detector import Detector, evaluate_threshold
from app.services.prometheus import PrometheusError
from tests.conftest import FakePrometheus, baseline_metrics, make_action, set_threshold, utcnow


def _detector(values, fail: bool = False) -> Detector:
    return Detector(prometheus=FakePrometheus(values, fail=fail))


def _zero_durations(db) -> None:
    for metric in ("cpu", "memory", "disk", "network", "availability"):
        set_threshold(db, metric, duration_seconds=0)


def test_detects_cpu_breach_and_dispatches_recovery(db):
    _zero_durations(db)
    values = baseline_metrics()
    values["cpu"]["10.10.0.11:9100"] = 96.0

    summary = _detector(values).evaluate_once(db)

    assert summary.hosts_evaluated == 3
    assert summary.breaches == 1
    assert summary.incidents_opened == 1
    assert summary.recoveries_triggered == 1
    assert summary.recoveries_blocked == 0

    incident = db.query(Incident).one()
    assert incident.category == "cpu"
    assert incident.severity == Severity.CRITICAL
    assert incident.status == IncidentStatus.RECOVERING
    assert incident.observed_value == 96.0

    action = db.query(RecoveryAction).one()
    assert action.status == RecoveryStatus.SIMULATED
    assert action.playbook == "recover_high_cpu.yml"
    assert action.dry_run is True
    assert action.triggered_by == "auto"

    web = db.query(Host).filter(Host.hostname == "web-01").one()
    assert web.status == HostStatus.DEGRADED


def test_repeated_detection_does_not_duplicate_incidents(db):
    _zero_durations(db)
    values = baseline_metrics()
    values["cpu"]["10.10.0.11:9100"] = 96.0
    detector = _detector(values)

    first = detector.evaluate_once(db)
    second = detector.evaluate_once(db)

    assert first.incidents_opened == 1
    assert second.incidents_opened == 0
    assert second.incidents_refreshed == 1
    assert db.query(Incident).count() == 1
    # simulated (dry-run) actions are not retried automatically
    assert db.query(RecoveryAction).count() == 1


def test_auto_resolves_when_metric_returns_to_normal(db):
    _zero_durations(db)
    values = baseline_metrics()
    values["cpu"]["10.10.0.11:9100"] = 96.0
    detector = _detector(values)
    detector.evaluate_once(db)
    assert db.query(Incident).filter(Incident.status == IncidentStatus.RECOVERING).count() == 1

    healthy = baseline_metrics()
    later = utcnow() + timedelta(minutes=15)
    summary = _detector(healthy).evaluate_once(db, now=later)

    assert summary.incidents_resolved == 1
    incident = db.query(Incident).one()
    assert incident.status == IncidentStatus.RESOLVED
    assert incident.mttr_seconds is not None


def test_duration_window_defers_breach(db):
    for metric in ("cpu", "memory", "disk", "network", "availability"):
        set_threshold(db, metric, duration_seconds=300)
    values = baseline_metrics()
    values["memory"]["10.10.0.12:9100"] = 97.0

    detector = _detector(values)
    first = detector.evaluate_once(db)
    assert first.breaches == 0
    assert first.incidents_opened == 0

    sustained = detector.evaluate_once(db, now=utcnow() + timedelta(minutes=6))
    assert sustained.breaches == 1
    assert sustained.incidents_opened == 1


def test_prometheus_failure_is_reported_without_side_effects(db):
    summary = _detector(baseline_metrics(), fail=True).evaluate_once(db)
    assert summary.hosts_evaluated == 0
    assert summary.errors
    assert "unreachable" in summary.errors[0]
    assert db.query(Incident).count() == 0


def test_host_down_creates_availability_incident(db):
    _zero_durations(db)
    values = baseline_metrics()
    values["availability"]["10.10.0.13:9100"] = 0.0
    values["cpu"].pop("10.10.0.13:9100")
    values["memory"].pop("10.10.0.13:9100")
    values["disk"].pop("10.10.0.13:9100")
    values["network"].pop("10.10.0.13:9100")

    summary = _detector(values).evaluate_once(db)

    assert summary.incidents_opened == 1
    incident = db.query(Incident).one()
    assert incident.category == "availability"
    assert incident.severity == Severity.CRITICAL
    assert incident.recommended_playbook == "recover_service.yml"

    db_host = db.query(Host).filter(Host.hostname == "db-01").one()
    assert db_host.status == HostStatus.DOWN


def test_healthy_fleet_creates_no_incidents(db):
    _zero_durations(db)
    summary = _detector(baseline_metrics()).evaluate_once(db)
    assert summary.breaches == 0
    assert summary.incidents_opened == 0
    assert db.query(Incident).count() == 0
    assert all(host.status == HostStatus.UP for host in db.query(Host).all())


def test_metric_samples_are_persisted(db):
    _zero_durations(db)
    _detector(baseline_metrics()).evaluate_once(db)
    assert db.query(MetricSample).count() == 15  # 3 hosts x 5 metrics
    names = {sample.metric_name for sample in db.query(MetricSample).all()}
    assert names == {"cpu", "memory", "disk", "network", "availability"}


def test_breaker_blocks_automatic_recovery_without_new_incident(db):
    _zero_durations(db)
    host = db.query(Host).filter(Host.hostname == "web-01").one()
    make_action(db, host_id=host.id, action_type="cpu", created_minutes_ago=0.5, finished_minutes_ago=0.5)

    values = baseline_metrics()
    values["cpu"]["10.10.0.11:9100"] = 96.0
    summary = _detector(values).evaluate_once(db)

    assert summary.incidents_opened == 1
    assert summary.recoveries_blocked == 1
    action = db.query(RecoveryAction).order_by(RecoveryAction.id.desc()).first()
    assert action.status == RecoveryStatus.BLOCKED
    assert "cooldown" in action.safeguard_reason


def test_failed_recovery_is_retried(db, monkeypatch):
    _zero_durations(db)
    host = db.query(Host).filter(Host.hostname == "web-01").one()
    incident = Incident(
        reference="INC-77777",
        title="CPU saturation",
        category="cpu",
        severity="critical",
        status=IncidentStatus.OPEN,
        host_id=host.id,
        fingerprint=incident_service.fingerprint("threshold", "web-01", "cpu"),
        metric="cpu",
        detected_at=utcnow(),
        recommended_playbook="recover_high_cpu.yml",
    )
    db.add(incident)
    db.commit()
    make_action(
        db,
        host_id=host.id,
        incident_id=incident.id,
        action_type="cpu",
        status=RecoveryStatus.FAILED,
        created_minutes_ago=20,
        finished_minutes_ago=19,
    )

    values = baseline_metrics()
    values["cpu"]["10.10.0.11:9100"] = 96.0
    summary = _detector(values).evaluate_once(db)

    assert summary.incidents_opened == 0
    assert summary.incidents_refreshed == 1
    assert summary.recoveries_triggered == 1
    assert db.query(Incident).count() == 1


def test_evaluate_threshold_respects_comparator(db):
    cpu = db.query(Threshold).filter_by(metric="cpu").one()
    assert evaluate_threshold(74.9, cpu) is None
    assert evaluate_threshold(75.0, cpu) == Severity.WARNING
    assert evaluate_threshold(90.0, cpu) == Severity.CRITICAL

    availability = (
        db.query(Threshold).filter_by(metric="availability").one()
    )
    assert evaluate_threshold(1.0, availability) is None
    assert evaluate_threshold(0.0, availability) == Severity.CRITICAL


def test_evaluate_threshold_ignores_disabled_rule(db):
    cpu = db.query(Threshold).filter_by(metric="cpu").one()
    cpu.enabled = False
    db.commit()
    assert evaluate_threshold(99.0, cpu) is None


def test_prometheus_error_type_is_used():
    fake = FakePrometheus(fail=True)
    try:
        fake.query("up")
    except PrometheusError as exc:
        assert "unreachable" in str(exc)




def test_alert_and_detector_share_one_incident(db):
    """Both detection paths must converge on a single incident per host+metric."""
    _zero_durations(db)
    from app.schemas import AlertmanagerAlert
    from app.services import incidents as incident_service

    alert = AlertmanagerAlert(
        status="firing",
        labels={
            "alertname": "CpuSaturation",
            "severity": "critical",
            "instance": "10.10.0.11:9100",
            "node": "web-01",
            "metric": "cpu",
        },
        annotations={"summary": "CPU saturation on web-01", "value": "97.4"},
    )
    result = incident_service.ingest_alert(db, alert)
    assert result is not None
    assert db.query(Incident).count() == 1
    assert result.incident.metric == "cpu"
    assert result.incident.observed_value == 97.4

    values = baseline_metrics()
    values["cpu"]["10.10.0.11:9100"] = 96.0
    summary = _detector(values).evaluate_once(db)

    assert summary.incidents_opened == 0
    assert summary.incidents_refreshed == 1
    assert db.query(Incident).count() == 1
    assert db.query(RecoveryAction).count() == 1
