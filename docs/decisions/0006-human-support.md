# 0006: Durable human ownership with a publication check

Status: accepted for milestone 5.

A customer can request human support while a model call is running. Checking ownership
only before that call would allow a stale automated answer to appear after the human
takes responsibility. Ownership must therefore be durable and checked again when the
answer is published.

Persist one handoff per conversation, with `queued`, `assigned`, and `resolved` states.
A conversation remains paused after resolution; new automated assistance starts in a
new conversation. This makes queue creation naturally idempotent and prevents accidental
resurrection of human-owned work on retry or process restart.

All handoff transitions, human replies, message sequence allocation, and automated
finalization lock the conversation row in a short transaction. They use the same lock
order. Model calls and retrieval do not hold that row lock. The existing per-run session
advisory lock remains responsible for serializing customer automation and checkpoint
recovery; human actions can progress independently of a slow model.

Finalization rereads handoff state under the row lock. If a handoff exists, it publishes
only actual queue/assignment/resolution status and removes automated order facts and
citations. New queued/assigned turns skip model execution, and new resolved turns are
rejected with guidance to start a new conversation. A provider failure during a handoff
also finishes with saved handoff status. Exhausting automatic retries queues human
support when persistence is available; failure to persist never becomes a success claim.

Login and sessions support both customer and support roles. Domain services independently
check role and ownership. Queue context includes only the handed-off customer's orders,
conversation, tickets, and attempted actions. Only the assigned support agent can reply
or resolve. Human authorship is stored separately from automated runs and constrained in
PostgreSQL. Reply request IDs compare content on replay; duplicates return the existing
message, and conflicting reuse returns 409. Reply messages and sequence advancement
commit together.

The browser polls rather than introducing streaming infrastructure. Customer status is
live, while old assistant acknowledgments and tool results remain historical records.
The UI displays no invented wait time and renders customer/model/support text as escaped
text. The queue currently displays up to 100 items per state; larger-scale pagination,
assignment transfers, notifications, staffing schedules, and retention are later work.

Validation covers role/CSRF boundaries, queue deduplication and rollback, graph recovery
after the queue commit, two competing support owners, human reply replay and author
integrity, and successful/failed model calls racing with claim/reply/resolve. Browser
checks use separate customer and staff sessions on desktop and mobile, including a lost
reply response and persisted resolution.

An unrelated full-suite failure exposed random UUIDs used to break equal retrieval ranks.
Ranking now uses stable article IDs and chunk positions for SQL and RRF ties. A regression
test changes chunk UUIDs twice and checks that all 26 query rankings remain identical.
This removes an ingestion-dependent instability without changing evidence thresholds.

Interview explanation: “Queueing creates a durable ownership boundary. Short database
transactions serialize claim and reply actions with automated publication. The model can
finish late, but its candidate answer cannot pass the final ownership check. Unique keys
and saved human authorship make retries safe and responsibility visible.”
