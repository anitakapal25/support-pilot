# Delivery and learning roadmap

Current status: milestone 7 operational controls and the hosted-demo profile are
implemented. Local verification on 2026-11-10: 90 backend tests, 14 desktop/mobile browser
tests and a container cold start on an empty database passed. The Render free-tier deployment,
live-provider evaluation and independent human review are still to be performed; none of
those results has been measured. Complete the milestone 7 checkpoint and the human review
before calling the demo validated.

Implement one milestone at a time. Each ends with tests, a code walkthrough, an
exercise, an architecture note, and the learner explaining the critical code path.
Do not advance automatically past a learning checkpoint.

0. Foundations: FastAPI, typed contracts, PostgreSQL, Docker, health checks.
1. Authorized order vertical slice: migrations, seeded users/orders, sessions, Argon2,
   CSRF, domain ownership checks, provider adapter, bounded graph, React chat.
2. Durable conversations: checkpoints, request deduplication, concurrency, recovery.
3. Policy retrieval: 18 versioned articles, section chunking, embeddings, PostgreSQL
   full-text plus vectors with rank fusion, calibrated evidence gate, citations.
4. Ticket actions: confirmed drafts, database-backed idempotency, honest outcomes.
5. Human support: durable queue, atomic claim, dashboard, automation pause.
6. Evals/security: 50 labeled scenarios (35 development/15 held-out), repeated critical
   cases, deterministic authorization/action checks, human-reviewed groundedness.
7. Operations: CI release gates, managed cloud PostgreSQL, restore/rollback rehearsal,
   retention, rate limits, usage reservations, trace retention, controlled pilot.

Confirmed defaults: one business; synthetic commerce first; OpenAI through an adapter;
10–15 hours/week; US$50 monthly ceiling; 100 conversations/day and five concurrent
conversations as test targets. Real customers require business integration, approved
policies, staffed support, and successful release gates. No performance target is a
measured result until a report records it.

## Initial architecture decision

Use synchronous SQLAlchemy for the foundation's tiny database probe. Regular FastAPI
handlers run blocking I/O in a thread pool. Introduce async boundaries deliberately
with model calls/workflow persistence, not by putting blocking calls in async handlers.
Use migrations for domain tables beginning in milestone 1. Milestone 0 creates none.
