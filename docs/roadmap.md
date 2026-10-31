# Delivery and learning roadmap

Current status: milestone 6 implementation and automated demo gates pass. The benchmark
covers 50 unique scenarios and 146 trials, with redacted traces and transactional audits.
Local verification: 72 backend tests, 14 desktop/mobile browser tests, and all machine
release gates passed. Independent human grounding/resolution review remains pending;
live-provider behavior and production performance have not been validated.
Complete the milestone 6 learning/review checkpoint before deployment work.

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
