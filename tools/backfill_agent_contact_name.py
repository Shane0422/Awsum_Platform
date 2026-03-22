from __future__ import annotations

import argparse

from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.agent import Agent


def normalize_name(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def run_backfill(dry_run: bool = True) -> tuple[int, int]:
    db = PlatformSessionLocal()
    scanned = 0
    updated = 0
    try:
        rows = db.query(Agent).order_by(Agent.i_agent_id.asc()).all()
        for row in rows:
            scanned += 1
            current_contact = normalize_name(row.c_contact_name)
            if current_contact:
                continue

            fallback_name = normalize_name(row.c_agent_name)
            if not fallback_name:
                continue

            updated += 1
            if dry_run:
                print(f"[DRY-RUN] agent_id={row.i_agent_id}: c_contact_name <- {fallback_name}")
            else:
                row.c_contact_name = fallback_name
                row.c_agent_name = fallback_name

        if dry_run:
            db.rollback()
        else:
            db.commit()

        return scanned, updated
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill tb_agent.c_contact_name from c_agent_name when contact_name is empty."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, script runs in dry-run mode.",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    scanned, updated = run_backfill(dry_run=dry_run)

    mode = "DRY-RUN" if dry_run else "APPLY"
    print(f"[{mode}] scanned={scanned}, candidates={updated}")


if __name__ == "__main__":
    main()
