"""Incident management and recovery triggering endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Host, Incident, IncidentStatus, RecoveryStatus
from ..schemas import (
    IncidentCreate,
    IncidentDetail,
    IncidentListResponse,
    IncidentRead,
    IncidentUpdate,
    RecoveryActionRead,
    RecoveryRequest,
    RecoveryResult,
)
from ..services import classifier
from ..services import incidents as incident_service
from ..services import recovery as recovery_service
from ..services import safeguards
from ..services.events import log_event

router = APIRouter(prefix="/incidents", tags=["incidents"])


def serialize(db: Session, incident: Incident) -> IncidentRead:
    host = db.get(Host, incident.host_id) if incident.host_id else None
    base = IncidentRead.model_validate(incident)
    return base.model_copy(update={"hostname": host.hostname if host else None})


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    host_id: int | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
):
    conditions = []
    if status == "active":
        conditions.append(Incident.status.in_(IncidentStatus.ACTIVE))
    elif status:
        conditions.append(Incident.status == status)
    if severity:
        conditions.append(Incident.severity == severity)
    if category:
        conditions.append(Incident.category == category)
    if host_id:
        conditions.append(Incident.host_id == host_id)
    if search:
        like = f"%{search}%"
        conditions.append(Incident.title.ilike(like) | Incident.reference.ilike(like))

    total = int(db.execute(select(func.count(Incident.id)).where(*conditions)).scalar_one() or 0)
    stmt = (
        select(Incident)
        .where(*conditions)
        .order_by(Incident.detected_at.desc(), Incident.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [serialize(db, incident) for incident in db.execute(stmt).scalars()]
    return IncidentListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=IncidentDetail, status_code=201)
def create_incident(payload: IncidentCreate, db: Session = Depends(get_db)):
    host = None
    if payload.host_id:
        host = db.get(Host, payload.host_id)
    elif payload.hostname:
        host = incident_service.resolve_host(db, payload.hostname)
    if payload.host_id and host is None:
        raise HTTPException(status_code=404, detail="host not found")

    classification = classifier.Classification(
        category=payload.category,
        severity=payload.severity,
        title=payload.title,
        playbook=classifier.CATEGORY_PLAYBOOKS.get(payload.category, "health_check.yml"),
        reason="manually reported incident",
        runbook=classifier.CATEGORY_RUNBOOK.get(payload.category, []),
        tags=[payload.category, payload.severity, "manual"],
    )
    fp = incident_service.fingerprint("manual", host.hostname if host else "unassigned", payload.title)
    result = incident_service.open_incident(
        db,
        host=host,
        classification=classification,
        fp=fp,
        metric=payload.metric,
        observed_value=payload.observed_value,
        threshold_value=payload.threshold_value,
        details={**payload.details, "reported": True},
        title=payload.title,
    )
    if payload.description:
        result.incident.description = payload.description
        db.commit()
        db.refresh(result.incident)
    if result.created:
        log_event(
            db,
            f"Manual incident {result.incident.reference} reported",
            category="incident",
            actor="operator",
            host_id=result.incident.host_id,
            incident_id=result.incident.id,
        )
    return _detail(db, result.incident.id)


def _detail(db: Session, incident_id: int) -> IncidentDetail:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    base = serialize(db, incident)
    actions = [
        RecoveryActionRead.model_validate(action)
        for action in recovery_service.recoveries_for_incident(db, incident_id)
    ]
    return IncidentDetail(**base.model_dump(), recovery_actions=actions)


@router.get("/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    return _detail(db, incident_id)


@router.patch("/{incident_id}", response_model=IncidentDetail)
def update_incident(incident_id: int, payload: IncidentUpdate, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    changes = payload.model_dump(exclude_unset=True)

    if "status" in changes and changes["status"] is not None:
        target = changes.pop("status")
        if target == IncidentStatus.ACKNOWLEDGED:
            incident_service.acknowledge(db, incident)
        elif target == IncidentStatus.RESOLVED:
            incident_service.resolve_incident(
                db, incident, reason="manually resolved by operator", actor="operator"
            )
        elif target in IncidentStatus.ALL:
            incident.status = target
            db.commit()
        else:
            raise HTTPException(status_code=422, detail=f"invalid status {target}")

    if changes:
        for key, value in changes.items():
            setattr(incident, key, value)
        db.commit()
    db.refresh(incident)
    return _detail(db, incident_id)


@router.post("/{incident_id}/recover", response_model=RecoveryResult)
def recover(
    incident_id: int,
    payload: RecoveryRequest | None = None,
    db: Session = Depends(get_db),
):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    payload = payload or RecoveryRequest()
    host = db.get(Host, incident.host_id) if incident.host_id else None

    decision = safeguards.evaluate(
        db,
        host=host,
        incident=incident,
        action_type=incident.category,
        forced=payload.force,
    )
    action = recovery_service.trigger_recovery(
        db,
        incident,
        playbook=payload.playbook,
        triggered_by="operator",
        force=payload.force,
    )
    allowed = action.status != RecoveryStatus.BLOCKED
    return RecoveryResult(
        allowed=allowed,
        status=action.status,
        reason=action.safeguard_reason if not allowed else (payload.reason or "recovery dispatched"),
        rule=None if allowed else decision.rule,
        action=RecoveryActionRead.model_validate(action),
    )


@router.get("/{incident_id}/recoveries", response_model=list[RecoveryActionRead])
def incident_recoveries(incident_id: int, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return [
        RecoveryActionRead.model_validate(action)
        for action in recovery_service.recoveries_for_incident(db, incident_id)
    ]
