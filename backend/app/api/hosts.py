"""Host inventory and metric history endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Host, Incident, IncidentStatus, MetricSample
from ..schemas import EventLogRead, HostCreate, HostDetail, HostRead, HostUpdate, MetricPoint, MetricSeries
from ..services import events as event_service

router = APIRouter(prefix="/hosts", tags=["hosts"])

SUPPORTED_METRICS = ("cpu", "memory", "disk", "network", "availability")
METRIC_UNITS = {"cpu": "percent", "memory": "percent", "disk": "percent", "network": "bps", "availability": "ratio"}


def _latest_metrics(db: Session, host_id: int) -> dict[str, float]:
    latest: dict[str, float] = {}
    for metric in SUPPORTED_METRICS:
        value = db.execute(
            select(MetricSample.value)
            .where(MetricSample.host_id == host_id, MetricSample.metric_name == metric)
            .order_by(MetricSample.recorded_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if value is not None:
            latest[metric] = round(float(value), 2)
    return latest


def _open_count(db: Session, host_id: int) -> int:
    return int(
        db.execute(
            select(func.count(Incident.id)).where(
                Incident.host_id == host_id, Incident.status.in_(IncidentStatus.ACTIVE)
            )
        ).scalar_one()
        or 0
    )


def to_detail(db: Session, host: Host) -> HostDetail:
    base = HostRead.model_validate(host)
    return HostDetail(
        **base.model_dump(),
        latest_metrics=_latest_metrics(db, host.id),
        open_incidents=_open_count(db, host.id),
    )


@router.get("", response_model=list[HostDetail])
def list_hosts(
    status: str | None = Query(default=None),
    environment: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Host).order_by(Host.hostname)
    if status:
        stmt = stmt.where(Host.status == status)
    if environment:
        stmt = stmt.where(Host.environment == environment)
    return [to_detail(db, host) for host in db.execute(stmt).scalars()]


@router.post("", response_model=HostDetail, status_code=201)
def create_host(payload: HostCreate, db: Session = Depends(get_db)):
    exists = db.execute(select(Host).where(Host.hostname == payload.hostname)).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail=f"host {payload.hostname} already exists")
    host = Host(**payload.model_dump())
    db.add(host)
    db.commit()
    db.refresh(host)
    event_service.log_event(
        db,
        f"Host {host.hostname} registered via API",
        category="host",
        actor="operator",
        host_id=host.id,
    )
    return to_detail(db, host)


@router.get("/{host_id}", response_model=HostDetail)
def get_host(host_id: int, db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    return to_detail(db, host)


@router.patch("/{host_id}", response_model=HostDetail)
def update_host(host_id: int, payload: HostUpdate, db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(host, key, value)
    db.commit()
    db.refresh(host)
    if changes:
        event_service.log_event(
            db,
            f"Host {host.hostname} updated: {', '.join(sorted(changes))}",
            category="host",
            actor="operator",
            host_id=host.id,
            context=changes,
        )
    return to_detail(db, host)


@router.delete("/{host_id}", status_code=204)
def delete_host(host_id: int, db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    db.delete(host)
    db.commit()
    return None


@router.get("/{host_id}/metrics", response_model=list[MetricSeries])
def host_metrics(
    host_id: int,
    minutes: int = Query(default=60, ge=5, le=1440),
    db: Session = Depends(get_db),
):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    stmt = (
        select(MetricSample)
        .where(MetricSample.host_id == host_id, MetricSample.recorded_at >= since)
        .order_by(MetricSample.recorded_at.asc())
    )
    buckets: dict[str, list[MetricPoint]] = {metric: [] for metric in SUPPORTED_METRICS}
    for sample in db.execute(stmt).scalars():
        if sample.metric_name in buckets:
            buckets[sample.metric_name].append(
                MetricPoint(timestamp=sample.recorded_at, value=round(float(sample.value), 3))
            )
    return [
        MetricSeries(metric=metric, unit=METRIC_UNITS.get(metric, "percent"), points=points)
        for metric, points in buckets.items()
    ]


@router.get("/{host_id}/events", response_model=list[EventLogRead])
def host_events(host_id: int, limit: int = Query(default=30, ge=1, le=200), db: Session = Depends(get_db)):
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    return list(
        db.execute(
            select(event_service.EventLog)
            .where(event_service.EventLog.host_id == host_id)
            .order_by(event_service.EventLog.id.desc())
            .limit(limit)
        ).scalars()
    )
