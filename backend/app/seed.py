"""Idempotent bootstrap data: default thresholds and optional demo hosts."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Category, Host, HostStatus, Threshold

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS: list[dict] = [
    {
        "metric": "cpu",
        "display_name": "CPU Utilisation",
        "unit": "percent",
        "warning": 75.0,
        "critical": 90.0,
        "comparator": ">=",
        "duration_seconds": 300,
        "category": Category.CPU,
    },
    {
        "metric": "memory",
        "display_name": "Memory Utilisation",
        "unit": "percent",
        "warning": 80.0,
        "critical": 92.0,
        "comparator": ">=",
        "duration_seconds": 300,
        "category": Category.MEMORY,
    },
    {
        "metric": "disk",
        "display_name": "Disk Utilisation",
        "unit": "percent",
        "warning": 80.0,
        "critical": 90.0,
        "comparator": ">=",
        "duration_seconds": 300,
        "category": Category.DISK,
    },
    {
        "metric": "network",
        "display_name": "Network Throughput",
        "unit": "bps",
        "warning": 100_000_000.0,
        "critical": 500_000_000.0,
        "comparator": ">=",
        "duration_seconds": 180,
        "category": Category.NETWORK,
    },
    {
        "metric": "availability",
        "display_name": "Host Availability",
        "unit": "ratio",
        "warning": 1.0,
        "critical": 0.5,
        "comparator": "<",
        "duration_seconds": 60,
        "category": Category.AVAILABILITY,
    },
]

DEMO_HOSTS: list[dict] = [
    {
        "hostname": "web-01",
        "instance": "10.10.0.11:9100",
        "ip_address": "10.10.0.11",
        "role": "web",
        "os": "Ubuntu 22.04",
        "labels": {"env": "simulated", "tier": "frontend"},
    },
    {
        "hostname": "api-01",
        "instance": "10.10.0.12:9100",
        "ip_address": "10.10.0.12",
        "role": "api",
        "os": "Ubuntu 22.04",
        "labels": {"env": "simulated", "tier": "backend"},
    },
    {
        "hostname": "db-01",
        "instance": "10.10.0.13:9100",
        "ip_address": "10.10.0.13",
        "role": "database",
        "os": "Rocky Linux 9",
        "labels": {"env": "simulated", "tier": "data"},
    },
]


def seed_thresholds(db: Session) -> int:
    created = 0
    for spec in DEFAULT_THRESHOLDS:
        exists = db.execute(select(Threshold).where(Threshold.metric == spec["metric"])).scalars().first()
        if exists:
            continue
        db.add(Threshold(**spec))
        created += 1
    if created:
        db.commit()
        logger.info("seeded %s default thresholds", created)
    return created


def seed_demo_hosts(db: Session) -> int:
    created = 0
    for spec in DEMO_HOSTS:
        exists = db.execute(select(Host).where(Host.hostname == spec["hostname"])).scalars().first()
        if exists:
            continue
        db.add(Host(status=HostStatus.UNKNOWN, **spec))
        created += 1
    if created:
        db.commit()
        logger.info("seeded %s demo hosts", created)
    return created


def seed_all(db: Session, include_demo_hosts: bool = True) -> dict[str, int]:
    result = {"thresholds": seed_thresholds(db)}
    if include_demo_hosts:
        result["hosts"] = seed_demo_hosts(db)
    return result
