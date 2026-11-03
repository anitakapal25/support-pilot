"""Portable PostgreSQL custom-format backups; restore only into an empty target."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import make_engine


def pg_environment(settings):
    url = make_url(settings.database_url.get_secret_value())
    env = {
        **os.environ,
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password or "",
        "PGDATABASE": url.database or "",
        "PGCONNECT_TIMEOUT": "5",
    }
    for key, target in [
        ("sslmode", "PGSSLMODE"),
        ("sslrootcert", "PGSSLROOTCERT"),
        ("options", "PGOPTIONS"),
    ]:
        if key in url.query:
            env[target] = str(url.query[key])
    return env


def run_pg(command, settings, *, data=None, container=None):
    env = pg_environment(settings)
    if container:
        # Run matching-major client tools inside the local database container.
        env.update(PGHOST="127.0.0.1", PGPORT="5432")
        command = [
            "docker",
            "exec",
            "-i",
            *[
                arg
                for key in (
                    "PGHOST",
                    "PGPORT",
                    "PGUSER",
                    "PGPASSWORD",
                    "PGDATABASE",
                    "PGCONNECT_TIMEOUT",
                )
                for arg in ("-e", key)
            ],
            container,
            *command,
        ]
    result = subprocess.run(command, env=env, input=data, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            "PostgreSQL backup command failed; check connectivity, tool version and permissions"
        )
    return result.stdout


def backup(settings, path, container=None):
    path = Path(path)
    if path.exists():
        raise ValueError("Refusing to overwrite an existing backup")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    path.write_bytes(
        run_pg(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl"], settings, container=container
        )
    )
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"sha256": checksum, "bytes": path.stat().st_size, "format": "postgres-custom"}
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def restore(settings, path, container=None):
    path = Path(path)
    manifest = json.loads(path.with_suffix(path.suffix + ".json").read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Backup checksum mismatch")
    engine = make_engine(settings)
    try:
        with engine.connect() as conn:
            if conn.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"
                )
            ):
                raise ValueError("Restore target must be an empty, separate database")
        run_pg(
            [
                "pg_restore",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
                "--dbname",
                make_url(settings.database_url.get_secret_value()).database,
            ],
            settings,
            data=path.read_bytes(),
            container=container,
        )
    finally:
        engine.dispose()
    return {"restored": True, "sha256": manifest["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "restore"])
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--container", help="Optional local PostgreSQL container with matching client tools"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            (backup if args.action == "create" else restore)(Settings(), args.path, args.container)
        )
    )


if __name__ == "__main__":
    main()
