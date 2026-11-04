"""Background detection loop.

Runs the detector on a fixed interval, each cycle in a worker thread with its
own DB session so a slow Prometheus query cannot block API requests.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..database import session_scope
from ..services import reports as report_service
from ..services.detector import Detector
from ..services.events import log_event

logger = logging.getLogger(__name__)

_state: dict[str, object] = {"last_summary": None, "cycles": 0, "last_error": None, "last_report_at": None}


def state() -> dict[str, object]:
    return dict(_state)


def _run_cycle() -> dict:
    db = session_scope()
    try:
        summary = Detector().evaluate_once(db)
        _state["last_summary"] = summary.as_dict()
        _state["cycles"] = int(_state.get("cycles", 0)) + 1
        _state["last_error"] = None
        if summary.errors:
            _state["last_error"] = summary.errors[0]
        return summary.as_dict()
    finally:
        db.close()


async def monitor_loop(stop_event: asyncio.Event | None = None) -> None:
    logger.info("monitor loop starting (interval=%ss, dry_run=%s)", settings.monitor_interval_seconds, settings.ansible_dry_run)
    cycles = 0
    while True:
        try:
            await asyncio.to_thread(_run_cycle)
            cycles += 1
            # generate an hourly health report snapshot for trend history
            if cycles == 1 or cycles % 120 == 0:
                db = session_scope()
                try:
                    report_service.generate_health_report(db, hours=24)
                finally:
                    db.close()
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            logger.info("monitor loop cancelled")
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _state["last_error"] = str(exc)
            logger.exception("monitor cycle failed: %s", exc)
            db = session_scope()
            try:
                log_event(db, f"Monitor cycle failed: {exc}", level="error", category="detector")
            except Exception:
                db.rollback()
            finally:
                db.close()

        try:
            await asyncio.wait_for(
                stop_event.wait() if stop_event else asyncio.sleep(settings.monitor_interval_seconds),
                timeout=settings.monitor_interval_seconds,
            )
            return
        except asyncio.TimeoutError:
            continue
