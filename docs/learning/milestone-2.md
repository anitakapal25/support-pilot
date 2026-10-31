# Milestone 2: Durable conversations

Goal: explain how a message survives API restarts and why retrying does not create a
second reply. Stop at this learning checkpoint before implementing retrieval.

## 1. Concept lesson

A Python variable disappears when its process exits. PostgreSQL rows survive a normal
API restart. Saving a transcript is only part of durability: the server also needs to
remember whether a request started, which work completed, and what response it published.

A **transaction** commits related rows together or rolls them back together. Here the
accepted user message and run are one transaction; the final assistant message and run
result are another. We deliberately do not hold a transaction open while waiting on a
model. Think of this like a short Laravel `DB::transaction` around related writes.

A **checkpoint** records LangGraph state and its next node. It answers “where can this
computation continue?” It does not automatically make an external action idempotent.

An **idempotency key** identifies the same logical submission across HTTP retries. React
creates a UUID once, then reuses it when resuming. New UUID means new intent, even if the
text is identical. Same UUID with different normalized text returns 409.

A **conversation lock** prevents two workers from advancing that conversation together.
This is a PostgreSQL advisory lock, not a Python lock: it coordinates separate processes.
The Python lock inside the saver instead protects one connection's cursor operations.

## 2. Trace the implementation in small steps

1. Read `backend/app/schemas.py`: distinguish a `SubmitMessage` from a `RunView`.
   The request contains text and a request ID, never customer identity.
2. Read conversation endpoints in `backend/app/api.py`. Authentication provides a
   `Principal`; authorization checks conversation ownership before loading history.
3. Follow `DurableTurn.submit` in `backend/app/conversations.py`. It authorizes, opens
   a PostgreSQL connection, acquires the advisory lock, and calls `accept`.
4. In `accept`, locate the hash comparison and unfinished-run check. Then find the
   transaction that inserts the run/user message and reserves their sequence numbers.
5. Follow `build_agent` in `backend/app/agent.py`. The provider routes, the application
   executes an authorized lookup, and the application formats the verified response.
6. In `lookup_order`, find the durable call reservation and cached tool result. A resume
   after the result commit can return that result without a second order read.
7. In `finalize`, find the transaction that commits the assistant message and canonical
   run response. The client reads the transcript rather than appending an assumed reply.
8. Read `frontend/src/ConversationChat.tsx`: follow `send → submit → load`. Notice that
   `Resume request` reuses the original ID and that reload reconstructs unfinished work
   from the server's messages/runs.

`models.py` defines application tables; migration `0002_durable_conversations.py`
creates them. `checkpoints.py` configures the official saver. `migrate.py` initializes
both migration systems. Alembic excludes framework-owned checkpoint tables from its
model comparison so it will not try to delete them.

## 3. Work through the failure windows

| Failure point | Durable evidence | Recovery |
|---|---|---|
| Before acceptance | No run | Retry same ID to submit |
| After acceptance | User message and unfinished run | Resume graph |
| After lookup result commit | Tool execution result | Reuse recorded lookup result |
| After graph completion | Completed checkpoint | Publish result without rerunning graph |
| After publication, before HTTP response | Completed run and assistant message | Return identical stored result |
| Lock connection lost | Previously committed records only | Old worker cannot publish; replacement resumes |

A crash between a read and its result commit may repeat that read. That is acceptable
for order lookup; it is not an exactly-once guarantee for refunds or ticket creation.
Three execution attempts and persisted provider/tool counters prevent restart-based
budget resets. Recovery is user initiated; no background job silently retries work.

## 4. Try it locally

Open http://localhost:58000 and sign in as `alice@example.test` with the documented
local demo password. Start a new chat, send `SP-1001`, then refresh and ask `When will
it arrive?`. Both cards should remain, and the new answer performs another lookup.
Restart the API (`docker compose restart api`) and refresh: history remains.

Create a new chat and ask the same follow-up without a reference. It should ask for a
reference. Return to the earlier conversation using the selector. Sign in as Bob:
Alice's conversations must not appear. Asking for `SP-1001` must not reveal its status.

## 5. Exercise

Before changing code, predict the result of each case:

- Submit the same request ID and text twice.
- Submit that ID with `SP-2001` instead.
- Submit two different request IDs while one conversation is running.
- Start requests in five different conversations simultaneously.
- Kill the process after recording a tool result, then resume from another process.

Read `tests/test_conversations.py` and compare those predictions with its assertions.
`tests/recovery_worker.py` deliberately exits a separate process after the tool-result
commit; another process resumes it. This is stronger evidence than merely mocking a
checkpoint object. The lost-connection test also proves that an old worker cannot
publish after losing the session which held its lock.

Run the suite using the TEST_DATABASE_URL command in the README. Database tests use
isolated schemas. Read the test summary: skipped integration tests are not evidence
that PostgreSQL persistence worked. See `docs/verification.md` for measured results.

## 6. Your explanation checkpoint

Explain these in your own words before milestone 3:

1. Why are a transcript, a checkpoint, and an idempotency record different things?
2. Which two database writes must commit together when accepting a message? Which
   writes must commit together when publishing an answer?
3. Where does the request UUID originate, and why must retry reuse it?
4. Why is a Python lock insufficient when two API processes serve the same conversation?
5. What prevents an old worker from publishing after its database connection dies?
6. Why can a completed request return an old order status while a new follow-up must
   perform another authorized lookup?
7. Which crash window can still repeat a read? Why would writes require more design?
8. Which real PostgreSQL tests demonstrate restart recovery, duplicate safety, and
   independent progress for concurrent conversations?

Architecture note: [0003 — durable turns](../decisions/0003-durable-conversations.md).
The interview explanation at its end is a starting point; rewrite it without looking.
