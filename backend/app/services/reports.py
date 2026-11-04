"""Health reporting and dashboard aggregation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Alert,
    AlertStatus,
    HealthReport,
    Host,
    HostStatus,
    Incident,
    IncidentStatus,
    MetricSample,
    RecoveryAction,
    RecoveryStatus,
    Severity,
)
from .events import log_event

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _avg_metric(db: Session, metric: str, since: datetime) -> float:
    stmt = select(func.avg(MetricSample.value)).where(
        MetricSample.metric_name == metric,
        MetricSample.recorded_at >= since,
    )
    value = db.execute(stmt).scalar_one_or_none()
    return round(float(value or 0.0), 2)


def generate_health_report(db: Session, hours: int = 24) -> HealthReport:
    now = _utcnow()
    window_start = now - timedelta(hours=hours)

    hosts = list(db.execute(select(Host)).scalars())
    hosts_up = sum(1 for host in hosts if host.status in (HostStatus.UP, HostStatus.DEGRADED))
    hosts_down = sum(1 for host in hosts if host.status == HostStatus.DOWN)
    availability = round(100.0 * hosts_up / len(hosts), 2) if hosts else 0.0

    incidents_in_window = list(
        db.execute(select(Incident).where(Incident.detected_at >= window_start)).scalars()
    )
    incidents_critical = sum(1 for inc in incidents_in_window if inc.severity == Severity.CRITICAL)
    incidents_open = db.execute(
        select(func.count(Incident.id)).where(Incident.status.in_(IncidentStatus.ACTIVE))
    ).scalar_one()

    actions = list(
        db.execute(select(RecoveryAction).where(RecoveryAction.created_at >= window_start)).scalars()
    )
    executed = [a for a in actions if a.status in (RecoveryStatus.SUCCESS, RecoveryStatus.SIMULATED, RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT)]
    successes = [a for a in actions if a.status in (RecoveryStatus.SUCCESS, RecoveryStatus.SIMULATED)]
    success_rate = round(100.0 * len(successes) / len(executed), 2) if executed else 0.0

    resolved = [inc for inc in incidents_in_window if inc.mttr_seconds is not None]
    mttr = round(sum(inc.mttr_seconds for inc in resolved) / len(resolved), 1) if resolved else None

    per_host = []
    for host in hosts:
        host_incidents = [inc for inc in incidents_in_window if inc.host_id == host.id]
        per_host.append(
            {
                "hostname": host.hostname,
                "status": host.status,
                "maintenance_mode": host.maintenance_mode,
                "incidents": len(host_incidents),
                "critical_incidents": sum(1 for inc in host_incidents if inc.severity == Severity.CRITICAL),
            }
        )

    category_counts: dict[str, int] = {}
    for incident in incidents_in_window:
        category_counts[incident.category] = category_counts.get(incident.category, 0) + 1

    payload = {
        "hours": hours,
        "per_host": per_host,
        "incidents_by_category": category_counts,
        "recoveries": {
            "total": len(actions),
            "executed": len(executed),
            "successful": len(successes),
            "failed": sum(1 for a in actions if a.status in (RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT)),
            "blocked": sum(1 for a in actions if a.status == RecoveryStatus.BLOCKED),
        },
        "top_incidents": [
            {
                "reference": inc.reference,
                "title": inc.title,
                "severity": inc.severity,
                "host_id": inc.host_id,
                "status": inc.status,
                "detected_at": inc.detected_at.isoformat() if inc.detected_at else None,
                "mttr_seconds": inc.mttr_seconds,
            }
            for inc in sorted(
                incidents_in_window,
                key=lambda item: (Severity.RANK.get(item.severity, 0), item.detected_at or now),
                reverse=True,
            )[:10]
        ],
    }

    report = HealthReport(
        generated_at=now,
        window_start=window_start,
        window_end=now,
        hosts_total=len(hosts),
        hosts_up=hosts_up,
        hosts_down=hosts_down,
        availability_percent=availability,
        avg_cpu_percent=_avg_metric(db, "cpu", window_start),
        avg_memory_percent=_avg_metric(db, "memory", window_start),
        avg_disk_percent=_avg_metric(db, "disk", window_start),
        incidents_total=len(incidents_in_window),
        incidents_critical=incidents_critical,
        incidents_open=int(incidents_open or 0),
        recoveries_total=len(actions),
        recoveries_success=len(successes),
        recovery_success_rate=success_rate,
        mean_time_to_recovery_seconds=mttr,
        payload=payload,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    log_event(
        db,
        f"Health report generated for the last {hours}h "
        f"({report.hosts_up}/{report.hosts_total} hosts healthy, {report.incidents_total} incidents)",
        category="report",
        actor="reporter",
        context={"report_id": report.id},
    )
    return report


def build_overview(db: Session, hours: int = 24) -> dict:
    now = _utcnow()
    since = now - timedelta(hours=hours)

    hosts = list(db.execute(select(Host).order_by(Host.hostname)).scalars())
    status_counts = {status: 0 for status in HostStatus.ALL}
    for host in hosts:
        status_counts[host.status] = status_counts.get(host.status, 0) + 1

    active_alerts = db.execute(
        select(func.count(Alert.id)).where(Alert.status == AlertStatus.FIRING)
    ).scalar_one()
    critical_alerts = db.execute(
        select(func.count(Alert.id)).where(Alert.status == AlertStatus.FIRING, Alert.severity == Severity.CRITICAL)
    ).scalar_one()

    open_incidents = db.execute(
        select(func.count(Incident.id)).where(Incident.status.in_(IncidentStatus.ACTIVE))
    ).scalar_one()
    critical_incidents = db.execute(
        select(func.count(Incident.id)).where(
            Incident.status.in_(IncidentStatus.ACTIVE), Incident.severity == Severity.CRITICAL
        )
    ).scalar_one()
    incidents_window = db.execute(
        select(func.count(Incident.id)).where(Incident.detected_at >= since)
    ).scalar_one()
    resolved_window = db.execute(
        select(func.count(Incident.id)).where(Incident.resolved_at >= since)
    ).scalar_one()

    actions_window = list(db.execute(select(RecoveryAction).where(RecoveryAction.created_at >= since)).scalars())
    executed = [a for a in actions_window if a.status in (RecoveryStatus.SUCCESS, RecoveryStatus.SIMULATED, RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT)]
    successes = [a for a in actions_window if a.status in (RecoveryStatus.SUCCESS, RecoveryStatus.SIMULATED)]
    blocked = [a for a in actions_window if a.status == RecoveryStatus.BLOCKED]
    running = [a for a in actions_window if a.status == RecoveryStatus.RUNNING]

    mttr_rows = db.execute(
        select(Incident.mttr_seconds).where(Incident.resolved_at >= since, Incident.mttr_seconds.is_not(None))
    ).scalars()
    mttr_values = [value for value in mttr_rows if value is not None]
    mttr = round(sum(mttr_values) / len(mttr_values), 1) if mttr_values else 0.0

    healthy = status_counts.get(HostStatus.UP, 0)
    availability = round(100.0 * (healthy + status_counts.get(HostStatus.DEGRADED, 0)) / len(hosts), 1) if hosts else 0.0
    success_rate = round(100.0 * len(successes) / len(executed), 1) if executed else 0.0

    host_cards = []
    for host in hosts:
        open_for_host = sum(
            1
            for inc in db.execute(
                select(Incident).where(Incident.host_id == host.id, Incident.status.in_(IncidentStatus.ACTIVE))
            ).scalars()
        )
        latest: dict[str, float] = {}
        for metric in ("cpu", "memory", "disk", "network", "availability"):
            value = db.execute(
                select(MetricSample.value)
                .where(MetricSample.host_id == host.id, MetricSample.metric_name == metric)
                .order_by(MetricSample.recorded_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if value is not None:
                latest[metric] = round(float(value), 2)
        host_cards.append(
            {
                "id": host.id,
                "hostname": host.hostname,
                "instance": host.instance,
                "ip_address": host.ip_address,
                "role": host.role,
                "environment": host.environment,
                "os": host.os,
                "status": host.status,
                "maintenance_mode": host.maintenance_mode,
                "auto_recovery_enabled": host.auto_recovery_enabled,
                "labels": host.labels or {},
                "last_seen": host.last_seen,
                "created_at": host.created_at,
                "latest_metrics": latest,
                "open_incidents": open_for_host,
            }
        )

    return {
        "generated_at": now,
        "hosts": status_counts,
        "alerts": {"firing": int(active_alerts or 0), "critical": int(critical_alerts or 0)},
        "incidents": {
            "open": int(open_incidents or 0),
            "critical": int(critical_incidents or 0),
            "in_window": int(incidents_window or 0),
            "resolved_in_window": int(resolved_window or 0),
        },
        "recoveries": {
            "total": len(actions_window),
            "successful": len(successes),
            "failed": sum(1 for a in actions_window if a.status in (RecoveryStatus.FAILED, RecoveryStatus.TIMEOUT)),
            "blocked": len(blocked),
            "running": len(running),
        },
        "kpis": {
            "availability": {"value": availability, "unit": "%", "label": "Availability"},
            "mttr": {"value": round(mttr / 60.0, 1), "unit": "min", "label": "Mean time to recovery"},
            "recovery_success": {"value": success_rate, "unit": "%", "label": "Recovery success"},
            "open_incidents": {"value": float(open_incidents or 0), "unit": "", "label": "Open incidents"},
        },
        "host_status": host_cards,
    }
