# Decision 0002: authenticated, bounded order-status slice

Status: implemented for milestone 1, 2026-10-28.

## Problem and decision

The assistant must report live order facts without allowing user or model text to
choose another customer's identity. Use opaque server sessions and one SQL-backed
commerce adapter that enforces customer ownership. Both HTTP and LangGraph use the
same adapter. Model-visible lookup arguments contain only an order reference.

The graph routes, optionally reads one order, and constructs a validated response.
Final wording and the order card come directly from the authorized tool result. A
second model call adds no useful capability for this narrow slice. Routing can use
OpenAI or an explicitly selected development-only deterministic demo router. Demo
mode is never an automatic fallback and is rejected in production configuration.

## Trust and lifecycle

Passwords are Argon2id hashes. Session tokens are cryptographically random, sent only
in HTTP-only cookies, and stored as SHA-256 hashes. CSRF tokens are separate random
values stored with the session and returned to the same-origin client. Every unsafe
authenticated request checks that token and any supplied Origin. Login requires a
custom header and checks Origin before password verification. Login attempts are
limited with atomic PostgreSQL counters by account and client IP. The API does not
trust forwarded IP headers; configure trusted proxies deliberately when deploying.

Queries combine order reference and authenticated customer ID. Missing and forbidden
orders return the same failure. A database constraint limits stored order states.
Pydantic validates tool arguments and responses, independently of strict model schemas.
Support-role identities cannot use the customer API.

A fixed, acyclic LangGraph replaces an open-ended agent loop. At most one tool read,
three total model attempts, 256 output tokens, a bounded input string, and a 30-second
graph budget keep execution controlled. Async HTTP cancellation bounds each provider
attempt; synchronous SQL uses thread-pooled endpoints and bounded connections/queries.
Only structured request IDs and outcomes are logged. Free-form model reasoning is not
persisted or returned. Application-owned telemetry is expanded in later milestones.

## Tradeoffs and limitations

Login sessions persist, but conversation messages and graph state do not. No
checkpointer, conversation table, request idempotency, or replay system is added here.
Those are milestone 2. A response request ID is for correlation, not deduplication.

The initial adapter reads synthetic PostgreSQL orders; a real commerce adapter must
preserve these authorization semantics. The OpenAI routing contract is tested with
SDK mocks; production model quality requires live labeled evaluations. No live OpenAI
success is claimed without an API-key-backed run.

Schema changes use Alembic. Seed data is explicit, development-only, and repeatable.
There is no automatic demo login or customer selector in the product UI.

## References checked

- https://developers.openai.com/api/docs/guides/function-calling
- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://alembic.sqlalchemy.org/en/latest/tutorial.html
- https://fastapi.tiangolo.com/async/
