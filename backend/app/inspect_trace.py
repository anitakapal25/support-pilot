"""Read one redacted request trace using its server-generated X-Request-ID."""

import argparse
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import make_engine
from app.models import AuditEvent, RequestTrace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id", type=UUID)
    args = parser.parse_args()
    engine = make_engine(Settings())
    try:
        with Session(engine) as db:
            row = db.get(RequestTrace, str(args.request_id))
            if row is None:
                raise SystemExit("Request trace not found")
            payload = {
                key: getattr(row, key)
                for key in (
                    "id",
                    "started_at",
                    "duration_ms",
                    "method",
                    "route",
                    "status_code",
                    "actor_id",
                    "conversation_id",
                    "events",
                    "dropped_events",
                )
            }
            payload["committed_audits"] = [
                {
                    "action": audit.action,
                    "resource_id": audit.resource_id,
                    "created_at": audit.created_at,
                }
                for audit in db.scalars(select(AuditEvent).where(AuditEvent.trace_id == row.id))
            ]
            print(json.dumps(payload, indent=2, default=str))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
