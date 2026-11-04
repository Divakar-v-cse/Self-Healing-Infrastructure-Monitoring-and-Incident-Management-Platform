"""Metric evaluation loop: Prometheus -> threshold breaches -> incidents -> recovery.

The detector is deliberately synchronous and dependency-injected so it can be
driven by a test with a fake Prometheus and a fake Ansible runner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Host,
    HostStatus,
    Incident,
    IncidentStatus,
    MetricSample,
    RecoveryStatus,
    Severity,
    Threshold,
)
from . import classifier, incidents as incident_service, recovery as recovery_service
from .events import log_event
from .prometheus import QUERIES, PrometheusClient, PrometheusError

logger = logging.getLogger(__name__)

METRIC_UNITS = {
    "cpu": "percent",
    "memory": "percent",
    "disk": "percent",
    "network": "bps",
    "availability": "ratio",
}

COMPARATOR_LABELS = {">=": "at or above", ">": "above", "<=": "at or below", "<": "below"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_threshold(value: float, threshold: Threshold) -> str | None:
    """Return ``warning``/``critical`` when the value breaches, else ``None``."""
    if not threshold.enabled:
        return None
    return classifier.breach_level(value, threshold.warning, threshold.critical, threshold.comparator)


@dataclass(slots=True)
class Breach:
    host: Host
    threshold: Threshold
    value: float
    severity: str


@dataclass(slots=True)
class DetectionSummary:
    started_at: datetime = field(default_factory=_utcnow)
    finished_at: datetime | None = None
    hosts_evaluated: int = 0
    breaches: int = 0
    incidents_opened: int = 0
    incidents_refreshed: int = 0
    incidents_resolved: int = 0
    recoveries_triggered: int = 0
    recoveries_blocked: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "hosts_evaluated": self.hosts_evaluated,
            "breaches": self.breaches,
            "incidents_opened": self.incidents_opened,
            "incidents_refreshed": self.incidents_refreshed,
            "incidents_resolved": self.incidents_resolved,
            "recoveries_triggered": self.recoveries_triggered,
            "recoveries_blocked": self.recoveries_blocked,
            "errors": self.errors,
        }


class Detector:
    def __init__(
        self,
        prometheus: PrometheusClient | None = None,
        runner: recovery_service.AnsibleRunner | None = None,
    ) -> None:
        self.prometheus = prometheus or PrometheusClient()
        self.runner = runner
        self._breach_since: dict[tuple[int, str], datetime] = {}

    # ------------------------------------------------------------------ #
    def _collect(self) -> dict[str, dict[str, float]]:
        """metric -> instance -> value, from a single scrape round."""
        collected: dict[str, dict[str, float]] = {}
        for metric, promql in QUERIES.items():
            try:
                samples = self.prometheus.query(promql)
            except PrometheusError as exc:
                logger.warning("prometheus query failed for %s: %s", metric, exc)
                raise
            series: dict[str, float] = {}
            for sample in samples:
                key = sample.instance or sample.labels.get("node") or "unknown"
                series[key] = sample.value
            collected[metric] = series
        return collected

    def _upsert_host(self, db: Session, instance: str, samples: dict[str, dict[str, float]]) -> Host:
        host = db.execute(select(Host).where(Host.instance == instance)).scalars().first()
        if host is None:
            hostname = instance.split(":")[0]
            host = db.execute(select(Host).where(Host.hostname == hostname)).scalars().first()
            if host is None:
                host = Host(
                    hostname=hostname,
                    instance=instance,
                    ip_address=hostname if _looks_like_ip(hostname) else None,
                    labels={"discovered": "prometheus"},
                )
                db.add(host)
                db.flush()
        if host.instance != instance:
            host.instance = instance
        return host

    # ------------------------------------------------------------------ #
    def evaluate_once(self, db: Session, now: datetime | None = None) -> DetectionSummary:
        now = now or _utcnow()
        summary = DetectionSummary(started_at=now)

        try:
            collected = self._collect()
        except PrometheusError as exc:
            summary.errors.append(str(exc))
            summary.finished_at = _utcnow()
            log_event(
                db,
                f"Detection cycle skipped: {exc}",
                level="warning",
                category="detector",
                actor="detector",
            )
            return summary

        thresholds = {
            threshold.metric: threshold
            for threshold in db.execute(select(Threshold).where(Threshold.enabled.is_(True))).scalars()
        }

        instances = sorted({instance for series in collected.values() for instance in series})
        availability = collected.get("availability", {})

        breaches: list[Breach] = []
        metric_values: dict[int, dict[str, float]] = {}

        for instance in instances:
            host = self._upsert_host(db, instance, collected)
            db.flush()
            summary.hosts_evaluated += 1

            values = {metric: series[instance] for metric, series in collected.items() if instance in series}
            metric_values[host.id] = values

            up = availability.get(instance, 0.0)
            is_up = up >= 1.0
            if not is_up:
                new_status = HostStatus.DOWN
            else:
                host.status = host.status if host.status == HostStatus.UNKNOWN else host.status
                new_status = None

            if is_up:
                worst: str | None = None
                for metric, value in values.items():
                    if metric == "availability":
                        continue
                    threshold = thresholds.get(metric)
                    if threshold is None:
                        continue
                    severity = evaluate_threshold(value, threshold)
                    if severity == Severity.CRITICAL:
                        worst = Severity.CRITICAL
                    elif severity == Severity.WARNING and worst != Severity.CRITICAL:
                        worst = Severity.WARNING
                new_status = HostStatus.DEGRADED if worst in (Severity.CRITICAL, Severity.WARNING) else HostStatus.UP
            else:
                new_status = HostStatus.DOWN

            if host.status != new_status:
                previously = host.status
                host.status = new_status
                host.last_status_change = now
                if new_status in (HostStatus.DOWN, HostStatus.DEGRADED):
                    log_event(
                        db,
                        f"Host {host.hostname} transitioned {previously} -> {new_status}",
                        level="error" if new_status == HostStatus.DOWN else "warning",
                        category="host",
                        actor="detector",
                        host_id=host.id,
                    )

            if is_up:
                host.last_seen = now

            for metric, value in values.items():
                db.add(
                    MetricSample(
                        host_id=host.id,
                        metric_name=metric,
                        value=round(float(value), 4),
                        unit=METRIC_UNITS.get(metric, "percent"),
                        recorded_at=now,
                    )
                )

            for metric, threshold in thresholds.items():
                if metric not in values:
                    continue
                value = values[metric]
                severity = evaluate_threshold(value, threshold)
                key = (host.id, metric)
                if severity is None:
                    self._breach_since.pop(key, None)
                    continue
                first_seen = self._breach_since.setdefault(key, now)
                sustained = (now - first_seen).total_seconds()
                if threshold.duration_seconds > 0 and sustained < threshold.duration_seconds:
                    continue
                breaches.append(Breach(host=host, threshold=threshold, value=value, severity=severity))

        db.commit()
        summary.breaches = len(breaches)
        breached_keys = {(breach.host.id, breach.threshold.metric) for breach in breaches}

        for breach in breaches:
            self._handle_breach(db, breach, summary, now)

        self._autoresolve(db, summary, breached_keys, now)
        self._prune_samples(db, now)

        summary.finished_at = _utcnow()
        return summary

    # ------------------------------------------------------------------ #
    def _handle_breach(self, db: Session, breach: Breach, summary: DetectionSummary, now: datetime) -> None:
        threshold = breach.threshold
        host = breach.host
        classification = classifier.classify_threshold_breach(
            metric=threshold.metric,
            observed=breach.value,
            warning=threshold.warning,
            critical=threshold.critical,
            unit=threshold.unit,
            hostname=host.hostname,
            comparator=threshold.comparator,
        )
        fp = incident_service.fingerprint("threshold", host.hostname, threshold.metric)

        try:
            result = incident_service.open_incident(
                db,
                host=host,
                classification=classification,
                fp=fp,
                metric=threshold.metric,
                observed_value=round(breach.value, 4),
                threshold_value=threshold.critical if breach.severity == Severity.CRITICAL else threshold.warning,
                details={
                    "unit": threshold.unit,
                    "comparator": threshold.comparator,
                    "thresholds": {"warning": threshold.warning, "critical": threshold.critical},
                    "severity_level": breach.severity,
                    "sustained_seconds": threshold.duration_seconds,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            summary.errors.append(f"failed to open incident for {host.hostname}/{threshold.metric}: {exc}")
            logger.exception("incident creation failed")
            return

        if result.created:
            summary.incidents_opened += 1
            action = incident_service.maybe_auto_recover(db, result.incident, reason="threshold breach")
        else:
            summary.incidents_refreshed += 1
            action = self._maybe_retry(db, result.incident)

        if action is not None:
            if action.status == RecoveryStatus.BLOCKED:
                summary.recoveries_blocked += 1
            else:
                summary.recoveries_triggered += 1

    def _maybe_retry(self, db: Session, incident: Incident):
        """Retry a failed recovery only when the previous attempt failed outright."""
        if not settings.recovery_enabled:
            return None
        actions = recovery_service.recoveries_for_incident(db, incident.id)
        if not actions:
            return None
        last = actions[-1]
        if last.status != RecoveryStatus.FAILED:
            return None
        if len([a for a in actions if a.status != RecoveryStatus.BLOCKED]) >= settings.max_attempts_per_incident:
            return None
        return recovery_service.trigger_recovery(db, incident, triggered_by="auto-retry", runner=self.runner)

    def _autoresolve(
        self,
        db: Session,
        summary: DetectionSummary,
        breached_keys: set[tuple[int, str]],
        now: datetime,
    ) -> None:
        stmt = select(Incident).where(Incident.status.in_(IncidentStatus.ACTIVE))
        for incident in db.execute(stmt).scalars():
            if incident.metric and (incident.host_id, incident.metric) in breached_keys:
                continue
            if incident.detected_at is None:
                continue
            detected = incident.detected_at
            if detected.tzinfo is None:
                detected = detected.replace(tzinfo=timezone.utc)
            age_minutes = (now - detected).total_seconds() / 60.0
            if age_minutes < settings.incident_autoresolve_minutes:
                continue
            incident_service.resolve_incident(
                db,
                incident,
                reason="metric returned below threshold for the configured settle window",
                actor="detector",
            )
            summary.incidents_resolved += 1

    def _prune_samples(self, db: Session, now: datetime) -> None:
        cutoff = now - timedelta(days=settings.metric_retention_days)
        db.execute(delete(MetricSample).where(MetricSample.recorded_at < cutoff))
        db.commit()


def _looks_like_ip(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
