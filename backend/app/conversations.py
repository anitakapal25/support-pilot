"""Durable chat orchestration: authorization, deduplication, locking, and recovery.

One dedicated PostgreSQL session owns both the advisory lock and all checkpoint/run
writes. Losing that connection prevents the old worker from publishing after another
worker acquires the lock. PostgresSaver's lock serializes our short transactions with
its checkpoint writer; no database transaction is held open during a model call.
"""

import hashlib
import logging
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.types.json import Jsonb
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent import build_agent
from app.auth import Principal
from app.checkpoints import checkpoint_connection, lock_key
from app.config import Settings
from app.handoffs import create_handoff, message_view, paused_response
from app.models import AgentRun, Conversation, Handoff, Message, utcnow
from app.orders import OrderUnavailable, SyntheticCommerceAdapter
from app.provider import DecisionProvider, ProviderUnavailable
from app.retrieval import CLARIFY, Retriever, validate_current_citations
from app.schemas import (
    ChatResponse,
    ConversationDetail,
    ConversationView,
    OrderStatus,
    RunView,
)
from app.telemetry import bind, emit

logger = logging.getLogger(__name__)
UNFINISHED = ("pending", "running", "retryable")


class BudgetExceeded(ProviderUnavailable):
    pass


def run_view(row: dict) -> RunView:
    return RunView(
        **{**row, **{key: str(row[key]) for key in ("id", "conversation_id", "request_id")}}
    )


def authorize(db: Session, customer: Principal, conversation_id: str) -> Conversation:
    conversation = (
        db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.customer_id == customer.id,
            )
        )
        if customer.role == "customer"
        else None
    )
    if conversation is None:
        raise HTTPException(404, "Conversation unavailable")
    return conversation


def create_conversation(engine: Engine, customer: Principal, request_id: str) -> ConversationView:
    if customer.role != "customer":
        raise HTTPException(403, "Customer account required")
    with Session(engine) as db, db.begin():
        db.execute(
            insert(Conversation)
            .values(
                id=request_id,
                customer_id=customer.id,
                created_at=utcnow(),
                next_sequence=1,
            )
            .on_conflict_do_nothing(index_elements=[Conversation.id])
        )
        conversation = authorize(db, customer, request_id)
        return ConversationView.model_validate(conversation, from_attributes=True)


def list_conversations(engine: Engine, customer: Principal) -> list[ConversationView]:
    with Session(engine) as db:
        conversations = db.scalars(
            select(Conversation)
            .where(
                Conversation.customer_id == customer.id,
            )
            .order_by(Conversation.created_at.desc(), Conversation.id)
            .limit(50)
        )
        return [
            ConversationView.model_validate(item, from_attributes=True) for item in conversations
        ]


def conversation_detail(
    engine: Engine, customer: Principal, conversation_id: str
) -> ConversationDetail:
    with Session(engine) as db:
        conversation = authorize(db, customer, conversation_id)
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        ).all()
        runs = db.scalars(
            select(AgentRun)
            .where(AgentRun.conversation_id == conversation_id)
            .order_by(AgentRun.user_sequence)
        ).all()
        return ConversationDetail(
            id=conversation.id,
            created_at=conversation.created_at,
            handoff=db.scalar(select(Handoff).where(Handoff.conversation_id == conversation_id)),
            messages=[message_view(db, message) for message in messages],
            runs=[RunView.model_validate(run, from_attributes=True) for run in runs],
        )


def read_run(engine: Engine, customer: Principal, conversation_id: str, request_id: str) -> RunView:
    with Session(engine) as db:
        authorize(db, customer, conversation_id)
        run = db.scalar(
            select(AgentRun).where(
                AgentRun.conversation_id == conversation_id, AgentRun.request_id == request_id
            )
        )
        if run is None:
            raise HTTPException(404, "Request unavailable")
        return RunView.model_validate(run, from_attributes=True)


