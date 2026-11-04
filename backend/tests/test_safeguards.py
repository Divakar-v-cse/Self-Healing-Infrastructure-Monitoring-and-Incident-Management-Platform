"""Loop-prevention safeguard tests."""

from __future__ import annotations

from app.config import settings
from app.models import RecoveryStatus
from app.services import safeguards
from tests.conftest import make_action


def test_recovery_is_allowed_when_all_clear(db, host, incident):
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is True
    assert decision.rule is None


def test_cooldown_blocks_rapid_repeat(db, host, incident):
    make_action(db, host_id=host.id, action_type="cpu", created_minutes_ago=0.5, finished_minutes_ago=0.5)
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "cooldown"
    assert "retry in" in decision.reason


def test_per_incident_attempt_limit(db, host, incident):
    for attempt in range(3):
        make_action(db, host_id=host.id, incident_id=incident.id, action_type="cpu", attempt=attempt + 1)
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "per_incident_limit"
    assert "operator" in decision.reason


def test_sliding_window_limit(db, host, incident):
    for _ in range(5):
        make_action(db, host_id=host.id, action_type="cpu", created_minutes_ago=10)
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "window_limit"


def test_circuit_breaker_after_repeated_failures(db, host, incident):
    for _ in range(3):
        make_action(
            db,
            host_id=host.id,
            action_type="cpu",
            status=RecoveryStatus.FAILED,
            created_minutes_ago=10,
            finished_minutes_ago=2,
        )
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "circuit_breaker"
    assert "consecutive failures" in decision.reason


def test_circuit_breaker_closes_after_cooldown(db, host, incident):
    for _ in range(3):
        make_action(
            db,
            host_id=host.id,
            action_type="cpu",
            status=RecoveryStatus.FAILED,
            created_minutes_ago=120,
            finished_minutes_ago=90,
        )
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is True


def test_single_failure_does_not_trip_breaker(db, host, incident):
    make_action(
        db,
        host_id=host.id,
        action_type="cpu",
        status=RecoveryStatus.FAILED,
        created_minutes_ago=30,
        finished_minutes_ago=25,
    )
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is True


def test_maintenance_mode_blocks_recovery(db, host, incident):
    host.maintenance_mode = True
    db.commit()
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "maintenance_mode"


def test_host_opt_out_blocks_recovery(db, host, incident):
    host.auto_recovery_enabled = False
    db.commit()
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "host_opt_out"


def test_global_kill_switch_blocks_even_when_forced(db, host, incident):
    settings.recovery_enabled = False
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu", forced=True)
    assert decision.allowed is False
    assert decision.rule == "global_switch"


def test_force_overrides_cooldown(db, host, incident):
    make_action(db, host_id=host.id, action_type="cpu", created_minutes_ago=0.5, finished_minutes_ago=0.5)
    assert safeguards.evaluate(db, host=host, incident=incident, action_type="cpu").allowed is False
    assert safeguards.evaluate(db, host=host, incident=incident, action_type="cpu", forced=True).allowed is True


def test_concurrency_limit(db, incident):
    for _ in range(2):
        make_action(db, action_type="cpu", status=RecoveryStatus.RUNNING)
    decision = safeguards.evaluate(db, host=None, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "concurrency_limit"


def test_ansible_disabled_blocks_recovery(db, host, incident):
    settings.ansible_enabled = False
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "ansible_disabled"


def test_blocked_actions_do_not_count_towards_limits(db, host, incident):
    for _ in range(4):
        make_action(db, host_id=host.id, action_type="cpu", status=RecoveryStatus.BLOCKED)
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is True


def test_resolved_incident_cannot_be_recovered(db, host, incident):
    incident.status = "resolved"
    db.commit()
    decision = safeguards.evaluate(db, host=host, incident=incident, action_type="cpu")
    assert decision.allowed is False
    assert decision.rule == "incident_resolved"


def test_snapshot_exposes_configuration():
    snapshot = safeguards.snapshot()
    for key in (
        "recovery_enabled",
        "max_attempts_per_incident",
        "recovery_cooldown_seconds",
        "circuit_breaker_threshold",
        "max_concurrent_recoveries",
    ):
        assert key in snapshot

