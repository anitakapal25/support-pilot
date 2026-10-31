"""Subprocess used by the crash recovery integration test; no application test hooks."""

import os
import sys

from app.auth import Principal
from app.config import Settings
from app.conversations import DurableTurn
from app.provider import DemoProvider
from sqlalchemy import create_engine

conversation_id, request_id, customer_id, mode = sys.argv[1:]
engine = create_engine(os.environ["RECOVERY_DATABASE_URL"])
customer = Principal(customer_id, "alice@example.test", "Alice", "customer", "unused")
original = DurableTurn.store_tool_result
if mode == "crash":

    def crash(self, arguments, result):
        original(self, arguments, result)
        os._exit(17)

    DurableTurn.store_tool_result = crash
result = DurableTurn(
    engine, customer, DemoProvider(), Settings(_env_file=None, agent_provider="demo")
).submit(conversation_id, request_id, "SP-1001")
assert result.status == "completed"
engine.dispose()
