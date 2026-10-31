# Milestone 5: Human support and ownership

Goal: trace a durable transfer from customer to human, and explain why an automated
answer cannot publish after that transfer. Pause here before the full evaluation and
security milestone.

## Concept lesson

A handoff is a state machine: `queued → assigned → resolved`. Queue creation immediately
pauses substantive automated answers. Claiming adds one support owner. Only that owner
can reply or resolve. Resolution keeps automation paused; the customer starts a new
conversation to get automated help again.

The database is the source of truth. An in-memory “human is handling this” flag would be
lost on restart and would differ across application workers. A unique conversation key
prevents duplicate handoffs, even when a queued request is retried. This milestone uses
one handoff per conversation for its entire lifetime, a stronger rule than one active
handoff, because resolved conversations do not reopen.

A row lock serializes short changes to one conversation. Queue creation, claim, reply,
resolve, message sequence allocation, and automated publication acquire that same row.
No row lock is held while waiting for the model. If a handoff wins the race, finalization
replaces the candidate automated answer with actual handoff status, removing order facts
and citations. If publication commits first, that answer precedes the handoff; the next
turn is paused. This establishes a precise ordering rather than relying on timing guesses.

The existing session advisory lock still serializes automated runs and checkpoints.
Human support does not need to wait for a slow model to finish before claiming or
replying. The final row-lock check prevents the old automated worker from publishing
substantive content after the handoff, including when recovering a completed checkpoint.

## Read and trace the code

1. Read migration `0005_human_support.py` and the `Handoff` model. Explain the unique
   conversation key, status/assignment constraints, and queue index.
2. Read `current_principal`, `current_customer`, and `current_support` in `auth.py`.
   Login/me/logout work for both roles; domain endpoints enforce the required role.
   Every mutation still verifies the session, origin, and CSRF token.
3. Follow `request_handoff` and `create_handoff` in `handoffs.py`. The customer and
   conversation come from authenticated context. Reason and summary are validated;
   neither grants access. A successful response is returned only after commit.
4. Follow the graph's `request_handoff` action in `provider.py`, `agent.py`, and
   `DurableTurn.request_handoff`. The handoff and tool result commit together on the
   run's dedicated connection. A crash after commit cannot create another queue entry.
5. Read `DurableTurn.submit`, `accept`, `failure`, and `finalize`. A queued/assigned
   conversation skips the model. A late successful answer, failed model call, or resumed
   checkpoint still checks live handoff state. Exhausting automated retries creates a
   durable `dependency_failure` handoff when the database is available.
6. Follow `claim`, `reply`, and `resolve`. Two competing claims serialize on the same row;
   the loser receives 409. Human reply request IDs are durable and payload-checked.
   A lost response can be retried with the same ID, including after resolution.
7. Read the `Message` authorship constraint. Human messages have role `support`, a
   server-supplied author, a reply request ID, and no automated run ID. Customer/assistant
   messages retain their run association. Shared sequence allocation prevents collisions.
   Human messages do not consume the customer's configured turn budget.
8. Read `support_detail`. The dashboard includes conversation history, escalation reason,
   issue summary, confirmed tickets, attempted actions, and orders filtered by the
   conversation's customer. Support access does not bypass customer order endpoints.
9. Inspect `SupportDashboard.tsx` and `ConversationChat.tsx`. The customer sees live queue
   status and human authorship without a promised wait time. The support owner can claim,
   reply, and resolve. Text is escaped. Polling refreshes customer status every three
   seconds and the dashboard every four seconds; manual queue refresh is also available.

## Try it in two browser sessions

Apply migrations and rebuild the API/frontend using the README startup instructions.
The seeded development accounts all use `Local-demo-only-42!`:

- Customer: `alice@example.test` or `bob@example.test`.
- Support: `support@example.test`.

1. In a normal browser window, sign in as Alice and start a conversation. Ask for a human,
   or select **Request human support**. Confirm that the status becomes queued.
2. In an incognito window or separate browser profile, sign in as support. Open the queued
   conversation, review the summary/history/orders, and select **Claim conversation**.
3. Send a human reply. The customer should see `Support · HUMAN`. Send `SP-1001` as the
   customer: the message is saved, but no automated order answer is produced.
4. Refresh both windows. Assignment and messages survive. Resolve from the support
   window; the customer composer closes and automation remains paused.
5. Start a new customer conversation. An authorized order lookup works again.
6. Read the concurrent-claim and in-flight-model tests. Predict which transaction wins
   in each ordering. Read the browser test that commits a human reply, drops the response,
   and retries: only one reply should appear.

```sh
# With the virtual environment active, using your configured PostgreSQL port:
PYTHONPATH=backend python -m app.migrate
TEST_DATABASE_URL=postgresql+psycopg://support:local-development-only@localhost:55432/support python -m pytest -q tests/test_handoffs.py
```

For PowerShell, set `$env:PYTHONPATH` and `$env:TEST_DATABASE_URL` first.

## Your explanation checkpoint

1. Why does queue creation pause automation before a support agent claims the work?
2. What is the difference between the session advisory lock and the conversation row lock?
3. Which check stops a model answer generated before the handoff from publishing afterward?
4. Why does checking only at request start leave a race?
5. What happens when two support agents claim the same queued conversation?
6. How does a retry after a lost human-reply response avoid duplication?
7. Which fields prove human authorship, and why cannot a customer or model supply them?
8. Why does resolving a handoff leave automation paused?
9. What can the application honestly report if the queue transaction fails?
10. Why are summaries and tool results treated as data rather than instructions?

Read [0006 — human ownership](../decisions/0006-human-support.md), then explain the
publication race in your own words. Human staffing, full release evaluation, production
admission controls, and operational recovery remain separate work.
