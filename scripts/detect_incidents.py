#!/usr/bin/env python3
"""Run a single incident-detection cycle outside the API process.

Useful for cron-driven deployments (where the in-process monitor loop is
disabled) and for debugging PromQL/threshold behaviour.

    cd backend && python ../scripts/detect_incidents.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.database import configure_database, get_session_factory, init_db  # noqa: E402
from app.seed import seed_all  # noqa: E402
from app.services.detector import Detector  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate metrics once and reconcile incidents")
    parser.add_argument("--json", action="store_true", help="emit machine readable summary")
    args = parser.parse_args()

    configure_database()
    init_db()
    session = get_session_factory()()
    try:
        seed_all(session)
        summary = Detector().evaluate_once(session)
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("detection cycle complete")
            for key in (
                "hosts_evaluated",
                "breaches",
                "incidents_opened",
                "incidents_refreshed",
                "incidents_resolved",
                "recoveries_triggered",
                "recoveries_blocked",
            ):
                print(f"  {key:24s} {payload[key]}")
            if payload["errors"]:
                print("  errors:")
                for error in payload["errors"]:
                    print(f"    - {error}")
                return 1
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
