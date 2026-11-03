"""Operator-only pilot controls and bounded retention cleanup."""

import argparse
import json
from datetime import timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.checkpoints import checkpoint_connection, lock_key
from app.config import Settings
from app.database import make_engine
from app.models import (
    AuditEvent,
    LoginRateLimit,
    LoginSession,
    OperationalCounter,
    PilotControl,
    RequestTrace,
    UsageReservation,
    utcnow,
)


def set_pilot(engine, enabled, percent):
    if not 0 <= percent <= 100:
        raise ValueError("Admission percentage must be 0..100")
    with Session(engine) as db, db.begin():
        db.execute(
            insert(PilotControl)
            .values(key="pilot", enabled=enabled, admission_percent=percent)
            .on_conflict_do_update(
                index_elements=[PilotControl.key],
                set_={"enabled": enabled, "admission_percent": percent},
            )
        )
    return {"enabled": enabled, "admission_percent": percent}


def cleanup(engine, days=30, batch=100, dry_run=True):
    cutoff = utcnow() - timedelta(days=days)
    # Retain unresolved human work and recently active conversations. Never interrupt a run.
    query = """SELECT c.id FROM conversations c WHERE c.created_at < :cutoff
        AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id AND m.created_at>=:cutoff)
        AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.conversation_id=c.id AND (r.updated_at>=:cutoff OR r.status IN ('pending','running','retryable')))
        AND NOT EXISTS (SELECT 1 FROM handoffs h WHERE h.conversation_id=c.id AND (h.status!='resolved' OR h.resolved_at>=:cutoff))
        ORDER BY c.created_at LIMIT :batch"""
    with engine.connect() as connection:
        candidates = list(connection.scalars(text(query), {"cutoff": cutoff, "batch": batch}))
    deleted = 0
    if not dry_run:
        for id in candidates:
            with checkpoint_connection(engine) as conn:
                if not conn.execute(
                    "SELECT pg_try_advisory_lock(%s) AS held", (lock_key(str(id)),)
                ).fetchone()["held"]:
                    continue
                with conn.transaction():
                    conn.execute("SELECT id FROM conversations WHERE id=%s FOR UPDATE", (id,))
                    # Recheck under the same row lock used by customer and human writes.
                    fresh = conn.execute(
                        """SELECT 1 FROM conversations c WHERE c.id=%s
                        AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id=c.id AND created_at>=%s)
                        AND NOT EXISTS (SELECT 1 FROM agent_runs WHERE conversation_id=c.id AND (updated_at>=%s OR status IN ('pending','running','retryable')))
                        AND NOT EXISTS (SELECT 1 FROM handoffs WHERE conversation_id=c.id AND (status!='resolved' OR resolved_at>=%s))""",
                        (id, cutoff, cutoff, cutoff),
                    ).fetchone()
                    if not fresh:
                        continue
                    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        conn.execute(
                            f"DELETE FROM {table} WHERE thread_id IN (SELECT id::text FROM agent_runs WHERE conversation_id=%s)",
                            (id,),
                        )
                    conn.execute("DELETE FROM request_traces WHERE conversation_id=%s", (id,))
                    conn.execute("DELETE FROM audit_events WHERE conversation_id=%s", (id,))
                    conn.execute("DELETE FROM conversations WHERE id=%s", (id,))
                    deleted += 1
        with Session(engine) as db, db.begin():
            for model, field, boundary in (
                (RequestTrace, RequestTrace.started_at, cutoff),
                (AuditEvent, AuditEvent.created_at, cutoff),
                (LoginSession, LoginSession.expires_at, utcnow()),
                (LoginRateLimit, LoginRateLimit.expires_at, utcnow()),
                (OperationalCounter, OperationalCounter.expires_at, utcnow()),
                (UsageReservation, UsageReservation.created_at, utcnow() - timedelta(days=90)),
            ):
                db.execute(delete(model).where(field < boundary))
    return {
        "dry_run": dry_run,
        "eligible_conversations": len(candidates),
        "deleted_conversations": deleted,
        "retention_days": days,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pilot = commands.add_parser("pilot")
    pilot.add_argument("--enabled", choices=["true", "false"], required=True)
    pilot.add_argument("--percent", type=int, default=100)
    retention = commands.add_parser("retention")
    retention.add_argument("--apply", action="store_true")
    retention.add_argument("--batch", type=int, default=100)
    commands.add_parser("status")
    args = parser.parse_args()
    settings = Settings()
    engine = make_engine(settings)
    try:
        if args.command == "pilot":
            result = set_pilot(engine, args.enabled == "true", args.percent)
        elif args.command == "retention":
            if not 1 <= args.batch <= 1000:
                parser.error("batch must be 1..1000")
            result = cleanup(engine, settings.retention_days, args.batch, not args.apply)
        else:
            with Session(engine) as db:
                row = db.get(PilotControl, "pilot")
                result = {
                    "enabled": settings.pilot_enabled and (row.enabled if row else True),
                    "admission_percent": min(
                        settings.pilot_admission_percent, row.admission_percent if row else 100
                    ),
                    "monthly_reserved_micro_usd": db.scalar(
                        select(OperationalCounter.used).where(
                            OperationalCounter.key == "spend:" + utcnow().strftime("%Y-%m")
                        )
                    )
                    or 0,
                }
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
