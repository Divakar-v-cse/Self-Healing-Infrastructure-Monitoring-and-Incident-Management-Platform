"""Classification engine tests."""

from __future__ import annotations

import pytest

from app.models import Category, Severity
from app.services import classifier


def test_exact_alert_rule_maps_cpu():
    result = classifier.classify_alert(
        {"alertname": "HighCpuUsage", "instance": "10.10.0.11:9100", "severity": "warning"},
        {"summary": "High CPU on web-01"},
    )
    assert result.category == Category.CPU
    assert result.severity == Severity.WARNING
    assert result.playbook == "recover_high_cpu.yml"
    assert "matched exact alert rule table" in result.reason
    assert result.title == "High CPU on web-01"
    assert result.runbook


def test_disk_alert_is_critical_and_uses_disk_playbook():
    result = classifier.classify_alert(
        {"alertname": "HostDiskFull", "instance": "db-01:9100", "severity": "critical"}, {}
    )
    assert result.category == Category.DISK
    assert result.severity == Severity.CRITICAL
    assert result.playbook == "recover_disk_pressure.yml"


@pytest.mark.parametrize(
    ("alertname", "expected"),
    [
        ("CustomCpuSpike", Category.CPU),
        ("MemoryLeakDetected", Category.MEMORY),
        ("FilesystemAlmostFull", Category.DISK),
        ("PacketLossDetected", Category.NETWORK),
        ("NodeUnreachable", Category.AVAILABILITY),
        ("SystemdUnitFailed", Category.SERVICE),
        ("SomethingTotallyUnknown", Category.UNKNOWN),
    ],
)
def test_keyword_fallback_classification(alertname, expected):
    result = classifier.classify_alert({"alertname": alertname, "instance": "host:9100"}, {})
    assert result.category == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("critical", Severity.CRITICAL),
        ("PAGE", Severity.CRITICAL),
        ("sev1", Severity.CRITICAL),
        ("warn", Severity.WARNING),
        ("medium", Severity.WARNING),
        ("info", Severity.INFO),
        ("bogus", Severity.WARNING),
        (None, Severity.WARNING),
    ],
)
def test_severity_normalisation(raw, expected):
    labels = {"alertname": "HighCpuUsage", "instance": "h:9100"}
    if raw:
        labels["severity"] = raw
    assert classifier.classify_alert(labels, {}).severity == expected


def test_threshold_breach_severity_critical():
    result = classifier.classify_threshold_breach(
        metric="memory", observed=95.0, warning=80.0, critical=92.0, unit="percent", hostname="web-01"
    )
    assert result.severity == Severity.CRITICAL
    assert result.category == Category.MEMORY
    assert "95.00%" in result.reason
    assert result.title == "Memory pressure detected on web-01"


def test_threshold_breach_severity_warning():
    result = classifier.classify_threshold_breach(
        metric="cpu", observed=80.0, warning=75.0, critical=90.0, hostname="api-01"
    )
    assert result.severity == Severity.WARNING
    assert result.playbook == "recover_high_cpu.yml"


def test_availability_uses_inverse_comparator():
    result = classifier.classify_threshold_breach(
        metric="availability",
        observed=0.0,
        warning=1.0,
        critical=0.5,
        unit="ratio",
        hostname="db-01",
        comparator="<",
    )
    assert result.severity == Severity.CRITICAL
    assert result.category == Category.AVAILABILITY
    assert result.playbook == "recover_service.yml"


def test_root_cause_hint_mentions_playbook():
    hint = classifier.root_cause_hint(Category.DISK, {"playbook": "recover_disk_pressure.yml"})
    assert "Disk pressure" in hint
    assert "recover_disk_pressure.yml" in hint


def test_unknown_alert_falls_back_to_health_check_playbook():
    result = classifier.classify_alert({"alertname": "MysterySignal", "instance": "x:1"}, {})
    assert result.category == Category.UNKNOWN
    assert result.playbook == "health_check.yml"
