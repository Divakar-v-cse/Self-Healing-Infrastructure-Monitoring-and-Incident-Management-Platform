"""Append-only event log used for the activity feed and audit trail."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EventLog

logger = logging.getLogger(__name__)


def log_event(
    db: Session,
    message: str,
    *,
    level: str = "info",
    category: str = "system",
    actor: str = "system",
    host_id: int | None = None,
    incident_id: int | None = None,
    context: dict[str, Any] | None = None,
    commit: bool = True,
) -> EventLog:
    entry = EventLog(
        message=message,
        level=level,
        category=category,
        actor=actor,
        host_id=host_id,
        incident_id=incident_id,
        context=context or {},
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    logger.info("[%s/%s] %s", category, level, message)
    return entry


def recent_events(db: Session, limit: int = 50, hours: int = 24, category: str | None = None) -> list[EventLog]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(EventLog).where(EventLog.created_at >= since).order_by(EventLog.id.desc()).limit(limit)
    if category:
        stmt = stmt.where(EventLog.category == category)
    return list(db.execute(stmt).scalars())