class DurableTurn:
    def __init__(
        self, engine: Engine, customer: Principal, provider: DecisionProvider, settings: Settings
    ):
        self.engine, self.customer, self.provider, self.settings = (
            engine,
            customer,
            provider,
            settings,
        )

    @contextmanager
    def transaction(self):
        # The pinned PostgresSaver exposes the same lock used by its cursor/pipeline.
        # Sharing it avoids mixing a checkpoint pipeline with an application transaction.
        with self.saver.lock, self.connection.transaction():
            yield self.connection

    def guard(self) -> None:
        with self.transaction() as conn:
            conn.execute("SELECT 1")

    def reserve_model_call(self) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "UPDATE agent_runs SET provider_calls=provider_calls+1, updated_at=now() "
                "WHERE id=%s AND provider_calls < %s RETURNING id",
                (self.run_id, self.settings.provider_retries + 1),
            ).fetchone()
            if row is None:
                raise BudgetExceeded()

    def lookup_order(self, principal: Principal, order_reference: str) -> OrderStatus:
        arguments = {"order_reference": order_reference}
        with self.transaction() as conn:
            cached = conn.execute(
                "SELECT arguments,result FROM tool_executions WHERE run_id=%s AND tool_name='lookup_order'",
                (self.run_id,),
            ).fetchone()
            if cached:
                if cached["arguments"] != arguments:
                    raise BudgetExceeded()
                if cached["result"]["outcome"] == "unavailable":
                    raise OrderUnavailable()
                return OrderStatus.model_validate(cached["result"]["order"])
            row = conn.execute(
                "UPDATE agent_runs SET tool_calls=tool_calls+1 WHERE id=%s AND tool_calls<4 RETURNING id",
                (self.run_id,),
            ).fetchone()
            if row is None:
                raise BudgetExceeded()
        try:
            order = SyntheticCommerceAdapter(self.engine).lookup_order(principal, order_reference)
            result = {"outcome": "verified", "order": order.model_dump(mode="json")}
        except OrderUnavailable:
            result = {"outcome": "unavailable"}
        self.store_tool_result(arguments, result)
        emit("tool.result", action="lookup_order", outcome=result["outcome"], run_id=self.run_id)
        if result["outcome"] == "unavailable":
            raise OrderUnavailable()
        return OrderStatus.model_validate(result["order"])

    def store_tool_result(self, arguments: dict, result: dict) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tool_executions(run_id,tool_name,arguments,result) VALUES (%s,'lookup_order',%s,%s)",
                (self.run_id, Jsonb(arguments), Jsonb(result)),
            )

    def request_handoff(self, arguments: dict) -> dict:
        with self.transaction() as conn:
            cached = conn.execute(
                "SELECT arguments,result FROM tool_executions WHERE run_id=%s AND tool_name='request_handoff'",
                (self.run_id,),
            ).fetchone()
            if cached:
                if cached["arguments"] != arguments:
                    raise BudgetExceeded()
                return cached["result"]
            reserved = conn.execute(
                "UPDATE agent_runs SET tool_calls=tool_calls+1 WHERE id=%s AND tool_calls<4 RETURNING id",
                (self.run_id,),
            ).fetchone()
            if not reserved:
                raise BudgetExceeded()
            handoff = create_handoff(conn, self.customer, self.conversation_id, arguments)
            response = paused_response(self.run_id, handoff)
            emit(
                "tool.result",
                action="request_handoff",
                outcome=response.handoff.status,
                resource_id=response.handoff.id,
                run_id=self.run_id,
            )
            result = {
                "outcome": "handoff",
                "answer": response.message,
                "handoff": response.handoff.model_dump(mode="json"),
            }
            conn.execute(
                "INSERT INTO tool_executions(run_id,tool_name,arguments,result) VALUES (%s,'request_handoff',%s,%s)",
                (self.run_id, Jsonb(arguments), Jsonb(result)),
            )
            return result

    def retrieve(self, query: str, timeout: float) -> dict:
        with self.transaction() as conn:
            reserved = conn.execute(
                "UPDATE agent_runs SET tool_calls=tool_calls+1 WHERE id=%s AND tool_calls<4 RETURNING id",
                (self.run_id,),
            ).fetchone()
            if not reserved:
                raise BudgetExceeded()
        return Retriever(self.engine, self.settings).answer(query, timeout)

    def accept(self, conversation_id: str, request_id: str, message: str, digest: str) -> dict:
        with self.transaction() as conn:
            conn.execute("SELECT id FROM conversations WHERE id=%s FOR UPDATE", (conversation_id,))
            existing = conn.execute(
                "SELECT * FROM agent_runs WHERE conversation_id=%s AND request_id=%s",
                (conversation_id, request_id),
            ).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise HTTPException(409, "Request ID was already used with different text")
                return existing
            handoff = conn.execute(
                "SELECT status FROM handoffs WHERE conversation_id=%s", (conversation_id,)
            ).fetchone()
            if handoff and handoff["status"] == "resolved":
                raise HTTPException(409, "This handoff is resolved. Start a new chat.")
            if conn.execute(
                "SELECT id FROM agent_runs WHERE conversation_id=%s AND status IN ('pending','running','retryable')",
                (conversation_id,),
            ).fetchone():
                raise HTTPException(
                    409, "Resume the unfinished request before sending another message"
                )
            conversation = conn.execute(
                "SELECT next_sequence FROM conversations WHERE id=%s", (conversation_id,)
            ).fetchone()
            sequence = conversation["next_sequence"]
            turn_count = conn.execute(
                "SELECT count(*) AS count FROM agent_runs WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchone()["count"]
            if turn_count >= self.settings.conversation_max_turns:
                raise HTTPException(409, "This conversation is full. Start a new chat.")
            history = conn.execute(
                "SELECT role,content,result FROM messages WHERE conversation_id=%s ORDER BY sequence DESC LIMIT 6",
                (conversation_id,),
            ).fetchall()
            last = next((item for item in history if item["role"] == "assistant"), None)
            last_order = (last["result"] or {}).get("order") if last else None
            context = {
                "messages": [
                    {"role": item["role"], "content": item["content"][:1000]}
                    for item in reversed(history)
                ],
                "last_order": last_order,
            }
            run_id = str(uuid4())
            row = conn.execute(
                "INSERT INTO agent_runs(id,conversation_id,request_id,payload_hash,user_sequence,status,attempts,provider_calls,tool_calls,context,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,'pending',0,0,0,%s,now(),now()) RETURNING *",
                (run_id, conversation_id, request_id, digest, sequence, Jsonb(context)),
            ).fetchone()
            conn.execute(
                "INSERT INTO messages(id,conversation_id,run_id,sequence,role,content,created_at) VALUES (%s,%s,%s,%s,'user',%s,now())",
                (str(uuid4()), conversation_id, run_id, sequence, message),
            )
            conn.execute(
                "UPDATE conversations SET next_sequence=next_sequence+2 WHERE id=%s",
                (conversation_id,),
            )
            return row

    def finalize(
        self,
        run: dict,
        response: ChatResponse,
        status: str = "completed",
        error_code: str | None = None,
    ) -> RunView:
        with self.transaction() as conn:
            conn.execute(
                "SELECT id FROM conversations WHERE id=%s FOR UPDATE", (run["conversation_id"],)
            )
            handoff = conn.execute(
                "SELECT * FROM handoffs WHERE conversation_id=%s", (run["conversation_id"],)
            ).fetchone()
            if handoff:
                response = paused_response(str(run["request_id"]), handoff)
            if response.citations and not validate_current_citations(
                conn, [item.model_dump(mode="json") for item in response.citations]
            ):
                emit("citation.validation", outcome="invalid_citations", run_id=self.run_id)
                prefix = (
                    f"Order {response.order.order_reference} is {response.order.status}.\n\n"
                    if response.order
                    else ""
                )
                response = response.model_copy(
                    update={
                        "citations": [],
                        "outcome": "clarification",
                        "message": prefix + CLARIFY,
                    }
                )
            # Publication is atomic: one assistant message and the canonical result together.
            conn.execute(
                "INSERT INTO messages(id,conversation_id,run_id,sequence,role,content,result,created_at) "
                "VALUES (%s,%s,%s,%s,'assistant',%s,%s,now()) ON CONFLICT (run_id,role) DO NOTHING",
                (
                    str(uuid4()),
                    run["conversation_id"],
                    self.run_id,
                    run["user_sequence"] + 1,
                    response.message,
                    Jsonb(response.model_dump(mode="json")),
                ),
            )
            row = conn.execute(
                "UPDATE agent_runs SET status=%s,response=%s,error_code=%s,updated_at=now() WHERE id=%s RETURNING *",
                (status, Jsonb(response.model_dump(mode="json")), error_code, self.run_id),
            ).fetchone()
            emit(
                "agent.completed",
                run_id=self.run_id,
                outcome=response.outcome,
                source_ids=[item.id for item in response.citations],
            )
            return run_view(row)

    def failure(self, run: dict, exhausted: bool) -> RunView:
        emit("agent.failure", run_id=self.run_id, outcome="failed" if exhausted else "retryable")
        with self.transaction() as conn:
            handoff = conn.execute(
                "SELECT * FROM handoffs WHERE conversation_id=%s", (run["conversation_id"],)
            ).fetchone()
        if handoff:
            return self.finalize(run, paused_response(str(run["request_id"]), handoff))
        if exhausted:
            with self.transaction() as conn:
                create_handoff(
                    conn,
                    self.customer,
                    str(run["conversation_id"]),
                    {
                        "reason": "dependency_failure",
                        "summary": "Automated assistance exhausted its retry budget. Review the conversation and attempted actions.",
                    },
                )
            response = ChatResponse(
                request_id=str(run["request_id"]),
                outcome="error",
                message="This request could not be completed within its retry budget. You can send a new message.",
            )
            return self.finalize(run, response, "failed", "retry_budget_exhausted")
        with self.transaction() as conn:
            row = conn.execute(
                "UPDATE agent_runs SET status='retryable',error_code='temporarily_unavailable',updated_at=now() WHERE id=%s RETURNING *",
                (self.run_id,),
            ).fetchone()
            return run_view(row)

    def submit(self, conversation_id: str, request_id: str, message: str) -> RunView:
        with Session(self.engine) as db:
            authorize(db, self.customer, conversation_id)
        bind(actor_id=self.customer.id, conversation_id=conversation_id)
        digest = hashlib.sha256(message.encode()).hexdigest()
        # Closing this unpooled connection releases the session advisory lock even on a crash.
        with checkpoint_connection(self.engine) as self.connection:
            self.saver = PostgresSaver(self.connection)
            locked = self.connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (lock_key(conversation_id),)
            ).fetchone()["acquired"]
            if not locked:
                row = self.connection.execute(
                    "SELECT * FROM agent_runs WHERE conversation_id=%s AND request_id=%s",
                    (conversation_id, request_id),
                ).fetchone()
                if row and row["payload_hash"] == digest:
                    return run_view(row)
                raise HTTPException(
                    409,
                    "Conversation is busy or the request ID conflicts. Retry the same request shortly.",
                )
            run = self.accept(conversation_id, request_id, message, digest)
            emit("agent.accepted", run_id=str(run["id"]), outcome=run["status"])
            if run["status"] in ("completed", "failed"):
                return run_view(run)
            self.run_id = str(run["id"])
            self.conversation_id = conversation_id
            with self.transaction() as conn:
                handoff = conn.execute(
                    "SELECT * FROM handoffs WHERE conversation_id=%s", (conversation_id,)
                ).fetchone()
            if handoff:
                return self.finalize(run, paused_response(request_id, handoff))
            config = {"configurable": {"thread_id": self.run_id}, "recursion_limit": 8}
            graph = build_agent(
                self.customer,
                self,
                self.provider,
                self.settings,
                checkpointer=self.saver,
                retrieve=self.retrieve,
                handoff=self.request_handoff,
                before_model=self.reserve_model_call,
                guard=self.guard,
            )
            # A checkpoint per turn avoids stale fields contaminating the next turn. Context
            # is explicitly reconstructed from authorized conversation messages at acceptance.
            snapshot = graph.get_state(config)
            if snapshot.values and not snapshot.next and snapshot.values.get("answer"):
                state = snapshot.values  # Crash after graph completion but before publication.
            else:
                if run["attempts"] >= self.settings.run_attempts:
                    return self.failure(run, True)
                with self.transaction() as conn:
                    run = conn.execute(
                        "UPDATE agent_runs SET attempts=attempts+1,status='running',error_code=NULL,updated_at=now() WHERE id=%s RETURNING *",
                        (self.run_id,),
                    ).fetchone()
                try:
                    initial = (
                        None if snapshot.values else {"message": message, "context": run["context"]}
                    )
                    state = graph.invoke(initial, config, durability="sync")
                except (ProviderUnavailable, SQLAlchemyError, psycopg.Error) as exc:
                    # If PostgreSQL is unavailable, this write also fails: return 503, and
                    # the committed run remains recoverable. Never invent a saved response.
                    return self.failure(
                        run,
                        isinstance(exc, BudgetExceeded)
                        or run["attempts"] >= self.settings.run_attempts,
                    )
            response = ChatResponse(
                request_id=request_id,
                outcome=state["outcome"],
                message=state["answer"],
                order=state.get("order"),
                citations=state.get("citations", []),
                handoff=state.get("handoff"),
            )
            result = self.finalize(run, response)
            logger.info(
                "durable_turn_completed run_id=%s outcome=%s", self.run_id, response.outcome
            )
            return result
