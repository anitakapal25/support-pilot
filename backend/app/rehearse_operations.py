"""Disposable database restore, persisted-state checks, and previous-code compatibility."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.backup import backup, restore
from app.checkpoints import setup_checkpoints
from app.config import Settings
from app.embeddings import make_embedder
from app.eval_dataset import load_dataset
from app.eval_runner import EvaluationSession, Trial
from app.evaluate_release import score
from app.ingest import ingest
from app.seed import seed_demo


def fingerprint(engine):
    result = {}
    with engine.connect() as conn:
        for table in sorted(inspect(conn).get_table_names(schema="public")):
            # Database-generated table names, quoted through the driver's identifier preparer.
            name = conn.dialect.identifier_preparer.quote(table)
            rows = [
                json.dumps(row, sort_keys=True, default=str)
                for row in conn.scalars(text(f"SELECT row_to_json(t) FROM {name} t"))
            ]
            result[table] = {
                "rows": len(rows),
                "sha256": hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(),
            }
    return result


SMOKE = """
import os
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from app.seed import DEMO_PASSWORD
with TestClient(create_app(Settings(agent_provider='demo',embedding_provider='demo',app_env='development',cookie_secure=False))) as client:
    assert client.get('/health/ready').status_code == 200
    login=client.post('/api/auth/login',json={'email':'alice@example.test','password':DEMO_PASSWORD},headers={'X-Requested-With':'support-pilot'})
    assert login.status_code == 200
    headers={'X-CSRF-Token':login.json()['csrf_token']}
    history=client.get('/api/conversations/'+os.environ['RESTORED_CONVERSATION'])
    assert history.status_code == 200 and len(history.json()['messages']) >= 2
    reply=client.post('/api/conversations/'+os.environ['RESTORED_CONVERSATION']+'/messages',headers=headers,json={'request_id':str(uuid4()),'message':'When will it arrive?'})
    assert reply.status_code == 200 and reply.json()['response']['order']['order_reference']=='SP-1001'
print('Persisted history and follow-up passed')
"""


def rehearse(settings, container, output, rollback_ref):
    root = Path(__file__).resolve().parents[2]
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    names = ["m7_source_" + uuid4().hex, "m7_restore_" + uuid4().hex]
    created = []
    engines = []
    start = time.perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"passed": False, "status": "incomplete"}) + "\n")
    try:
        for name in names:
            with admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
            created.append(name)
            engines.append(create_engine(url.set(database=name)))
        source, target = engines
        config = Config("alembic.ini")
        with source.begin() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
        seed_demo(source)
        setup_checkpoints(source)
        ingest(source, root / "knowledge", make_embedder(settings), settings)
        cases, _, _ = load_dataset(root / "evals/scenarios.json")
        checks = {}
        conversation = None
        with EvaluationSession(source, settings) as session:
            for id in ["order-alice-own", "ticket-replay", "handoff-reply", "policy-return-window"]:
                case = next(case for case in cases if case.id == id)
                trial = Trial(session, case)
                actual = trial.execute()
                checks[id] = all(score(case, actual).values())
                if id == "order-alice-own":
                    conversation = trial.conversations[0]
        expected = fingerprint(source)
        with TemporaryDirectory(prefix="support-pilot-recovery-") as folder:
            archive = Path(folder) / "database.dump"
            backup_settings = settings.model_copy(
                update={
                    "database_url": settings.database_url.__class__(
                        url.set(database=names[0]).render_as_string(hide_password=False)
                    )
                }
            )
            restore_settings = settings.model_copy(
                update={
                    "database_url": settings.database_url.__class__(
                        url.set(database=names[1]).render_as_string(hide_password=False)
                    )
                }
            )
            manifest = backup(backup_settings, archive, container)
            restore_start = time.perf_counter()
            restore(restore_settings, archive, container)
            checks["exact_database_restore"] = fingerprint(target) == expected
            restore_seconds = time.perf_counter() - restore_start
            try:
                restore(restore_settings, archive, container)
            except ValueError:
                checks["nonempty_restore_blocked"] = True
            else:
                checks["nonempty_restore_blocked"] = False
            env = {
                **os.environ,
                "DATABASE_URL": restore_settings.database_url.get_secret_value(),
                "RESTORED_CONVERSATION": conversation,
                "PYTHONPATH": str(root / "backend"),
            }
            subprocess.run([sys.executable, "-c", SMOKE], env=env, check=True)
            checks["restart_followup"] = True
            old_archive = Path(folder) / "previous.tar"
            with old_archive.open("wb") as stream:
                subprocess.run(
                    ["git", "archive", rollback_ref, "backend"], stdout=stream, check=True, cwd=root
                )
            previous = Path(folder) / "previous"
            previous.mkdir()
            with tarfile.open(old_archive) as stream:
                stream.extractall(previous, filter="data")
            env["PYTHONPATH"] = str(previous / "backend")
            subprocess.run([sys.executable, "-c", SMOKE], env=env, cwd=folder, check=True)
            checks["previous_code_on_expanded_schema"] = True
            report = {
                "passed": all(checks.values()),
                "checks": checks,
                "tables": expected,
                "backup_bytes": manifest["bytes"],
                "restore_seconds": round(restore_seconds, 3),
                "rehearsal_seconds": round(time.perf_counter() - start, 3),
                "rollback_ref": rollback_ref,
                "rto_target_seconds": 900,
                "rpo_target_hours": 24,
                "scope": "Local disposable synthetic databases. Previous application code against expanded schema, not cloud traffic cutover. No hosted RTO/RPO certification.",
            }
            report["passed"] &= restore_seconds < 900
            output.write_text(json.dumps(report, indent=2) + "\n")
            return report
    finally:
        for engine in engines:
            engine.dispose()
        with admin.connect() as conn:
            for name in created:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--container", help="Local PostgreSQL container for matching pg_dump/pg_restore"
    )
    parser.add_argument("--rollback-ref", default="3b04a8ee515c19213662059dd951c97fa0589af9")
    parser.add_argument("--output", type=Path, default=Path("evals/reports/operations.json"))
    args = parser.parse_args()
    settings = Settings(
        agent_provider="demo",
        embedding_provider="demo",
        app_env="development",
        cookie_secure=False,
        request_limits_enabled=False,
        spend_limits_enabled=False,
    )
    report = rehearse(settings, args.container, args.output, args.rollback_ref)
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("passed", "checks", "restore_seconds", "rehearsal_seconds")
            },
            indent=2,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
