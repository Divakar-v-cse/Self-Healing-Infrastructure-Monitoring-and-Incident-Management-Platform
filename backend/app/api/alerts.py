"""Alert feed plus the Alertmanager webhook receiver."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Alert, AlertStatus, RecoveryAction, RecoveryStatus
from ..schemas import AlertRead, AlertmanagerWebhook, RecoveryActionRead, WebhookResult
from ..services import incidents as incident_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=list[AlertRead])
def list_alerts(
    status: str | None = Query(default=None),
    severity: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(Alert).order_by(Alert.received_at.desc(), Alert.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(Alert.status == status)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    return list(db.execute(stmt).scalars())


@router.get("/alerts/summary")
def alert_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Alert.severity, Alert.status, func.count(Alert.id)).group_by(Alert.severity, Alert.status)
    ).all()
    summary = {"firing": 0, "resolved": 0, "critical": 0, "warning": 0}
    for severity, status, count in rows:
        summary[status] = summary.get(status, 0) + int(count)
        if status == AlertStatus.FIRING:
            summary[severity] = summary.get(severity, 0) + int(count)
    return summary


@router.post("/webhooks/alertmanager", response_model=WebhookResult)
def alertmanager_webhook(payload: AlertmanagerWebhook, db: Session = Depends(get_db)):
    """Alertmanager receiver. Critical incidents are classified and auto-recovered."""
    return incident_service.ingest_webhook(db, payload)


@router.get("/recoveries", response_model=list[RecoveryActionRead])
def list_recoveries(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(RecoveryAction).order_by(RecoveryAction.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(RecoveryAction.status == status)
    return [RecoveryActionRead.model_validate(action) for action in db.execute(stmt).scalars()]


@router.get("/recoveries/stats")
def recovery_stats(db: Session = Depends(get_db)):
    rows = db.execute(
        select(RecoveryAction.status, func.count(RecoveryAction.id)).group_by(RecoveryAction.status)
    ).all()
    by_status = {status: int(count) for status, count in rows}
    executed = sum(
        by_status.get(status, 0)
        for status in (RecoveryStatus.SUCCESS, RecoveryStatus.SIMULATED, RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT)
    )
    successful = by_status.get(RecoveryStatus.SUCCESS, 0) + by_status.get(RecoveryStatus.SIMULATED, 0)
    return {
        "by_status": by_status,
        "total": sum(by_status.values()),
        "executed": executed,
        "success_rate": round(100.0 * successful / executed, 2) if executed else 0.0,
        "blocked": by_status.get(RecoveryStatus.BLOCKED, 0),
    }
