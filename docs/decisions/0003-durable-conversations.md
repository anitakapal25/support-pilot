# 0003: Durable turns, explicit recovery, and conversation context

Status: accepted for milestone 2, 2026-10-29.

A process can crash after accepting a message, completing a lookup, or finishing the
graph. A client can lose a response even though the server committed it. Therefore a
HTTP response cannot be our only record of success.

## Decision

Keep the transcript, request ledger, and tool results in application tables managed by
Alembic. Use the official PostgreSQL LangGraph saver for execution checkpoints, with
its own setup migrations. `python -m app.migrate` runs both explicitly before serving.
Pin the saver dependency: our transaction helper shares its cursor lock, so upgrades
must rerun concurrency and recovery tests.

Each server-generated run ID is a checkpoint thread. Conversation memory is explicitly
reconstructed from its authorized transcript, rather than accidentally carrying all
previous graph fields into another turn. Six bounded messages and the latest assistant
order result provide context. Memory is untrusted input; a follow-up performs a new
ownership-scoped lookup. Historical cards retain their original checked timestamp.

A client-generated UUID identifies a request within a conversation. Its normalized
text hash detects conflicting reuse. The first transaction stores the run, user
message, and two reserved sequence numbers. Final publication stores the assistant
message and canonical result together. Uniqueness constraints back both boundaries.

A dedicated, unpooled PostgreSQL session holds a conversation advisory lock and performs
checkpoint/run writes. Closing or losing that connection releases the lock and prevents
the old worker from writing through a replacement connection. Short transactions share
the saver's cursor lock; no transaction spans a model call. Other conversations proceed
independently. Five-way integration tests exercise this against PostgreSQL.

A duplicate completed request returns the recorded result. An interrupted request
resumes its checkpoint; an already completed checkpoint only needs publication. Tool
results are committed separately and reused if the process dies before checkpointing.
Call reservations and execution-attempt counters survive restarts, limiting recovery.

## Limits and consequences

This is explicit client-driven recovery, not a durable background worker. An unfinished
turn blocks new messages in that conversation until resumed. The UI can recover accepted
requests after reload. An unsent request lost before server acceptance is not promised
to survive closing the browser. Conversation creation is independently idempotent.

A read can repeat if the process dies after executing it but before storing its result.
That is safe for the current read-only tool; this does NOT establish exactly-once
external writes. Milestone 4 needs transactional ticket idempotency and confirmation.
A replayed completed request deliberately returns historical data; a new request gets
a fresh lookup. Transcripts and checkpoints contain customer content and will need
retention cleanup and operational controls before production deployment.

The latest 50 conversations are listed, and each accepts 100 turns. This bounds this
learning milestone; older conversations remain addressable by their authorized ID.
The graph deadline applies per execution attempt, with three attempts maximum. There
is no claim yet that the provisional latency or production reliability gates pass.

## Interview explanation

“Checkpoints track where computation can resume; transactions keep business records
consistent; idempotency identifies repeated intent. We need all three. A conversation
lock serializes competing turns, while unique constraints and stored results prevent
publication duplicates. Read-only recovery is established before we introduce actions
that change a business system.”
