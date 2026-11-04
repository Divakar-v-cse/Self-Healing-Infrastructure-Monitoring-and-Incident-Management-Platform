"""Platform status, configuration, reporting, simulation and self-metrics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import (
    Alert,
    AlertStatus,
    EventLog,
    HealthReport,
    Host,
    Incident,
    IncidentStatus,
    RecoveryAction,
    RecoveryStatus,
    Severity,
    Threshold,
)
from ..schemas import (
    EventLogRead,
    FaultInjection,
    FaultInjectionResult,
    HealthReportRead,
    MessageResponse,
    OverviewResponse,
    SafeguardConfig,
    SimulationStatus,
    ThresholdRead,
    ThresholdUpdate,
)
from ..services import detector as detector_module
from ..services import reports as report_service
from ..services import safeguards
from ..services.events import log_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


# --------------------------------------------------------------------------- #
# liveness / readiness
# --------------------------------------------------------------------------- #
@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    checks = {"database": False, "prometheus": False, "simulator": False}
    try:
        db.execute(select(func.count(Host.id)))
        checks["database"] = True
    except Exception:  # pragma: no cover - defensive
        checks["database"] = False

    try:
        with httpx.Client(timeout=2.0) as client:
            checks["prometheus"] = client.get(f"{settings.prometheus_url}/-/healthy").status_code == 200
    except httpx.HTTPError:
        checks["prometheus"] = False

    try:
        with httpx.Client(timeout=2.0) as client:
            checks["simulator"] = client.get(f"{settings.simulator_url}/healthz").status_code == 200
    except httpx.HTTPError:
        checks["simulator"] = False

    return {"ready": checks["database"], "checks": checks}


# --------------------------------------------------------------------------- #
# dashboard overview
# --------------------------------------------------------------------------- #
@router.get("/overview", response_model=OverviewResponse)
def overview(hours: int = Query(default=24, ge=1, le=720), db: Session = Depends(get_db)):
    return report_service.build_overview(db, hours=hours)


@router.get("/events", response_model=list[EventLogRead])
def list_events(
    limit: int = Query(default=50, ge=1, le=300),
    hours: int = Query(default=24, ge=1, le=720),
    category: str | None = None,
    db: Session = Depends(get_db),
):
    from ..services.events import recent_events

    return [EventLogRead.model_validate(entry) for entry in recent_events(db, limit=limit, hours=hours, category=category)]


# --------------------------------------------------------------------------- #
# thresholds
# --------------------------------------------------------------------------- #
@router.get("/thresholds", response_model=list[ThresholdRead])
def list_thresholds(db: Session = Depends(get_db)):
    return list(db.execute(select(Threshold).order_by(Threshold.id)).scalars())


@router.patch("/thresholds/{metric}", response_model=ThresholdRead)
def update_threshold(metric: str, payload: ThresholdUpdate, db: Session = Depends(get_db)):
    threshold = db.execute(select(Threshold).where(Threshold.metric == metric)).scalars().first()
    if threshold is None:
        raise HTTPException(status_code=404, detail=f"threshold for metric '{metric}' not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(threshold, key, value)
    if threshold.warning > threshold.critical and threshold.comparator in (">=", ">"):
        db.rollback()
        raise HTTPException(status_code=422, detail="warning threshold must be below critical")
    db.commit()
    db.refresh(threshold)
    log_event(
        db,
        f"Threshold for {metric} updated: warning={threshold.warning}, critical={threshold.critical}",
        category="config",
        actor="operator",
        context=changes,
    )
    return threshold


@router.post("/thresholds", response_model=ThresholdRead, status_code=201)
def create_threshold(payload: ThresholdUpdate, metric: str = Query(...), db: Session = Depends(get_db)):
    exists = db.execute(select(Threshold).where(Threshold.metric == metric)).scalars().first()
    if exists:
        raise HTTPException(status_code=409, detail=f"threshold for '{metric}' already exists")
    data = payload.model_dump(exclude_unset=True)
    threshold = Threshold(
        metric=metric,
        display_name=data.get("display_name", metric.title()),
        unit=data.get("unit", "percent"),
        warning=data.get("warning", 80.0),
        critical=data.get("critical", 90.0),
        comparator=data.get("comparator", ">="),
        duration_seconds=data.get("duration_seconds", 300),
        enabled=data.get("enabled", True),
        auto_recovery=data.get("auto_recovery", True),
        category=data.get("category", "unknown"),
    )
    db.add(threshold)
    db.commit()
    db.refresh(threshold)
    return threshold


# --------------------------------------------------------------------------- #
# safeguards / configuration
# --------------------------------------------------------------------------- #
@router.get("/safeguards", response_model=SafeguardConfig)
def get_safeguards():
    return SafeguardConfig(**safeguards.snapshot())


@router.patch("/safeguards", response_model=SafeguardConfig)
def update_safeguards(payload: SafeguardConfig, db: Session = Depends(get_db)):
    mutable = {
        "recovery_enabled",
        "ansible_enabled",
        "ansible_dry_run",
        "max_attempts_per_incident",
        "max_attempts_per_window",
        "attempt_window_minutes",
        "recovery_cooldown_seconds",
        "circuit_breaker_threshold",
        "circuit_breaker_cooldown_seconds",
        "max_concurrent_recoveries",
        "incident_autoresolve_minutes",
    }
    updates = {key: value for key, value in payload.model_dump().items() if key in mutable}
    for key, value in updates.items():
        setattr(settings, key, value)
    log_event(
        db,
        f"Safeguard configuration updated: {', '.join(sorted(updates))}",
        category="config",
        actor="operator",
        context=updates,
    )
    return SafeguardConfig(**safeguards.snapshot())


# --------------------------------------------------------------------------- #
# health reports
# --------------------------------------------------------------------------- #
@router.get("/reports", response_model=list[HealthReportRead])
def list_reports(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    stmt = select(HealthReport).order_by(HealthReport.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


@router.post("/reports", response_model=HealthReportRead, status_code=201)
def create_report(hours: int = Query(default=24, ge=1, le=720), db: Session = Depends(get_db)):
    return report_service.generate_health_report(db, hours=hours)


# --------------------------------------------------------------------------- #
# detection trigger
# --------------------------------------------------------------------------- #
@router.post("/detections/run")
def run_detection(db: Session = Depends(get_db)):
    """Run one evaluation cycle synchronously (used by the UI and by tests)."""
    summary = detector_module.Detector().evaluate_once(db)
    return summary.as_dict()


# --------------------------------------------------------------------------- #
# failure simulation (proxied to the simulator service)
# --------------------------------------------------------------------------- #
@router.get("/simulation", response_model=SimulationStatus)
def simulation_status():
    try:
        with httpx.Client(timeout=5.0) as client:
            hosts = client.get(f"{settings.simulator_url}/sim/hosts")
            hosts.raise_for_status()
            faults = client.get(f"{settings.simulator_url}/sim/faults")
            faults.raise_for_status()
            return SimulationStatus(
                simulator_reachable=True,
                mode="simulator",
                hosts=hosts.json().get("hosts", []),
                active_faults=faults.json().get("faults", []),
            )
    except httpx.HTTPError as exc:
        return SimulationStatus(
            simulator_reachable=False,
            mode="simulator",
            hosts=[],
            active_faults=[],
            detail=f"simulator unreachable at {settings.simulator_url}: {exc}",
        )


@router.post("/simulation/faults", response_model=FaultInjectionResult)
def inject_fault(payload: FaultInjection, db: Session = Depends(get_db)):
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(f"{settings.simulator_url}/sim/faults", json=payload.model_dump())
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"simulator unavailable: {exc}") from exc

    log_event(
        db,
        f"Failure injected on {payload.hostname}: {payload.metric}={payload.value} for {payload.duration_seconds}s",
        level="warning",
        category="simulation",
        actor="operator",
        context=payload.model_dump(),
    )
    return FaultInjectionResult(
        hostname=payload.hostname,
        metric=payload.metric,
        value=payload.value,
        expires_at=data.get("expires_at"),
        active_faults=data.get("faults", []),
    )


@router.delete("/simulation/faults", response_model=MessageResponse)
def clear_faults(hostname: str | None = None, db: Session = Depends(get_db)):
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.delete(f"{settings.simulator_url}/sim/faults", params={"hostname": hostname} if hostname else None)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"simulator unavailable: {exc}") from exc
    log_event(
        db,
        f"Simulated faults cleared{' for ' + hostname if hostname else ''}",
        category="simulation",
        actor="operator",
    )
    return MessageResponse(message="faults cleared", detail=hostname)


# --------------------------------------------------------------------------- #
# Prometheus self-metrics for the platform itself
# --------------------------------------------------------------------------- #
@router.get("/self-metrics", include_in_schema=False)
def self_metrics(db: Session = Depends(get_db)):
    hosts = db.execute(select(Host.status, func.count(Host.id)).group_by(Host.status)).all()
    incidents = db.execute(
        select(Incident.status, Incident.severity, func.count(Incident.id)).group_by(Incident.status, Incident.severity)
    ).all()
    recoveries = db.execute(
        select(RecoveryAction.status, func.count(RecoveryAction.id)).group_by(RecoveryAction.status)
    ).all()
    alerts = db.execute(
        select(func.count(Alert.id)).where(Alert.status == AlertStatus.FIRING)
    ).scalar_one()
    mttr = db.execute(
        select(func.avg(Incident.mttr_seconds)).where(Incident.mttr_seconds.is_not(None))
    ).scalar_one()

    lines: list[str] = [
        "# HELP ship_hosts_total Hosts by current status.",
        "# TYPE ship_hosts_total gauge",
    ]
    for status, count in hosts:
        lines.append(f'ship_hosts_total{{status="{status}"}} {count}')

    lines += [
        "# HELP ship_incidents_total Incidents by status and severity.",
        "# TYPE ship_incidents_total gauge",
    ]
    for status, severity, count in incidents:
        lines.append(f'ship_incidents_total{{status="{status}",severity="{severity}"}} {count}')

    lines += [
        "# HELP ship_recovery_actions_total Recovery actions by status.",
        "# TYPE ship_recovery_actions_total counter",
    ]
    for status, count in recoveries:
        lines.append(f'ship_recovery_actions_total{{status="{status}"}} {count}')

    lines += [
        "# HELP ship_alerts_firing Number of firing alerts.",
        "# TYPE ship_alerts_firing gauge",
        f"ship_alerts_firing {int(alerts or 0)}",
        "# HELP ship_mttr_seconds Mean time to recovery across resolved incidents.",
        "# TYPE ship_mttr_seconds gauge",
        f"ship_mttr_seconds {float(mttr or 0):.2f}",
        "# HELP ship_recovery_dry_run Recovery execution is simulated (1) or real (0).",
        "# TYPE ship_recovery_dry_run gauge",
        f"ship_recovery_dry_run {1 if settings.ansible_dry_run else 0}",
        "# HELP ship_recovery_enabled Global recovery kill switch (1 enabled).",
        "# TYPE ship_recovery_enabled gauge",
        f"ship_recovery_enabled {1 if settings.recovery_enabled else 0}",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
