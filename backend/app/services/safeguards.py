"""Guard rails that prevent recovery storms and repeated recovery loops.

Every automated or manual recovery attempt must pass :func:`evaluate`. The
function is pure with respect to its inputs except for the DB reads it
performs, which makes the rules straightforward to test and reason about.

Rules (evaluated in order, first failure wins):

1. ``global_switch``      - recovery globally disabled or host in maintenance
2. ``host_opt_out``       - per-host auto recovery disabled
3. ``ansible_disabled``   - ansible execution disabled by configuration
4. ``concurrency_limit``  - too many recoveries running right now
5. ``per_incident_limit`` - max attempts for the same incident exceeded
6. ``window_limit``       - max attempts per host within the sliding window
7. ``cooldown``           - same action on the same host too soon
8. ``circuit_breaker``    - repeated failures tripped the breaker for this host
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Host, Incident, IncidentStatus, RecoveryAction, RecoveryStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SafeguardDecision:
    allowed: bool
    rule: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "rule": self.rule, "reason": self.reason}


def _allow() -> SafeguardDecision:
    return SafeguardDecision(allowed=True)


def _block(rule: str, reason: str) -> SafeguardDecision:
    logger.info("recovery blocked by safeguard %s: %s", rule, reason)
    return SafeguardDecision(allowed=False, rule=rule, reason=reason)


def count_attempts_for_incident(db: Session, incident_id: int) -> int:
    stmt = select(func.count(RecoveryAction.id)).where(
        RecoveryAction.incident_id == incident_id,
        RecoveryAction.status.notin_([RecoveryStatus.BLOCKED]),
    )
    return int(db.execute(stmt).scalar_one() or 0)


def count_attempts_in_window(db: Session, host_id: int, action_type: str, window_minutes: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    stmt = select(func.count(RecoveryAction.id)).where(
        RecoveryAction.host_id == host_id,
        RecoveryAction.action_type == action_type,
        RecoveryAction.created_at >= since,
        RecoveryAction.status.notin_([RecoveryStatus.BLOCKED]),
    )
    return int(db.execute(stmt).scalar_one() or 0)


def consecutive_failures(db: Session, host_id: int, action_type: str) -> int:
    stmt = (
        select(RecoveryAction)
        .where(
            RecoveryAction.host_id == host_id,
            RecoveryAction.action_type == action_type,
            RecoveryAction.status.in_([RecoveryStatus.SUCCESS, RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT]),
        )
        .order_by(RecoveryAction.id.desc())
        .limit(10)
    )
    streak = 0
    for action in db.execute(stmt).scalars():
        if action.status in (RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT):
            streak += 1
        else:
            break
    return streak


def last_action(db: Session, host_id: int, action_type: str) -> RecoveryAction | None:
    stmt = (
        select(RecoveryAction)
        .where(
            RecoveryAction.host_id == host_id,
            RecoveryAction.action_type == action_type,
            RecoveryAction.status != RecoveryStatus.BLOCKED,
        )
        .order_by(RecoveryAction.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate(
    db: Session,
    *,
    host: Host | None,
    incident: Incident | None,
    action_type: str,
    forced: bool = False,
) -> SafeguardDecision:
    """Decide whether a recovery action may proceed."""
    if forced:
        # An operator explicitly overriding is still bound by the global kill
        # switch: we never allow recovery while the platform is disabled.
        if not settings.recovery_enabled:
            return _block("global_switch", "recovery is globally disabled (SHIP_RECOVERY_ENABLED=false)")
        return _allow()

    if not settings.recovery_enabled:
        return _block("global_switch", "recovery is globally disabled (SHIP_RECOVERY_ENABLED=false)")

    if host is not None:
        if host.maintenance_mode:
            return _block("maintenance_mode", f"host {host.hostname} is in maintenance mode")
        if not host.auto_recovery_enabled:
            return _block("host_opt_out", f"automatic recovery is disabled for host {host.hostname}")

    if not settings.ansible_enabled:
        return _block("ansible_disabled", "ansible execution is disabled (SHIP_ANSIBLE_ENABLED=false)")

    running = db.execute(
        select(func.count(RecoveryAction.id)).where(RecoveryAction.status == RecoveryStatus.RUNNING)
    ).scalar_one()
    if int(running or 0) >= settings.max_concurrent_recoveries:
        return _block(
            "concurrency_limit",
            f"{running} recoveries already running (limit {settings.max_concurrent_recoveries})",
        )

    if incident is not None:
        if incident.status == IncidentStatus.RESOLVED:
            return _block("incident_resolved", f"incident {incident.reference} is already resolved")
        attempts = count_attempts_for_incident(db, incident.id)
        if attempts >= settings.max_attempts_per_incident:
            return _block(
                "per_incident_limit",
                f"incident {incident.reference} already has {attempts} attempts "
                f"(limit {settings.max_attempts_per_incident}); escalating to operator",
            )

    if host is not None:
        in_window = count_attempts_in_window(db, host.id, action_type, settings.attempt_window_minutes)
        if in_window >= settings.max_attempts_per_window:
            return _block(
                "window_limit",
                f"{in_window} '{action_type}' recoveries for {host.hostname} in the last "
                f"{settings.attempt_window_minutes}m (limit {settings.max_attempts_per_window})",
            )

        streak = consecutive_failures(db, host.id, action_type)
        if streak >= settings.circuit_breaker_threshold:
            previous = last_action(db, host.id, action_type)
            finished = _as_utc(previous.finished_at or previous.created_at) if previous else None
            if finished is not None:
                reopen_at = finished + timedelta(seconds=settings.circuit_breaker_cooldown_seconds)
                if datetime.now(timezone.utc) < reopen_at:
                    return _block(
                        "circuit_breaker",
                        f"{streak} consecutive failures for '{action_type}' on {host.hostname}; "
                        f"breaker open until {reopen_at.isoformat(timespec='seconds')}",
                    )

        previous = last_action(db, host.id, action_type)
        if previous is not None:
            last_at = _as_utc(previous.created_at)
            if last_at is not None:
                next_allowed = last_at + timedelta(seconds=settings.recovery_cooldown_seconds)
                if datetime.now(timezone.utc) < next_allowed:
                    remaining = int((next_allowed - datetime.now(timezone.utc)).total_seconds())
                    return _block(
                        "cooldown",
                        f"cooldown active for '{action_type}' on {host.hostname}; retry in {remaining}s",
                    )

    return _allow()


def snapshot() -> dict[str, object]:
    """Current safeguard configuration, exposed through the API/UI."""
    return {
        "recovery_enabled": settings.recovery_enabled,
        "ansible_enabled": settings.ansible_enabled,
        "ansible_dry_run": settings.ansible_dry_run,
        "max_attempts_per_incident": settings.max_attempts_per_incident,
        "max_attempts_per_window": settings.max_attempts_per_window,
        "attempt_window_minutes": settings.attempt_window_minutes,
        "recovery_cooldown_seconds": settings.recovery_cooldown_seconds,
        "circuit_breaker_threshold": settings.circuit_breaker_threshold,
        "circuit_breaker_cooldown_seconds": settings.circuit_breaker_cooldown_seconds,
        "max_concurrent_recoveries": settings.max_concurrent_recoveries,
        "incident_autoresolve_minutes": settings.incident_autoresolve_minutes,
        "environment": settings.environment,
        "playbook_dir": str(settings.playbook_dir),
        "inventory_file": str(settings.inventory_file),
    }
