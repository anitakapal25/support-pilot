"""PostgresSaver connections also own the conversation lock. Never pool these sessions."""

import hashlib

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import Engine

CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def lock_key(conversation_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"support-pilot:{conversation_id}".encode()).digest()[:8], "big", signed=True
    )


def checkpoint_connection(engine: Engine):
    url = engine.url.set(drivername="postgresql")
    # Preserve the search_path in test URLs. Never log the unmasked connection URL.
    options = str(url.query.get("options", "")) + " -c statement_timeout=5000 -c lock_timeout=2000"
    return psycopg.connect(
        url.render_as_string(hide_password=False),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        connect_timeout=3,
        options=options,
    )


def setup_checkpoints(engine: Engine) -> None:
    # This is a deployment/setup operation, not an API startup side effect.
    with checkpoint_connection(engine) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (lock_key("checkpoint-setup"),))
        PostgresSaver(connection).setup()
