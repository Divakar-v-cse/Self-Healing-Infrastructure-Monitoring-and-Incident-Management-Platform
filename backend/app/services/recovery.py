"""Ansible-driven automated recovery.

Design notes
------------
* Playbooks are addressed by file name and executed with ``--limit <hostname>``
  against the configured inventory, so one playbook serves every host.
* ``ansible_dry_run`` (default) never invokes Ansible: the action is recorded
  with status ``simulated`` and the exact command that *would* run. This makes
  the platform safe to exercise locally and keeps the UI honest about what was
  actually executed.
* Safeguards are evaluated before a row is created as ``running``; blocked
  attempts are persisted as ``blocked`` so repeated loops stay visible.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Host,
    Incident,
    IncidentStatus,
    RecoveryAction,
    RecoveryStatus,
)
from . import safeguards
from .events import log_event

logger = logging.getLogger(__name__)

DEFAULT_PLAYBOOK = "health_check.yml"


@dataclass(slots=True)
class AnsibleRunResult:
    status: str
    output: str
    duration_seconds: float
    command: str


class AnsibleRunner:
    """Thin, mockable wrapper around ``ansible-playbook``."""

    def __init__(
        self,
        playbook_dir: Path | None = None,
        inventory: Path | None = None,
        binary: str | None = None,
        dry_run: bool | None = None,
        timeout: int | None = None,
    ) -> None:
        self.playbook_dir = Path(playbook_dir or settings.playbook_dir)
        self.inventory = Path(inventory or settings.inventory_file)
        self.binary = binary or settings.ansible_binary
        self.dry_run = settings.ansible_dry_run if dry_run is None else dry_run
        self.timeout = timeout or settings.ansible_timeout_seconds

    # ------------------------------------------------------------------ #
    def resolve_playbook(self, playbook: str) -> Path:
        candidate = (self.playbook_dir / playbook).resolve()
        base = self.playbook_dir.resolve()
        if base not in candidate.parents and candidate != base:
            raise ValueError(f"playbook path escapes the playbook directory: {playbook}")
        return candidate

    def build_command(self, playbook_path: Path, hostname: str) -> list[str]:
        command = [
            self.binary,
            "-i",
            str(self.inventory),
            str(playbook_path),
            "--limit",
            hostname,
            "-e",
            f"target_host={hostname}",
            "--extra-vars",
            "recovery_mode=automated",
        ]
        extra = (settings.ansible_extra_args or "").strip()
        if extra:
            command.extend(shlex.split(extra))
        return command

    # ------------------------------------------------------------------ #
    def run(self, playbook: str, hostname: str) -> AnsibleRunResult:
        started = time.perf_counter()
        try:
            playbook_path = self.resolve_playbook(playbook)
        except ValueError as exc:
            return AnsibleRunResult(RecoveryStatus.FAILED, str(exc), 0.0, "")

        if not playbook_path.exists():
            return AnsibleRunResult(
                RecoveryStatus.FAILED,
                f"playbook not found: {playbook_path}",
                time.perf_counter() - started,
                "",
            )

        command = self.build_command(playbook_path, hostname)
        command_str = " ".join(shlex.quote(part) for part in command)

        if self.dry_run:
            output = (
                "DRY-RUN: ansible execution skipped (SHIP_ANSIBLE_DRY_RUN=true)\n"
                f"would execute: {command_str}\n"
                f"target host: {hostname}\n"
                f"playbook: {playbook_path.name}\n"
                "set SHIP_ANSIBLE_DRY_RUN=false to execute for real"
            )
            return AnsibleRunResult(RecoveryStatus.SIMULATED, output, time.perf_counter() - started, command_str)

        if not self.inventory.exists():
            return AnsibleRunResult(
                RecoveryStatus.FAILED,
                f"inventory not found: {self.inventory}",
                time.perf_counter() - started,
                command_str,
            )

        env = {
            "ANSIBLE_HOST_KEY_CHECKING": "False",
            "ANSIBLE_RETRY_FILES_ENABLED": "False",
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except FileNotFoundError:
            return AnsibleRunResult(
                RecoveryStatus.FAILED,
                f"ansible binary not found: {self.binary}",
                time.perf_counter() - started,
                command_str,
            )
        except subprocess.TimeoutExpired:
            return AnsibleRunResult(
                RecoveryStatus.TIMEOUT,
                f"ansible-playbook timed out after {self.timeout}s",
                time.perf_counter() - started,
                command_str,
            )

        duration = time.perf_counter() - started
        output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
        status = RecoveryStatus.SUCCESS if completed.returncode == 0 else RecoveryStatus.FAILED
        return AnsibleRunResult(status, output.strip()[:20000], duration, command_str)


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _running_action(db: Session, incident_id: int | None) -> RecoveryAction | None:
    if incident_id is None:
        return None
    stmt = (
        select(RecoveryAction)
        .where(
            RecoveryAction.incident_id == incident_id,
            RecoveryAction.status.in_([RecoveryStatus.PENDING, RecoveryStatus.RUNNING]),
        )
        .order_by(RecoveryAction.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def trigger_recovery(
    db: Session,
    incident: Incident,
    *,
    playbook: str | None = None,
    triggered_by: str = "auto",
    force: bool = False,
    runner: AnsibleRunner | None = None,
) -> RecoveryAction:
    """Attempt recovery for ``incident``. Always returns a persisted action row."""
    host: Host | None = db.get(Host, incident.host_id) if incident.host_id else None
    playbook = playbook or incident.recommended_playbook or DEFAULT_PLAYBOOK
    action_type = incident.category

    running = _running_action(db, incident.id)
    if running is not None:
        action = RecoveryAction(
            incident_id=incident.id,
            host_id=incident.host_id,
            action_type=action_type,
            playbook=playbook,
            status=RecoveryStatus.BLOCKED,
            attempt=1,
            triggered_by=triggered_by,
            dry_run=settings.ansible_dry_run,
            safeguard_reason=f"recovery already {running.status} for this incident (action #{running.id})",
            finished_at=datetime.now(timezone.utc),
        )
        db.add(action)
        db.commit()
        db.refresh(action)
        return action

    decision = safeguards.evaluate(
        db,
        host=host,
        incident=incident,
        action_type=action_type,
        forced=force,
    )

    attempt = safeguards.count_attempts_for_incident(db, incident.id) + 1
    action = RecoveryAction(
        incident_id=incident.id,
        host_id=incident.host_id,
        action_type=action_type,
        playbook=playbook,
        status=RecoveryStatus.PENDING,
        attempt=attempt,
        triggered_by=triggered_by,
        dry_run=settings.ansible_dry_run,
    )

    if not decision.allowed:
        action.status = RecoveryStatus.BLOCKED
        action.safeguard_reason = decision.reason
        action.finished_at = datetime.now(timezone.utc)
        db.add(action)
        db.commit()
        db.refresh(action)
        log_event(
            db,
            f"Recovery blocked for {incident.reference}: {decision.reason}",
            level="warning",
            category="safeguard",
            actor="safeguards",
            host_id=incident.host_id,
            incident_id=incident.id,
            context={"rule": decision.rule, "action_id": action.id},
        )
        return action

    action.status = RecoveryStatus.RUNNING
    action.started_at = datetime.now(timezone.utc)
    db.add(action)
    incident.auto_recovery_attempted = True
    incident.status = IncidentStatus.RECOVERING
    db.commit()
    db.refresh(action)

    log_event(
        db,
        f"Recovery attempt #{attempt} started for {incident.reference} using {playbook}",
        category="recovery",
        actor=triggered_by,
        host_id=incident.host_id,
        incident_id=incident.id,
        context={"action_id": action.id, "playbook": playbook, "dry_run": action.dry_run},
    )

    runner = runner or AnsibleRunner()
    result = runner.run(playbook, host.hostname if host else "unknown")

    action.status = result.status
    action.output = result.output
    action.duration_seconds = round(result.duration_seconds, 3)
    action.command = result.command
    action.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(action)

    level = "info" if result.status in (RecoveryStatus.SUCCESS, RecoveryStatus.SIMULATED) else "error"
    log_event(
        db,
        f"Recovery attempt #{attempt} for {incident.reference} finished with status {result.status}",
        level=level,
        category="recovery",
        actor=triggered_by,
        host_id=incident.host_id,
        incident_id=incident.id,
        context={
            "action_id": action.id,
            "status": result.status,
            "duration_seconds": action.duration_seconds,
            "dry_run": action.dry_run,
        },
    )
    return action


def recoveries_for_incident(db: Session, incident_id: int) -> list[RecoveryAction]:
    stmt = (
        select(RecoveryAction)
        .where(RecoveryAction.incident_id == incident_id)
        .order_by(RecoveryAction.id.asc())
    )
    return list(db.execute(stmt).scalars())
