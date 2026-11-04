"""Incident classification engine.

Turns a raw signal (Prometheus evaluation result or Alertmanager alert) into a
normalised incident: category, severity, human title, recommended playbook and
a human readable reasoning trail. Rule based, deterministic and unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Category, Severity

# Alert name -> (category, playbook)
ALERT_RULES: dict[str, tuple[str, str]] = {
    "HostDown": (Category.AVAILABILITY, "recover_service.yml"),
    "InstanceDown": (Category.AVAILABILITY, "recover_service.yml"),
    "NodeExporterDown": (Category.AVAILABILITY, "recover_service.yml"),
    "HighCpuUsage": (Category.CPU, "recover_high_cpu.yml"),
    "CpuSaturation": (Category.CPU, "recover_high_cpu.yml"),
    "HighMemoryUsage": (Category.MEMORY, "recover_high_memory.yml"),
    "MemoryPressure": (Category.MEMORY, "recover_high_memory.yml"),
    "DiskSpaceLow": (Category.DISK, "recover_disk_pressure.yml"),
    "DiskWillFillIn24Hours": (Category.DISK, "recover_disk_pressure.yml"),
    "HostDiskFull": (Category.DISK, "recover_disk_pressure.yml"),
    "NetworkTrafficSpike": (Category.NETWORK, "recover_network.yml"),
    "NetworkDrops": (Category.NETWORK, "recover_network.yml"),
    "ServiceDown": (Category.SERVICE, "recover_service.yml"),
}

# Keyword fallbacks so unknown alert names from a custom ruleset still classify.
KEYWORD_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("cpu", "load", "throttl"), Category.CPU, "recover_high_cpu.yml"),
    (("memory", "mem", "oom", "swap", "pressure"), Category.MEMORY, "recover_high_memory.yml"),
    (("disk", "filesystem", "inode", "space"), Category.DISK, "recover_disk_pressure.yml"),
    (("network", "traffic", "bandwidth", "packet", "latency", "dns"), Category.NETWORK, "recover_network.yml"),
    (("down", "unreachable", "unavailable", "availability"), Category.AVAILABILITY, "recover_service.yml"),
    (("service", "process", "systemd", "unit"), Category.SERVICE, "recover_service.yml"),
]

CATEGORY_PLAYBOOKS: dict[str, str] = {
    Category.CPU: "recover_high_cpu.yml",
    Category.MEMORY: "recover_high_memory.yml",
    Category.DISK: "recover_disk_pressure.yml",
    Category.NETWORK: "recover_network.yml",
    Category.AVAILABILITY: "recover_service.yml",
    Category.SERVICE: "recover_service.yml",
    Category.UNKNOWN: "health_check.yml",
}

CATEGORY_TITLES: dict[str, str] = {
    Category.CPU: "Sustained CPU saturation",
    Category.MEMORY: "Memory pressure detected",
    Category.DISK: "Filesystem capacity critical",
    Category.NETWORK: "Network traffic anomaly",
    Category.AVAILABILITY: "Host unreachable",
    Category.SERVICE: "Service unavailable",
    Category.UNKNOWN: "Unclassified infrastructure anomaly",
}

CATEGORY_RUNBOOK: dict[str, list[str]] = {
    Category.CPU: [
        "Confirm the spike is not a scheduled batch job.",
        "Identify the top CPU consumer with `top -b -n1 | head -20`.",
        "Scale out or restart the offending unit if it is a runaway process.",
        "Verify CPU returns below the warning threshold for 5 minutes.",
    ],
    Category.MEMORY: [
        "Check `free -m` and `vmstat 1 5` for swap thrash.",
        "Identify the largest RSS consumer and whether it is leaking.",
        "Restart the offending service after flushing reclaimable caches.",
        "Confirm memory holds below threshold for 10 minutes.",
    ],
    Category.DISK: [
        "Find the largest directories with `du -xh --max-depth=2 / | sort -h`.",
        "Rotate or compress logs, prune unused images and tmp files.",
        "Verify inode usage as well as block usage.",
        "Confirm free space is above 20%.",
    ],
    Category.NETWORK: [
        "Check interface errors and drops with `ip -s link`.",
        "Correlate with deployment or backup windows.",
        "Look for retransmits and asymmetric routing.",
        "Confirm throughput normalises.",
    ],
    Category.AVAILABILITY: [
        "Ping and TCP-probe the host from the monitoring node.",
        "Check power, hypervisor and network path.",
        "Attempt an out-of-band console session.",
        "Escalate to the on-call owner if unreachable for 10 minutes.",
    ],
    Category.SERVICE: [
        "Inspect `systemctl status <unit>` and recent journal lines.",
        "Validate dependencies (DB, cache, upstream APIs).",
        "Restart the unit and confirm the health endpoint.",
        "Watch for crash loops before escalating.",
    ],
    Category.UNKNOWN: [
        "Inspect the raw alert payload and dashboards.",
        "Assign an owner and classify manually.",
    ],
}


@dataclass(slots=True)
class Classification:
    category: str
    severity: str
    title: str
    playbook: str
    reason: str
    runbook: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "playbook": self.playbook,
            "reason": self.reason,
            "runbook": self.runbook,
            "tags": self.tags,
        }


def _clamp_severity(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate in Severity.ALL:
        return candidate
    if candidate in {"critical", "page", "emergency", "fatal", "high", "sev1", "p1"}:
        return Severity.CRITICAL
    if candidate in {"warning", "warn", "medium", "sev2", "p2"}:
        return Severity.WARNING
    if candidate in {"info", "notice", "low", "sev3", "p3"}:
        return Severity.INFO
    return Severity.WARNING


def breach_level(value: float, warning: float, critical: float, comparator: str = ">=") -> str | None:
    """Single source of truth for threshold semantics.

    Returns ``critical``, ``warning`` or ``None``. Strict (``>`` / ``<``) and
    non-strict (``>=`` / ``<=``) comparators are honoured exactly, so an
    availability threshold of ``< 1`` is not triggered by ``up == 1``.
    """
    comparator = comparator or ">="
    if comparator == ">=":
        if value >= critical:
            return Severity.CRITICAL
        if value >= warning:
            return Severity.WARNING
    elif comparator == ">":
        if value > critical:
            return Severity.CRITICAL
        if value > warning:
            return Severity.WARNING
    elif comparator == "<=":
        if value <= critical:
            return Severity.CRITICAL
        if value <= warning:
            return Severity.WARNING
    else:  # "<"
        if value < critical:
            return Severity.CRITICAL
        if value < warning:
            return Severity.WARNING
    return None


def category_from_metric(metric: str, observed: float | None, warning: float, critical: float) -> str:
    """Severity for a threshold breach (assumes a 'greater than' comparator)."""
    if observed is None:
        return Severity.WARNING
    return breach_level(observed, warning, critical) or Severity.INFO


def classify_alert(labels: dict[str, str], annotations: dict[str, str] | None = None) -> Classification:
    """Classify an Alertmanager alert."""
    annotations = annotations or {}
    alertname = labels.get("alertname", "UnknownAlert")
    reason_bits: list[str] = [f"alertname={alertname}"]

    category: str | None = None
    playbook: str | None = None

    if alertname in ALERT_RULES:
        category, playbook = ALERT_RULES[alertname]
        reason_bits.append("matched exact alert rule table")
    else:
        haystack = " ".join(
            [
                alertname.lower(),
                labels.get("category", "").lower(),
                annotations.get("summary", "").lower(),
                annotations.get("description", "").lower(),
            ]
        )
        for keywords, candidate_category, candidate_playbook in KEYWORD_RULES:
            if any(keyword in haystack for keyword in keywords):
                category, playbook = candidate_category, candidate_playbook
                matched = next(k for k in keywords if k in haystack)
                reason_bits.append(f"keyword '{matched}' matched {candidate_category} rule")
                break

    if category is None:
        category = labels.get("category") if labels.get("category") in Category.ALL else Category.UNKNOWN
        playbook = CATEGORY_PLAYBOOKS.get(category, "health_check.yml")
        reason_bits.append("no rule matched, fell back to category label")

    severity = _clamp_severity(labels.get("severity") or annotations.get("severity"))
    reason_bits.append(f"severity={severity} from alert labels")

    summary = annotations.get("summary") or labels.get("summary") or ""
    title = summary.strip() or f"{CATEGORY_TITLES.get(category, alertname)} on {labels.get('instance', 'unknown host')}"

    return Classification(
        category=category,
        severity=severity,
        title=title[:255],
        playbook=playbook or CATEGORY_PLAYBOOKS[category],
        reason="; ".join(reason_bits),
        runbook=CATEGORY_RUNBOOK.get(category, []),
        tags=[category, severity, alertname],
    )


def classify_threshold_breach(
    metric: str,
    observed: float,
    warning: float,
    critical: float,
    unit: str = "percent",
    hostname: str = "",
    comparator: str = ">=",
) -> Classification:
    """Classify a metric threshold breach detected by the evaluator."""
    severity = breach_level(observed, warning, critical, comparator) or Severity.WARNING

    category = {
        "cpu": Category.CPU,
        "memory": Category.MEMORY,
        "disk": Category.DISK,
        "network": Category.NETWORK,
        "availability": Category.AVAILABILITY,
    }.get(metric, Category.UNKNOWN)

    threshold = critical if severity == Severity.CRITICAL else warning
    suffix = "%" if unit == "percent" else f" {unit}"
    title = f"{CATEGORY_TITLES.get(category, metric)} on {hostname or 'unknown host'}"
    reason = (
        f"metric={metric} observed={observed:.2f}{suffix} "
        f"breached {severity} threshold {threshold:.2f}{suffix} "
        f"(warning={warning:.2f}, critical={critical:.2f}, comparator={comparator})"
    )
    return Classification(
        category=category,
        severity=severity,
        title=title[:255],
        playbook=CATEGORY_PLAYBOOKS.get(category, "health_check.yml"),
        reason=reason,
        runbook=CATEGORY_RUNBOOK.get(category, []),
        tags=[category, severity, metric],
    )


def root_cause_hint(category: str, details: dict[str, Any] | None = None) -> str:
    """Short hypothesis used to pre-fill the incident root cause field."""
    details = details or {}
    hint = {
        Category.CPU: "Sustained CPU saturation - likely a runaway process, traffic surge or undersized instance.",
        Category.MEMORY: "Memory pressure - possible application leak, cache growth or undersized instance.",
        Category.DISK: "Disk pressure - log growth, container images or unrotated backups consuming capacity.",
        Category.NETWORK: "Network anomaly - possible interface errors, saturation or upstream routing change.",
        Category.AVAILABILITY: "Host unreachable - possible power, hypervisor, network path or kernel failure.",
        Category.SERVICE: "Service unavailable - possible crash loop, failed deploy or dependency outage.",
        Category.UNKNOWN: "Unclassified - requires operator triage.",
    }.get(category, "Unclassified - requires operator triage.")
    if details.get("playbook"):
        hint += f" Auto-recovery candidate: {details['playbook']}."
    return hint
