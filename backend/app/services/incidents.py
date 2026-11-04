"""Incident lifecycle: dedupe, open, acknowledge, resolve, alert ingestion."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Alert,
    AlertStatus,
    Host,
    HostStatus,
    Incident,
    IncidentStatus,
    RecoveryStatus,
)
from ..schemas import AlertmanagerWebhook, WebhookResult
from . import classifier, recovery as recovery_service
from .events import log_event

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:40]


def next_reference(db: Session) -> str:
    total = db.execute(select(func.count(Incident.id))).scalar_one() or 0
    candidate = int(total) + 1
    while True:
        reference = f"INC-{candidate:05d}"
        exists = db.execute(select(Incident.id).where(Incident.reference == reference)).first()
        if not exists:
            return reference
        candidate += 1


def resolve_host(db: Session, identifier: str | None, *, create: bool = True) -> Host | None:
    """Match a host by hostname, instance ``host:port`` or IP address."""
    if not identifier:
        return None
    ident = identifier.strip()
    host = db.execute(select(Host).where(Host.hostname == ident)).scalars().first()
    if host is None:
        host = db.execute(select(Host).where(Host.instance == ident)).scalars().first()
    if host is None:
        host = db.execute(select(Host).where(Host.ip_address == ident)).scalars().first()
    if host is None:
        bare = ident.split(":")[0]
        if bare != ident:
            host = db.execute(select(Host).where(Host.hostname == bare)).scalars().first()
        if host is None:
            host = db.execute(select(Host).where(Host.instance.like(f"{bare}:%"))).scalars().first()
    if host is None and create:
        hostname = ident.split(":")[0]
        host = Host(
            hostname=hostname,
            instance=ident if ":" in ident else None,
            ip_address=hostname if hostname.replace(".", "").isdigit() else None,
            status=HostStatus.UNKNOWN,
            labels={"discovered": "auto"},
        )
        db.add(host)
        db.commit()
        db.refresh(host)
        log_event(
            db,
            f"Discovered host {host.hostname} from monitoring data",
            category="discovery",
            actor="detector",
            host_id=host.id,
        )
    return host


def find_active_incident(db: Session, fp: str, host_id: int | None) -> Incident | None:
    stmt = (
        select(Incident)
        .where(
            Incident.fingerprint == fp,
            Incident.host_id == host_id,
            Incident.status.in_(IncidentStatus.ACTIVE),
        )
        .order_by(Incident.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


@dataclass(slots=True)
class IncidentResult:
    incident: Incident
    created: bool


def open_incident(
    db: Session,
    *,
    host: Host | None,
    classification: classifier.Classification,
    fp: str,
    metric: str | None = None,
    observed_value: float | None = None,
    threshold_value: float | None = None,
    details: dict | None = None,
    title: str | None = None,
) -> IncidentResult:
    """Create an incident, or refresh the existing active one with the same fingerprint."""
    existing = find_active_incident(db, fp, host.id if host else None)
    if existing is not None:
        existing.observed_value = observed_value
        existing.details = {**(existing.details or {}), **(details or {}), "last_seen": _utcnow().isoformat()}
        if classifier.Severity.RANK.get(classification.severity, 1) > classifier.Severity.RANK.get(existing.severity, 1):
            existing.severity = classification.severity
        db.commit()
        db.refresh(existing)
        return IncidentResult(incident=existing, created=False)

    incident = Incident(
        reference=next_reference(db),
        title=(title or classification.title)[:255],
        description=None,
        category=classification.category,
        severity=classification.severity,
        status=IncidentStatus.OPEN,
        host_id=host.id if host else None,
        fingerprint=fp,
        metric=metric,
        observed_value=observed_value,
        threshold_value=threshold_value,
        classification_reason=classification.reason,
        recommended_playbook=classification.playbook,
        root_cause=classifier.root_cause_hint(classification.category, {"playbook": classification.playbook}),
        detected_at=_utcnow(),
        details={
            **(details or {}),
            "runbook": classification.runbook,
            "tags": classification.tags,
            "severity_reason": classification.reason,
        },
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    log_event(
        db,
        f"Incident {incident.reference} opened: {incident.title} [{incident.severity}]",
        level="warning" if incident.severity != "critical" else "error",
        category="incident",
        actor="detector",
        host_id=incident.host_id,
        incident_id=incident.id,
        context={"category": incident.category, "fingerprint": fp},
    )
    return IncidentResult(incident=incident, created=True)


def acknowledge(db: Session, incident: Incident, actor: str = "operator") -> Incident:
    if incident.status == IncidentStatus.OPEN:
        incident.status = IncidentStatus.ACKNOWLEDGED
        incident.acknowledged_at = _utcnow()
        db.commit()
        db.refresh(incident)
        log_event(
            db,
            f"Incident {incident.reference} acknowledged by {actor}",
            category="incident",
            actor=actor,
            host_id=incident.host_id,
            incident_id=incident.id,
        )
    return incident


def resolve_incident(db: Session, incident: Incident, *, reason: str, actor: str = "detector") -> Incident:
    if incident.status == IncidentStatus.RESOLVED:
        return incident
    resolved_at = _utcnow()
    detected = incident.detected_at
    if detected is not None and detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)
    incident.status = IncidentStatus.RESOLVED
    incident.resolved_at = resolved_at
    if detected is not None:
        incident.mttr_seconds = max(0.0, (resolved_at - detected).total_seconds())
    if reason and not incident.root_cause:
        incident.root_cause = reason
    incident.details = {**(incident.details or {}), "resolution": reason}
    db.commit()
    db.refresh(incident)
    log_event(
        db,
        f"Incident {incident.reference} resolved: {reason}",
        category="incident",
        actor=actor,
        host_id=incident.host_id,
        incident_id=incident.id,
        context={"mttr_seconds": incident.mttr_seconds},
    )
    return incident


def latest_recovery_status(db: Session, incident_id: int) -> str | None:
    stmt = (
        select(recovery_service.RecoveryAction.status)
        .where(recovery_service.RecoveryAction.incident_id == incident_id)
        .order_by(recovery_service.RecoveryAction.id.desc())
        .limit(1)
    )
    row = db.execute(stmt).first()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Alertmanager ingestion
# --------------------------------------------------------------------------- #
def _alert_fingerprint(labels: dict[str, str], host: Host | None, provided: str | None) -> str:
    """Canonical fingerprint so both detection paths converge on one incident.

    When the alert carries a ``metric`` label (the shipped rules always do) the
    fingerprint matches the detector's ``threshold|host|metric`` key, so an
    alert and a threshold evaluation for the same condition refresh a single
    incident instead of raising duplicates.
    """
    if provided:
        return provided
    metric = labels.get("metric")
    if host is not None and metric:
        return fingerprint("threshold", host.hostname, metric)
    keys = ("alertname", "instance", "severity", "job")
    return fingerprint(*[f"{key}={labels.get(key, '')}" for key in keys])


def ingest_alert(db: Session, payload_alert, *, source: str = "alertmanager") -> IncidentResult | None:
    labels = payload_alert.labels or {}
    annotations = payload_alert.annotations or {}
    status = (payload_alert.status or "firing").lower()
    alertname = labels.get("alertname", "UnknownAlert")
    instance = labels.get("instance") or labels.get("node") or labels.get("hostname")

    host = resolve_host(db, instance)
    fp = _alert_fingerprint(labels, host, payload_alert.fingerprint)

    if status == "resolved":
        # Close any earlier firing rows for this fingerprint so the alert feed
        # and the ship_alerts_firing gauge reflect the current state.
        db.execute(
            update(Alert)
            .where(Alert.fingerprint == fp, Alert.status == AlertStatus.FIRING)
            .values(status=AlertStatus.RESOLVED, ends_at=_utcnow())
        )
        db.commit()

    alert = Alert(
        fingerprint=fp,
        alertname=alertname,
        severity=labels.get("severity", "warning"),
        status=AlertStatus.RESOLVED if status == "resolved" else AlertStatus.FIRING,
        host_id=host.id if host else None,
        instance=instance,
        summary=annotations.get("summary"),
        description=annotations.get("description"),
        labels=labels,
        annotations=annotations,
        starts_at=payload_alert.startsAt or _utcnow(),
        ends_at=payload_alert.endsAt if status == "resolved" else None,
        source=source,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    if status == "resolved":
        incident = find_active_incident(db, fp, host.id if host else None)
        if incident is not None:
            resolve_incident(
                db,
                incident,
                reason=f"Alertmanager resolved {alertname}",
                actor="alertmanager",
            )
            alert.incident_id = incident.id
            db.commit()
            return IncidentResult(incident=incident, created=False)
        return None

    classification = classifier.classify_alert(labels, annotations)
    result = open_incident(
        db,
        host=host,
        classification=classification,
        fp=fp,
        metric=labels.get("metric"),
        observed_value=_to_float(annotations.get("value") or labels.get("value")),  # rendered by Alertmanager
        threshold_value=_to_float(labels.get("threshold")),
        details={"alertname": alertname, "labels": labels, "annotations": annotations},
    )
    alert.incident_id = result.incident.id
    db.commit()

    if result.created:
        maybe_auto_recover(db, result.incident, reason=f"alert {alertname}")
    return result


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def maybe_auto_recover(
    db: Session,
    incident: Incident,
    *,
    reason: str = "",
    playbook: str | None = None,
) -> recovery_service.RecoveryAction | None:
    """Trigger auto-recovery when the policy allows it. Returns the action row."""
    threshold = None
    if incident.metric:
        from ..models import Threshold

        threshold = db.execute(select(Threshold).where(Threshold.metric == incident.metric)).scalars().first()
    auto_enabled = True if threshold is None else threshold.auto_recovery
    if not auto_enabled:
        log_event(
            db,
            f"Auto-recovery disabled by policy for metric {incident.metric}",
            category="recovery",
            actor="policy",
            host_id=incident.host_id,
            incident_id=incident.id,
        )
        return None
    return recovery_service.trigger_recovery(
        db,
        incident,
        playbook=playbook or incident.recommended_playbook,
        triggered_by="auto",
    )


def ingest_webhook(db: Session, payload: AlertmanagerWebhook) -> WebhookResult:
    opened = resolved = triggered = blocked = 0
    for alert in payload.alerts:
        result = ingest_alert(db, alert)
        if result is None:
            continue
        action = latest_recovery_status(db, result.incident.id)
        if result.created:
            opened += 1
        elif alert.status.lower() == "resolved":
            resolved += 1
        if action == RecoveryStatus.SIMULATED or action == RecoveryStatus.SUCCESS or action == RecoveryStatus.RUNNING:
            triggered += 1
        elif action == RecoveryStatus.BLOCKED:
            blocked += 1

    log_event(
        db,
        f"Alertmanager webhook processed: {len(payload.alerts)} alerts, {opened} incidents opened",
        category="alertmanager",
        actor="webhook",
        context={"opened": opened, "resolved": resolved, "triggered": triggered, "blocked": blocked},
    )
    return WebhookResult(
        received=len(payload.alerts),
        incidents_opened=opened,
        incidents_resolved=resolved,
        recoveries_triggered=triggered,
        blocked=blocked,
        message=f"processed {len(payload.alerts)} alerts",
    )

