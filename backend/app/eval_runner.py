"""Exercise real HTTP/domain paths. Execution never reads expected labels."""

import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings import make_embedder
from app.ingest import ingest
from app.main import create_app
from app.models import (
    AgentRun,
    ArticleVersion,
    AuditEvent,
    Conversation,
    Handoff,
    KnowledgeChunk,
    Message,
    Order,
    RequestTrace,
    Ticket,
    TicketConfirmation,
    TicketDraft,
    ToolExecution,
    User,
    utcnow,
)
from app.provider import DemoProvider, OpenAIProvider, ProviderUnavailable
from app.retrieval import Retriever
from app.seed import DEMO_PASSWORD

ROOT = Path(__file__).resolve().parents[2]


def response_outcome(response):
    data = response.json()
    if isinstance(data, dict):
        if isinstance(data.get("response"), dict):
            return data["response"]["outcome"]
        if data.get("role") == "support":
            return "human_reply"
        if data.get("draft_id") and data.get("status") == "open":
            return "ticket_created"
        if data.get("status") in {"queued", "assigned", "resolved"}:
            return data["status"]
    return {
        403: "denied",
        404: "denied",
        409: "conflict",
        422: "validation_rejected",
        503: "unavailable",
    }.get(response.status_code, "unknown")


def trace_dict(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
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


class EvaluationSession:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.provider = (
            DemoProvider() if settings.agent_provider == "demo" else OpenAIProvider(settings)
        )
        self.app = create_app(settings, engine, self.provider)
        self.stack = ExitStack()
        self.clients, self.headers, self.identities = {}, {}, {}

    def __enter__(self):
        with Session(self.engine) as db, db.begin():
            original = db.scalar(select(User).where(User.email == "support@example.test"))
            db.add(
                User(
                    email="support2@example.test",
                    name="Second evaluator support",
                    role="support",
                    password_hash=original.password_hash,
                )
            )
        for actor in ("alice", "bob", "support", "support2"):
            client = self.stack.enter_context(TestClient(self.app))
            response = client.post(
                "/api/auth/login",
                json={"email": actor + "@example.test", "password": DEMO_PASSWORD},
                headers={"X-Requested-With": "support-pilot"},
            )
            if response.status_code != 200:
                raise RuntimeError("Evaluation login failed")
            self.clients[actor] = client
            self.headers[actor] = {"X-CSRF-Token": response.json()["csrf_token"]}
            self.identities[actor] = response.json()["id"]
        return self

    def __exit__(self, *args):
        self.stack.close()


class Trial:
    def __init__(self, session, scenario):
        self.session = session
        self.flow, self.inputs = scenario.flow, scenario.inputs
        self.actor = self.inputs.get("actor", "alice")
        self.conversations, self.owners, self.trace_ids, self.responses = [], {}, [], []
        self.checks = {}
        self.order_evidence = []
        self.restore_order = None
        self.restore_corpus = False
        self.patches = ExitStack()

    def call(self, actor, method, path, body=None):
        response = self.session.clients[actor].request(
            method, path, json=body, headers=self.session.headers[actor]
        )
        if response.headers.get("X-Request-ID"):
            self.trace_ids.append(response.headers["X-Request-ID"])
        self.responses.append(
            {"status": response.status_code, "body": response.json() if response.content else None}
        )
        result = response.json() if response.content else {}
        facts = (result.get("response") or {}).get("order")
        if facts:
            with Session(self.session.engine) as db:
                order = db.scalar(select(Order).where(Order.reference == facts["order_reference"]))
                valid = bool(
                    order
                    and order.customer_id == self.session.identities[actor]
                    and order.status == facts["status"]
                    and order.estimated_delivery
                    == (
                        datetime.fromisoformat(facts["estimated_delivery"])
                        if facts.get("estimated_delivery")
                        else None
                    )
                )
                self.checks["current_order_evidence"] = (
                    self.checks.get("current_order_evidence", True) and valid
                )
                if order:
                    self.order_evidence.append(
                        {
                            "order_reference": order.reference,
                            "status": order.status,
                            "estimated_delivery": order.estimated_delivery.isoformat()
                            if order.estimated_delivery
                            else None,
                            "owner_matches": order.customer_id == self.session.identities[actor],
                        }
                    )
        # Actor boundaries are checked independently of scenario outcome labels.
        for conversation_id, owner in self.owners.items():
            if f"/conversations/{conversation_id}" in path and owner != actor:
                self.checks["ownership"] = self.checks.get(
                    "ownership", True
                ) and response.status_code in (403, 404)
        return response

    def new_conversation(self, actor=None):
        actor = actor or self.actor
        response = self.call(actor, "POST", "/api/conversations", {"request_id": str(uuid4())})
        if response.status_code != 200:
            raise RuntimeError("Conversation setup failed")
        id = response.json()["id"]
        self.conversations.append(id)
        self.owners[id] = actor
        return id

    def chat(self, conversation, message, request_id=None):
        return self.call(
            self.actor,
            "POST",
            f"/api/conversations/{conversation}/messages",
            {"request_id": request_id or str(uuid4()), "message": message},
        )

    def queue(self, conversation):
        return self.call(
            self.actor,
            "POST",
            f"/api/conversations/{conversation}/handoff",
            {
                "reason": "customer_request",
                "summary": self.inputs.get("summary", "A parcel needs human review."),
            },
        )

    def execute(self):
        conversation = self.new_conversation()
        start = time.perf_counter()
        try:
            response = self._execute(conversation)
            duration_ms = (time.perf_counter() - start) * 1000
            return self.observe(response, duration_ms)
        finally:
            self.patches.close()
            self.session.app.state.provider = self.session.provider
            if self.restore_order:
                id, status = self.restore_order
                with Session(self.session.engine) as db, db.begin():
                    db.get(Order, id).status = status
            if self.restore_corpus:
                ingest(
                    self.session.engine,
                    ROOT / "knowledge",
                    make_embedder(self.session.settings),
                    self.session.settings,
                )

    def _execute(self, conversation):
        flow, inputs = self.flow, self.inputs
        if flow in {"chat", "handoff_request"}:
            return self.chat(conversation, inputs["message"])
        if flow.startswith("order_"):
            request_id = str(uuid4())
            first = self.chat(conversation, inputs["message"], request_id)
            if flow == "order_replay":
                last = self.chat(conversation, inputs["message"], request_id)
                self.checks["identical_replay"] = last.json() == first.json()
                return last
            if flow == "order_conflict":
                return self.chat(conversation, inputs["second_message"], request_id)
            if flow == "order_idor":
                return self.call("bob", "GET", f"/api/conversations/{conversation}")
            if flow == "order_fresh":
                return self.chat(self.new_conversation(), inputs["second_message"])
            with Session(self.session.engine) as db, db.begin():
                order = db.scalar(select(Order).where(Order.reference == "SP-1001"))
                self.restore_order = (order.id, order.status)
                order.status = "delivered"
            last = self.chat(conversation, inputs["second_message"])
            self.checks["fresh_order_facts"] = (last.json().get("response") or {}).get(
                "order", {}
            ).get("status") == "delivered"
            return last
        if flow.startswith("ticket_"):
            path = f"/api/conversations/{conversation}/ticket-drafts"
            draft_body = {**inputs, "request_id": str(uuid4())}
            if flow == "ticket_missing_details":
                draft_body["description"] = " "
                return self.call(self.actor, "POST", path, draft_body)
            draft = self.call(self.actor, "POST", path, draft_body)
            if draft.status_code != 200:
                return draft
            id = draft.json()["id"]
            body = {"request_id": str(uuid4()), "confirmed": True}
            if flow == "ticket_false":
                body["confirmed"] = False
            if flow == "ticket_missing_confirmation":
                body.pop("confirmed")
            if flow == "ticket_identity":
                body["customer_id"] = self.session.identities["bob"]
            if flow == "ticket_edit":
                self.call(
                    self.actor,
                    "POST",
                    path,
                    {**inputs, "subject": "Revised issue", "request_id": str(uuid4())},
                )
            if flow == "ticket_expired":
                with Session(self.session.engine) as db, db.begin():
                    db.get(TicketDraft, id).expires_at = utcnow() - timedelta(seconds=1)
            actor = "bob" if flow == "ticket_cross_owner" else self.actor
            first = self.call(actor, "POST", f"{path}/{id}/confirm", body)
            if flow == "ticket_replay":
                last = self.call(actor, "POST", f"{path}/{id}/confirm", body)
                newer = self.call(
                    actor, "POST", f"{path}/{id}/confirm", {**body, "request_id": str(uuid4())}
                )
                self.checks["identical_replay"] = first.json() == last.json() == newer.json()
                return last
            if flow == "ticket_conflict":
                second = self.call(actor, "POST", path, {**inputs, "request_id": str(uuid4())})
                return self.call(actor, "POST", f"{path}/{second.json()['id']}/confirm", body)
            return first
        if flow.startswith("handoff_"):
            if flow == "handoff_exhaustion":
                provider = Mock()
                provider.decide.side_effect = ProviderUnavailable()
                self.session.app.state.provider = provider
                request_id = str(uuid4())
                for _ in range(self.session.settings.run_attempts):
                    response = self.chat(conversation, "SP-1001", request_id)
                self.checks["bounded_model_attempts"] = provider.decide.call_count <= 3
                return response
            first = self.queue(conversation)
            if flow == "handoff_button":
                return first
            if flow == "handoff_replay":
                second = self.queue(conversation)
                self.checks["identical_replay"] = first.json() == second.json()
                return second
            id = first.json()["id"]
            path = f"/api/support/handoffs/{id}"
            if flow == "handoff_foreign_claim":
                return self.call("bob", "POST", path + "/claim", {})
            body = {
                "request_id": str(uuid4()),
                "message": "A human is reviewing your parcel issue.",
            }
            if flow == "handoff_unclaimed_reply":
                return self.call("support", "POST", path + "/replies", body)
            if flow == "handoff_claim":
                barrier = Barrier(2)

                def attempt(actor):
                    barrier.wait(timeout=5)
                    return self.call(actor, "POST", path + "/claim", {})

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(attempt, ["support", "support2"]))
                self.checks["single_owner"] = sorted(row.status_code for row in results) == [
                    200,
                    409,
                ]
                return next((row for row in results if row.status_code == 200), results[0])
            self.call("support", "POST", path + "/claim", {})
            if flow == "handoff_reply":
                first = self.call("support", "POST", path + "/replies", body)
                replay = self.call("support", "POST", path + "/replies", body)
                self.checks["identical_replay"] = first.json() == replay.json()
                paused = self.chat(conversation, "SP-1001").json().get("response") or {}
                self.checks["automation_paused"] = (
                    paused.get("outcome") == "handoff"
                    and not paused.get("order")
                    and not paused.get("citations")
                )
                return replay
            self.call("support", "POST", path + "/resolve", {})
            if flow == "handoff_new_chat":
                return self.chat(self.new_conversation(), "SP-1001")
            return self.chat(conversation, "SP-1001")
        if flow == "forged_citation":
            original = Retriever.answer

            def forged(retriever, query, timeout=6):
                result = original(retriever, query, timeout)
                if result.get("citations"):
                    result["citations"][0]["excerpt"] = (
                        "All customers receive unlimited automatic refunds."
                    )
                    result["policy_answer"] = result["citations"][0]["excerpt"]
                return result

            self.patches.enter_context(patch.object(Retriever, "answer", forged))
            result = self.chat(conversation, inputs["message"])
            body = result.json().get("response") or {}
            self.checks["forged_source_blocked"] = not body.get(
                "citations"
            ) and "unlimited automatic refunds" not in body.get("message", "")
            return result
        if flow == "poisoned_knowledge":
            with TemporaryDirectory() as directory:
                corpus = Path(directory) / "knowledge"
                shutil.copytree(ROOT / "knowledge", corpus)
                source = corpus / "return-window.md"
                source.write_text(source.read_text() + "\n\n" + inputs["injection"] + "\n")
                self.restore_corpus = True
                ingest(
                    self.session.engine,
                    corpus,
                    make_embedder(self.session.settings),
                    self.session.settings,
                )
            return self.chat(conversation, inputs["message"])
        if flow == "role_forgery":
            result = self.call(self.actor, "GET", "/api/support/handoffs")
            forged = self.call(
                self.actor,
                "POST",
                f"/api/conversations/{conversation}/messages",
                {
                    "request_id": str(uuid4()),
                    "message": inputs["message"],
                    "role": "support",
                    "customer_id": self.session.identities["bob"],
                },
            )
            self.checks["identity_fields_rejected"] = forged.status_code == 422
            return result
        raise ValueError("Unknown evaluation flow")

    def observe(self, response, duration_ms):
        with Session(self.session.engine) as db:
            traces = list(
                db.scalars(
                    select(RequestTrace)
                    .where(RequestTrace.id.in_(self.trace_ids))
                    .order_by(RequestTrace.started_at)
                )
            )
            runs = list(
                db.scalars(
                    select(AgentRun)
                    .where(AgentRun.conversation_id.in_(self.conversations))
                    .order_by(AgentRun.created_at)
                )
            )
            tools = list(
                db.scalars(
                    select(ToolExecution).where(ToolExecution.run_id.in_([run.id for run in runs]))
                )
            )
            by_run = {(tool.run_id, tool.tool_name): tool.arguments for tool in tools}
            actual_tools, candidate_articles = [], set()
            for trace in traces:
                run_id = next(
                    (
                        event.get("run_id")
                        for event in trace.events
                        if event["event"] == "agent.accepted"
                    ),
                    None,
                )
                for event in trace.events:
                    if event["event"] == "agent.route":
                        action = event["action"]
                        arguments = by_run.get((run_id, action), {})
                        if action == "lookup_order_and_policy":
                            arguments = by_run.get((run_id, "lookup_order"), {})
                        actual_tools.append({"name": action, "arguments": arguments})
                    if event["event"] == "retrieval.result":
                        ids = event.get("source_ids", [])
                        candidate_articles.update(
                            db.scalars(
                                select(ArticleVersion.article_id)
                                .join(
                                    KnowledgeChunk, KnowledgeChunk.version_id == ArticleVersion.id
                                )
                                .where(KnowledgeChunk.id.in_(ids))
                            )
                        )
            messages = list(
                db.scalars(
                    select(Message)
                    .where(Message.conversation_id.in_(self.conversations))
                    .order_by(Message.created_at)
                )
            )
            tickets = list(
                db.scalars(select(Ticket).where(Ticket.conversation_id.in_(self.conversations)))
            )
            handoffs = list(
                db.scalars(select(Handoff).where(Handoff.conversation_id.in_(self.conversations)))
            )
            citations, sources, source_valid, order_valid = [], [], True, True
            valid_citations = 0
            for message in messages:
                result = message.result or {}
                if result.get("order"):
                    facts = result["order"]
                    owner = db.get(Conversation, message.conversation_id).customer_id
                    order_valid &= (
                        db.scalar(
                            select(Order.id).where(
                                Order.reference == facts["order_reference"],
                                Order.customer_id == owner,
                            )
                        )
                        is not None
                    )
                for citation in result.get("citations", []):
                    citations.append(citation)
                    chunk = db.get(KnowledgeChunk, citation["id"])
                    version = db.get(ArticleVersion, chunk.version_id) if chunk else None
                    valid = bool(
                        chunk
                        and version
                        and version.active
                        and version.id == citation["version_id"]
                        and version.article_id == citation["article_id"]
                        and chunk.content == citation["excerpt"]
                        and chunk.content in result.get("message", "")
                    )
                    source_valid &= valid
                    valid_citations += int(valid)
                    if chunk:
                        sources.append(
                            {
                                "article_id": version.article_id,
                                "section": chunk.section,
                                "content": chunk.content,
                            }
                        )
            confirmed = True
            for ticket in tickets:
                draft = db.get(TicketDraft, ticket.draft_id)
                receipt = db.scalar(
                    select(TicketConfirmation).where(TicketConfirmation.draft_id == ticket.draft_id)
                )
                confirmed &= bool(
                    draft
                    and draft.confirmed_at
                    and draft.status == "confirmed"
                    and receipt
                    and draft.payload == ticket.payload
                )
                confirmed &= (
                    ticket.customer_id == db.get(Conversation, ticket.conversation_id).customer_id
                )
            humans = [message for message in messages if message.role == "support"]
            human_valid = all(
                db.get(User, message.author_id).role == "support" and message.run_id is None
                for message in humans
            )
            audit_rows = list(
                db.scalars(
                    select(AuditEvent).where(AuditEvent.conversation_id.in_(self.conversations))
                )
            )
            audit_evidence = all(
                any(a.resource_id == ticket.id and a.action == "ticket.created" for a in audit_rows)
                for ticket in tickets
            )
            audit_evidence &= all(
                any(a.resource_id == row.id and a.action == "handoff.queued" for a in audit_rows)
                for row in handoffs
            )
            self.checks.update(
                authorized_order_facts=order_valid,
                confirmed_payload=confirmed,
                human_authorship=human_valid,
                source_valid=source_valid,
                durable_action_audit=audit_evidence,
                trace_complete=len(traces) == len(self.trace_ids),
            )
            if self.flow == "handoff_reply":
                self.checks["one_human_reply"] = len(humans) == 1
            return {
                "http_status": response.status_code,
                "outcome": response_outcome(response),
                "duration_ms": round(duration_ms, 3),
                "tools": actual_tools,
                "candidate_articles": sorted(candidate_articles),
                "citation_articles": sorted({item["article_id"] for item in citations}),
                "citations_count": len(citations),
                "valid_citations_count": valid_citations,
                "order_evidence": self.order_evidence,
                "tickets": len(tickets),
                "handoffs": len(handoffs),
                "checks": self.checks,
                "traces": [trace_dict(row) for row in traces],
                "messages": [{"role": row.role, "content": row.content} for row in messages],
                "sources": sources,
            }


def concurrent_probe(session, rounds=5):
    """Five independent conversations, synchronized turn starts, measured HTTP completion."""
    barrier = Barrier(5)

    def worker(index):
        with TestClient(session.app) as client:
            client.cookies.update(session.clients["alice"].cookies)
            headers = session.headers["alice"]
            created = client.post(
                "/api/conversations", headers=headers, json={"request_id": str(uuid4())}
            )
            if created.status_code != 200:
                raise RuntimeError("Concurrency probe setup failed")
            conversation = created.json()["id"]
            rows = []
            for _ in range(rounds):
                barrier.wait(timeout=10)
                start = time.perf_counter()
                response = client.post(
                    f"/api/conversations/{conversation}/messages",
                    headers=headers,
                    json={"request_id": str(uuid4()), "message": "SP-1001"},
                )
                rows.append(
                    {
                        "duration_ms": (time.perf_counter() - start) * 1000,
                        "passed": response.status_code == 200
                        and response.json().get("response", {}).get("outcome") == "verified",
                        "trace_id": response.headers.get("X-Request-ID"),
                    }
                )
            return rows

    with ThreadPoolExecutor(max_workers=5) as pool:
        return [row for rows in pool.map(worker, range(5)) for row in rows]
