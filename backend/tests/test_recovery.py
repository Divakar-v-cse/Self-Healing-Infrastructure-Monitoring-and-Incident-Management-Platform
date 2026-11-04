"""Ansible runner and recovery orchestration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.models import Incident, IncidentStatus, RecoveryAction, RecoveryStatus
from app.services import recovery
from tests.conftest import make_action


@pytest.fixture()
def runner() -> recovery.AnsibleRunner:
    return recovery.AnsibleRunner()


def test_dry_run_is_the_safe_default(runner):
    assert runner.dry_run is True
    result = runner.run("recover_high_cpu.yml", "web-01")
    assert result.status == RecoveryStatus.SIMULATED
    assert "DRY-RUN" in result.output
    assert "SHIP_ANSIBLE_DRY_RUN=false" in result.output


def test_command_targets_a_single_host(runner):
    playbook = Path(settings.playbook_dir) / "recover_high_cpu.yml"
    command = runner.build_command(playbook, "db-01")
    joined = " ".join(command)
    assert "--limit db-01" in joined
    assert "recover_high_cpu.yml" in joined
    assert "target_host=db-01" in joined
    assert command[0] == settings.ansible_binary


def test_missing_playbook_fails(runner):
    result = runner.run("does_not_exist.yml", "web-01")
    assert result.status == RecoveryStatus.FAILED
    assert "playbook not found" in result.output


def test_path_traversal_is_rejected(runner):
    result = runner.run("../../etc/passwd", "web-01")
    assert result.status == RecoveryStatus.FAILED
    assert "escapes the playbook directory" in result.output


def test_real_execution_fails_without_inventory(tmp_path, runner):
    real = recovery.AnsibleRunner(inventory=tmp_path / "missing.ini", dry_run=False)
    result = real.run("recover_high_cpu.yml", "web-01")
    assert result.status == RecoveryStatus.FAILED
    assert "inventory not found" in result.output


def test_trigger_creates_action_and_marks_incident_recovering(db, incident):
    action = recovery.trigger_recovery(db, incident, triggered_by="test")
    assert action.status == RecoveryStatus.SIMULATED
    assert action.attempt == 1
    assert action.playbook == "recover_high_cpu.yml"
    assert action.command and "--limit" in action.command
    assert action.duration_seconds is not None

    db.refresh(incident)
    assert incident.status == IncidentStatus.RECOVERING
    assert incident.auto_recovery_attempted is True


def test_second_attempt_is_blocked_by_cooldown(db, incident):
    recovery.trigger_recovery(db, incident, triggered_by="test")
    second = recovery.trigger_recovery(db, incident, triggered_by="test")
    assert second.status == RecoveryStatus.BLOCKED
    assert "cooldown" in second.safeguard_reason
    db.refresh(incident)
    assert incident.status == IncidentStatus.RECOVERING


def test_blocked_attempts_are_persisted_for_audit(db, incident):
    for _ in range(2):
        recovery.trigger_recovery(db, incident, triggered_by="test")
    assert db.query(RecoveryAction).count() == 2
    blocked = db.query(RecoveryAction).filter_by(status=RecoveryStatus.BLOCKED).count()
    assert blocked == 1


def test_force_bypasses_cooldown(db, incident):
    recovery.trigger_recovery(db, incident, triggered_by="test")
    forced = recovery.trigger_recovery(db, incident, triggered_by="operator", force=True)
    assert forced.status == RecoveryStatus.SIMULATED
    assert forced.attempt == 2


def test_running_action_prevents_double_dispatch(db, incident):
    running = RecoveryAction(
        incident_id=incident.id,
        host_id=incident.host_id,
        action_type="cpu",
        playbook="recover_high_cpu.yml",
        status=RecoveryStatus.RUNNING,
        triggered_by="test",
        dry_run=True,
    )
    db.add(running)
    db.commit()

    action = recovery.trigger_recovery(db, incident, triggered_by="test")
    assert action.status == RecoveryStatus.BLOCKED
    assert "already running" in action.safeguard_reason


def test_incident_without_host_uses_health_check_path(db):
    incident = Incident(
        reference="INC-NOHOST",
        title="Unassigned incident",
        category="unknown",
        severity="warning",
        status=IncidentStatus.OPEN,
        host_id=None,
        fingerprint="nohost",
        recommended_playbook=None,
    )
    db.add(incident)
    db.commit()
    action = recovery.trigger_recovery(db, incident, triggered_by="test")
    assert action.playbook == recovery.DEFAULT_PLAYBOOK


def test_recovery_history_is_ordered(db, incident):
    recovery.trigger_recovery(db, incident, triggered_by="test")
    recovery.trigger_recovery(db, incident, triggered_by="test")
    history = recovery.recoveries_for_incident(db, incident.id)
    assert [action.id for action in history] == sorted(action.id for action in history)
    assert len(history) == 2
