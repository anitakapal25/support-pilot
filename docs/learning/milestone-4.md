# Milestone 4: Confirmed tickets

Goal: trace customer details → immutable draft → explicit confirmation → one durable
synthetic ticket. Human support queues and external ticket providers are milestone 5
and future adapter work. An open ticket does not mean a human has claimed the chat.

## Concept lesson

A draft is a proposed action, not a completed action. Its subject, issue description,
and optional owned order reference are an immutable JSON snapshot. Editing creates a
new server-issued draft ID and supersedes the previous pending draft. Pending drafts
expire after 24 hours. Confirmation applies to one exact draft, not to whatever details
a model might produce later.

An idempotency key identifies an operation across retries. Draft preparation compares
the normalized payload when the same client request ID is reused. Confirmation receipts
bind each confirmation request ID to its draft. Reusing it for another draft is a 409.
The draft ID also has a unique ticket constraint: even a retry using a different
confirmation request ID returns the same ticket.

Atomicity means all writes commit together or none do. The confirmation receipt, draft
status/confirmation timestamp, and ticket insert share one PostgreSQL transaction.
A row lock on the authorized conversation serializes preparation and confirmation,
including requests handled by different processes. Unique constraints provide a second
line of enforcement. Success is returned only after commit. A lost response is safe:
retry confirmation, or reload to read the committed ticket.

## Code walkthrough

1. Read `TicketDetails`, `CreateTicketDraft`, and `ConfirmTicket` in `schemas.py`.
   Unknown fields are forbidden. Confirmation requires the actual JSON boolean `true`.
   Identity, ticket status, and the confirmed payload cannot be supplied in that request.
2. Follow the three ticket tables in migration `0004_confirmed_tickets.py` and `models.py`.
   The composite foreign keys prevent linking a ticket or receipt to another
   conversation's draft. Explain the draft and confirmation unique constraints.
3. Follow `prepare_draft` in `tickets.py`. Authorization precedes locking; order ownership
   is verified without revealing another customer's order. A repeated preparation request
   returns its existing draft; changed details under the same ID fail.
4. Follow `create_ticket`. It checks the receipt, scopes the draft to the authorized
   conversation, returns any existing ticket, rejects expired/superseded drafts, rechecks
   order access, then writes the receipt, confirmation, and ticket in one transaction.
5. Read the API endpoints and `TicketPanel.tsx`. The customer enters details, selects
   **Review ticket**, reviews the persisted snapshot, then selects **Confirm and create
   ticket**. Refresh restores the draft/ticket; earlier ticket references remain available.
6. Read `prepare_ticket` routing in `provider.py` and `agent.py`. Chat requests guide the
   customer to the form. The model cannot invoke a write or turn a message such as
   “I confirm everything” into authorization. The server's `create_ticket` domain tool
   accepts the exact draft reference only through the authenticated confirmation path.
7. Read `tests/test_tickets.py`: ownership, CSRF, schema checks, stale drafts, conflicting
   request IDs, six simultaneous confirmations, rollback, and replay after completion.
   The browser test commits a confirmation then drops its response before retrying.

## Run and exercise

Start and migrate using the README's Docker commands. For host development:

```sh
PYTHONPATH=backend python -m app.migrate
TEST_DATABASE_URL=postgresql+psycopg://support:local-development-only@localhost:55432/support python -m pytest -q tests/test_tickets.py
```

Use your configured PostgreSQL port; PowerShell users set `PYTHONPATH` and
`TEST_DATABASE_URL` through `$env:` before running Python.

1. Sign in as Alice and start a conversation. Ask “Please create a support ticket.”
2. Enter a subject, at least ten characters of issue detail, and optionally `SP-1001`.
3. Select **Review ticket**. Refresh: the draft survives, and no ticket exists yet.
4. Edit the issue and review again. Explain why the old draft ID must no longer confirm.
5. Confirm the new draft. Record its reference and refresh: the same open ticket remains.
6. Enter Bob's `SP-2001`: preparation must report “Order unavailable”.
7. Read the lost-response browser test. Predict what a retry after commit returns and
   what happens if the database fails before commit.

## Explanation checkpoint

Explain without reading the code:

1. Why are a client request ID and a server-issued immutable draft both useful?
2. Which exact customer action authorizes creation? Why cannot model text replace it?
3. Which writes must commit together? Why is an in-memory “already created” flag unsafe?
4. Why does retrying a confirmed draft with a new request ID still return one ticket?
5. Why must a repeated confirmation request ID fail when supplied with another draft?
6. How do editing, expiration, and order ownership affect confirmation?
7. What prevents two application workers creating two tickets concurrently?
8. Why does the local exactly-once database effect not establish exactly-once delivery
   to a future external ticket provider? What would an outbox/provider idempotency add?

Architecture decision: [0005 — confirmed tickets](../decisions/0005-confirmed-tickets.md).
Rewrite its interview explanation in your own words before milestone 5.
