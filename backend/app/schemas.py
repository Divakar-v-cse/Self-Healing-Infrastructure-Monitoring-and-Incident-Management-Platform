"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# hosts
# --------------------------------------------------------------------------- #
class HostBase(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    instance: str | None = None
    ip_address: str | None = None
    role: str = "generic"
    environment: str = "production"
    os: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    auto_recovery_enabled: bool = True


class HostCreate(HostBase):
    pass


class HostUpdate(BaseModel):
    role: str | None = None
    environment: str | None = None
    os: str | None = None
    labels: dict[str, Any] | None = None
    maintenance_mode: bool | None = None
    auto_recovery_enabled: bool | None = None


class HostRead(ORMModel):
    id: int
    hostname: str
    instance: str | None
    ip_address: str | None
    role: str
    environment: str
    os: str | None
    status: str
    maintenance_mode: bool
    auto_recovery_enabled: bool
    labels: dict[str, Any] = Field(default_factory=dict)
    last_seen: datetime | None
    created_at: datetime


class HostDetail(HostRead):
    latest_metrics: dict[str, float] = Field(default_factory=dict)
    open_incidents: int = 0


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
class MetricPoint(BaseModel):
    timestamp: datetime
    value: float


class MetricSeries(BaseModel):
    metric: str
    unit: str = "percent"
    points: list[MetricPoint] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #
class AlertRead(ORMModel):
    id: int
    fingerprint: str
    alertname: str
    severity: str
    status: str
    host_id: int | None
    instance: str | None
    summary: str | None
    description: str | None
    labels: dict[str, Any] = Field(default_factory=dict)
    starts_at: datetime
    ends_at: datetime | None
    received_at: datetime
    source: str


class AlertmanagerAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime | None = None
    endsAt: datetime | None = None
    fingerprint: str | None = None
    generatorURL: str | None = None

    @field_validator("labels", "annotations", mode="before")
    @classmethod
    def _coerce_none(cls, value: Any) -> dict[str, str]:
        return value or {}


class AlertmanagerWebhook(BaseModel):
    version: str = "4"
    groupKey: str | None = None
    status: str = "firing"
    receiver: str | None = None
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


class WebhookResult(BaseModel):
    received: int
    incidents_opened: int
    incidents_resolved: int
    recoveries_triggered: int
    blocked: int
    message: str


# --------------------------------------------------------------------------- #
# incidents
# --------------------------------------------------------------------------- #
class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str | None = None
    hostname: str | None = None
    host_id: int | None = None
    category: str = "unknown"
    severity: str = "warning"
    metric: str | None = None
    observed_value: float | None = None
    threshold_value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class IncidentUpdate(BaseModel):
    status: Literal["open", "acknowledged", "recovering", "resolved", "suppressed"] | None = None
    severity: Literal["info", "warning", "critical"] | None = None
    root_cause: str | None = None
    description: str | None = None


class RecoveryActionRead(ORMModel):
    id: int
    incident_id: int | None
    host_id: int | None
    action_type: str
    playbook: str | None
    status: str
    attempt: int
    triggered_by: str
    dry_run: bool
    safeguard_reason: str | None
    command: str | None
    output: str | None
    duration_seconds: float | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class IncidentRead(ORMModel):
    id: int
    reference: str
    title: str
    description: str | None
    category: str
    severity: str
    status: str
    host_id: int | None
    fingerprint: str
    metric: str | None
    observed_value: float | None
    threshold_value: float | None
    classification_reason: str | None
    recommended_playbook: str | None
    root_cause: str | None
    detected_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    mttr_seconds: float | None
    auto_recovery_attempted: bool
    details: dict[str, Any] = Field(default_factory=dict)
    hostname: str | None = None


class IncidentDetail(IncidentRead):
    recovery_actions: list[RecoveryActionRead] = Field(default_factory=list)


class IncidentListResponse(BaseModel):
    items: list[IncidentRead]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# thresholds & safeguards
# --------------------------------------------------------------------------- #
class ThresholdBase(BaseModel):
    metric: str
    display_name: str
    unit: str = "percent"
    warning: float
    critical: float
    comparator: Literal[">=", ">", "<=", "<"] = ">="
    duration_seconds: int = 300
    enabled: bool = True
    auto_recovery: bool = True
    category: str = "unknown"


class ThresholdUpdate(BaseModel):
    display_name: str | None = None
    unit: str | None = None
    warning: float | None = None
    critical: float | None = None
    comparator: Literal[">=", ">", "<=", "<"] | None = None
    duration_seconds: int | None = None
    enabled: bool | None = None
    auto_recovery: bool | None = None
    category: str | None = None


class ThresholdRead(ORMModel):
    id: int
    metric: str
    display_name: str
    unit: str
    warning: float
    critical: float
    comparator: str
    duration_seconds: int
    enabled: bool
    auto_recovery: bool
    category: str


class SafeguardConfig(BaseModel):
    recovery_enabled: bool = True
    ansible_enabled: bool = True
    ansible_dry_run: bool = True
    max_attempts_per_incident: int = 3
    max_attempts_per_window: int = 5
    attempt_window_minutes: int = 60
    recovery_cooldown_seconds: int = 300
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown_seconds: int = 1800
    max_concurrent_recoveries: int = 2
    incident_autoresolve_minutes: int = 10
    environment: str = "development"
    playbook_dir: str = ""
    inventory_file: str = ""


# --------------------------------------------------------------------------- #
# recovery
# --------------------------------------------------------------------------- #
class RecoveryRequest(BaseModel):
    playbook: str | None = None
    reason: str | None = None
    force: bool = False


class RecoveryResult(BaseModel):
    allowed: bool
    status: str
    reason: str | None = None
    rule: str | None = None
    action: RecoveryActionRead | None = None


# --------------------------------------------------------------------------- #
# health reports & overview
# --------------------------------------------------------------------------- #
class HealthReportRead(ORMModel):
    id: int
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    hosts_total: int
    hosts_up: int
    hosts_down: int
    availability_percent: float
    avg_cpu_percent: float
    avg_memory_percent: float
    avg_disk_percent: float
    incidents_total: int
    incidents_critical: int
    incidents_open: int
    recoveries_total: int
    recoveries_success: int
    recovery_success_rate: float
    mean_time_to_recovery_seconds: float | None
    payload: dict[str, Any] = Field(default_factory=dict)


class KPIBlock(BaseModel):
    value: float
    unit: str = ""
    delta: float | None = None
    label: str = ""


class OverviewResponse(BaseModel):
    generated_at: datetime
    hosts: dict[str, int]
    alerts: dict[str, int]
    incidents: dict[str, int]
    recoveries: dict[str, int]
    kpis: dict[str, KPIBlock]
    host_status: list[HostDetail] = Field(default_factory=list)


class EventLogRead(ORMModel):
    id: int
    created_at: datetime
    level: str
    category: str
    actor: str
    message: str
    host_id: int | None
    incident_id: int | None
    context: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #
class FaultInjection(BaseModel):
    hostname: str
    metric: Literal["cpu", "memory", "disk", "network", "down"]
    value: float | None = None
    duration_seconds: int = 120


class FaultInjectionResult(BaseModel):
    hostname: str
    metric: str
    value: float | None
    expires_at: datetime | None
    active_faults: list[dict[str, Any]] = Field(default_factory=list)


class SimulationStatus(BaseModel):
    simulator_reachable: bool
    mode: str
    hosts: list[dict[str, Any]] = Field(default_factory=list)
    active_faults: list[dict[str, Any]] = Field(default_factory=list)
    detail: str | None = None


class MessageResponse(BaseModel):
    message: str
    detail: str | None = None
