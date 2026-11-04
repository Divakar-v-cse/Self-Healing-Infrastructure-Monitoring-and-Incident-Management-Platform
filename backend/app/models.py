"""SQLAlchemy ORM models for hosts, metrics, alerts, incidents and recovery."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# enums kept as plain strings for SQLite/Postgres portability
# --------------------------------------------------------------------------- #
class HostStatus:
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    ALL = (UP, DOWN, DEGRADED, UNKNOWN)


class Severity:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    ALL = (INFO, WARNING, CRITICAL)
    RANK = {INFO: 0, WARNING: 1, CRITICAL: 2}


class IncidentStatus:
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"
    ACTIVE = (OPEN, ACKNOWLEDGED, RECOVERING)
    ALL = (OPEN, ACKNOWLEDGED, RECOVERING, RESOLVED, SUPPRESSED)


class Category:
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"
    AVAILABILITY = "availability"
    SERVICE = "service"
    UNKNOWN = "unknown"
    ALL = (CPU, MEMORY, DISK, NETWORK, AVAILABILITY, SERVICE, UNKNOWN)


class RecoveryStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SIMULATED = "simulated"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    ALL = (PENDING, RUNNING, SUCCESS, FAILED, SIMULATED, BLOCKED, TIMEOUT)
    TERMINAL = (SUCCESS, FAILED, SIMULATED, BLOCKED, TIMEOUT)


class AlertStatus:
    FIRING = "firing"
    RESOLVED = "resolved"
    ALL = (FIRING, RESOLVED)


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    instance: Mapped[str | None] = mapped_column(String(255), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64), default="generic")
    environment: Mapped[str] = mapped_column(String(64), default="production")
    os: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default=HostStatus.UNKNOWN, index=True)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_recovery_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_change: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    incidents: Mapped[list["Incident"]] = relationship(back_populates="host", cascade="all, delete-orphan")
    samples: Mapped[list["MetricSample"]] = relationship(back_populates="host", cascade="all, delete-orphan")


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_host_name_ts", "host_id", "metric_name", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    metric_name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="percent")
    source: Mapped[str] = mapped_column(String(32), default="prometheus")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    host: Mapped[Host] = relationship(back_populates="samples")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    alertname: Mapped[str] = mapped_column(String(128))
    severity: Mapped[str] = mapped_column(String(32), default=Severity.WARNING, index=True)
    status: Mapped[str] = mapped_column(String(32), default=AlertStatus.FIRING, index=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("hosts.id", ondelete="SET NULL"))
    instance: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    annotations: Mapped[dict] = mapped_column(JSON, default=dict)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(32), default="alertmanager")

    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default=Category.UNKNOWN, index=True)
    severity: Mapped[str] = mapped_column(String(32), default=Severity.WARNING, index=True)
    status: Mapped[str] = mapped_column(String(32), default=IncidentStatus.OPEN, index=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str | None] = mapped_column(String(64))
    observed_value: Mapped[float | None] = mapped_column(Float)
    threshold_value: Mapped[float | None] = mapped_column(Float)
    classification_reason: Mapped[str | None] = mapped_column(Text)
    recommended_playbook: Mapped[str | None] = mapped_column(String(128))
    root_cause: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mttr_seconds: Mapped[float | None] = mapped_column(Float)
    auto_recovery_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    host: Mapped[Host | None] = relationship(back_populates="incidents")
    recovery_actions: Mapped[list["RecoveryAction"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="RecoveryAction.id"
    )


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(64), default=Category.UNKNOWN)
    playbook: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default=RecoveryStatus.PENDING, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    triggered_by: Mapped[str] = mapped_column(String(64), default="auto")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    safeguard_reason: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    incident: Mapped[Incident | None] = relationship(back_populates="recovery_actions")


class Threshold(Base):
    __tablename__ = "thresholds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32), default="percent")
    warning: Mapped[float] = mapped_column(Float)
    critical: Mapped[float] = mapped_column(Float)
    comparator: Mapped[str] = mapped_column(String(4), default=">=")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=300)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_recovery: Mapped[bool] = mapped_column(Boolean, default=True)
    category: Mapped[str] = mapped_column(String(32), default=Category.UNKNOWN)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class HealthReport(Base):
    __tablename__ = "health_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hosts_total: Mapped[int] = mapped_column(Integer, default=0)
    hosts_up: Mapped[int] = mapped_column(Integer, default=0)
    hosts_down: Mapped[int] = mapped_column(Integer, default=0)
    availability_percent: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    avg_memory_percent: Mapped[float] = mapped_column(Float, default=0.0)
    avg_disk_percent: Mapped[float] = mapped_column(Float, default=0.0)
    incidents_total: Mapped[int] = mapped_column(Integer, default=0)
    incidents_critical: Mapped[int] = mapped_column(Integer, default=0)
    incidents_open: Mapped[int] = mapped_column(Integer, default=0)
    recoveries_total: Mapped[int] = mapped_column(Integer, default=0)
    recoveries_success: Mapped[int] = mapped_column(Integer, default=0)
    recovery_success_rate: Mapped[float] = mapped_column(Float, default=0.0)
    mean_time_to_recovery_seconds: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class EventLog(Base):
    """Append-only audit / recovery trail surfaced in the UI."""

    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    category: Mapped[str] = mapped_column(String(32), default="system")
    actor: Mapped[str] = mapped_column(String(64), default="system")
    message: Mapped[str] = mapped_column(Text)
    host_id: Mapped[int | None] = mapped_column(Integer)
    incident_id: Mapped[int | None] = mapped_column(Integer)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
