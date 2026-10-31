# Support Pilot

**Milestone 6: evaluation, security checks, and redacted tracing.** Built with FastAPI, PostgreSQL,
SQLAlchemy/Alembic, LangGraph, and React/TypeScript. Local demo mode runs without a model
key; OpenAI mode uses a real tool-calling provider adapter. The UI labels demo mode.

Implemented: customer sign-in, Argon2 hashes, expiring/revocable server sessions,
CSRF/origin checks, database-backed login throttling, authorized order reads, a bounded
checkpointed graph, saved chat history, bounded follow-up context, stable request IDs,
serialized conversation runs, recovery controls, migrations, and security tests.

Policy retrieval, confirmed synthetic tickets, and human handoff are implemented. This development milestone is not a production release.

## Start with Docker Desktop (Windows, macOS, Linux)

Copy `.env.example` to `.env` (PowerShell: `Copy-Item .env.example .env`). Review port
settings if you already run PostgreSQL. Then:

```sh
docker compose up --build -d --wait
docker compose run --rm seed
docker compose run --rm ingest
docker compose ps
```

Compose runs Alembic and LangGraph checkpoint migrations before starting the API. Seeds are an explicit,
idempotent development-only command. Run seeding once before your first login.
The API serves the built React UI at http://localhost:8000 and API docs at `/docs`.
In this existing workspace `.env` selects **http://localhost:58000**, database port
**55432**, and demo mode to avoid conflicts with existing services.

| Account | Local password | Owned order |
|---|---|---|
| alice@example.test | Local-demo-only-42! | SP-1001 |
| bob@example.test | Local-demo-only-42! | SP-2001 |
| support@example.test | Local-demo-only-42! | Support dashboard |

Sign in as Alice and ask `Where is my order SP-1001?`. Try `SP-2001`: it must be
unavailable. Unknown references receive the same message. The support account opens the human-support dashboard. There is no public signup
or password reset in this milestone.

Chat history survives refresh and API restarts. After `SP-1001`, ask `When will it
arrive?`: the reference comes from this conversation, but status is freshly checked.
Use **New chat** for separate context and the conversation selector to return to history.
An interrupted turn offers **Resume request** and **Check status**. Resume reuses the
same request ID; completed results are returned without repeating work.

```sh
docker compose logs api
docker compose stop
```

Ports bind only to localhost. Changing `POSTGRES_PORT` also requires changing the
host-side `DATABASE_URL`. Changing `API_PORT` requires updating `ALLOWED_ORIGINS`.
Container-to-container database traffic continues to use port 5432. Credentials in
`.env.example` are for local development only. Changing a database password in `.env`
does not modify an existing PostgreSQL user's password.

## Enable OpenAI

In your untracked `.env`, set `AGENT_PROVIDER=openai`, a server-side `OPENAI_API_KEY`,
and `OPENAI_MODEL` to a tool-calling model available in your API account. Do not put the
key in React, a `VITE_` variable, Git, or chat. Restart the API:

```sh
docker compose up -d api
```

There is no automatic fallback from OpenAI to the demo router. Missing configuration
or provider failure returns an honest unavailable response. Live OpenAI behavior must
be smoke-tested separately; mocked adapter tests do not establish model quality.

The provider can choose order lookup, policy retrieval, both together, clarification,
or unsupported scope.
The application executes the chosen lookup and formats the final answer from verified
database facts. It does not display unchecked model prose. There are at most three
provider attempts, up to four reserved tool executions across recovery attempts, and
a 256-token routing output limit. Policy excerpts come from validated retrieved sections. Input
messages are limited to 2,000 characters. Socket operations and complete provider
calls have timeouts; the graph checks a 30-second execution budget before and after I/O.

Production configuration rejects demo mode, insecure cookies, and non-HTTPS allowed
origins. Additional production release gates remain in later milestones.

## Develop locally

Use Python 3.14 and Node 22. Install Python packages in a virtual environment:

```sh
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
# Linux/macOS (sets up application AND checkpoint tables)
PYTHONPATH=backend python -m app.migrate
# PowerShell: $env:PYTHONPATH = 'backend'; python -m app.migrate
# Linux/macOS
PYTHONPATH=backend python -m app.seed
# PowerShell equivalent: $env:PYTHONPATH = 'backend'; python -m app.seed
python -m uvicorn app.main:create_app --factory --app-dir backend --port 58000 --reload
```

Stop the Compose API first if it already occupies the same port. Leave PostgreSQL
running. In another terminal, run `npm ci` and `npm run dev` inside `frontend`.
Vite serves http://localhost:5173 and proxies `/api` to port 58000. For the built UI,
run `npm run build` and restart the Python API. All browser API calls stay same-origin.

## Verification

```sh
python -m ruff check backend tests
python -m ruff format --check backend tests
# Linux/macOS; set TEST_DATABASE_URL explicitly to the project's local database:
TEST_DATABASE_URL=postgresql+psycopg://support:local-development-only@localhost:55432/support python -m pytest -q
```

PowerShell: set `$env:TEST_DATABASE_URL` to that URL before `python -m pytest -q`.
Integration tests create and remove their own random schema; they do not truncate
application tables. Without the variable, database tests skip. CI supplies it.

Browser tests use the migrated, seeded development database and start a temporary API
on port 58001. Activate the Python environment first; then, inside `frontend`:

```sh
npm ci
npm run build
npx playwright install chromium
npm run test:e2e
```

The tests run on desktop and mobile Chromium. `PYTHON_EXECUTABLE` can override the
Python used by Playwright (path relative to the project root, or an absolute path).
They verify login, order cards, non-disclosure, logout, saved history, follow-ups,
and isolation between conversations.

See [verification notes](docs/verification.md) for measured results and environment
limitations, including this workspace's Docker DNS workaround.

## Learning

Start with [the milestone 6 walkthrough](docs/learning/milestone-6.md), then answer its
checkpoint before moving on. [Architecture decision](docs/decisions/0007-evaluation-and-telemetry.md)
explains evaluation evidence, human review, and redacted tracing. [Roadmap](docs/roadmap.md) tracks
future milestones.

## Durable conversation API

All routes require a customer session; POST requests also require its CSRF token.
Identity is always taken from that session, never the request body or model.

| Method and path | Contract |
|---|---|
| POST `/api/conversations` | `{ "request_id": "<UUID>" }`; idempotent creation |
| GET `/api/conversations` | Latest 50 owned conversations |
| GET `/api/conversations/{id}` | Ordered messages and run states |
| POST `/api/conversations/{id}/messages` | `{ "request_id": "<stable UUID>", "message": "SP-1001" }` |
| GET `/api/conversations/{id}/runs/{request_id}` | Stored run state/result |

The old `/api/chat` endpoint returns 410 after authentication/CSRF checks. A message
returns a run envelope with `status` and optional `response`: 200 completed, 202 in
progress, 503 retryable/failed. Reusing an ID with different text returns 409. A new
request while another is unfinished returns 409; resume the existing request first.

There are at most three execution attempts and three provider calls per request,
including retries across restarts; four tool executions cover lookup, retrieval, and handoff. The
30-second graph execution budget restarts per recovery attempt, not as an unlimited
retry loop. Each conversation accepts 100 turns; start a new one after that. The model
receives only the last six messages (1,000 characters each) and the last order result.
No background recovery, pagination beyond the latest 50 conversations, retention
cleanup or production admission controls are implemented yet.

## Knowledge and citations (milestone 3)

The `knowledge/` directory contains 18 synthetic English policy articles. Ingestion
uses token-limited section chunks, preserves table headers, embeds through a replaceable
adapter, and activates the complete snapshot transactionally. Removed files retire
articles; prior versions remain available for existing conversation citations.

```sh
# Host development, after installing updated requirements and migrating:
PYTHONPATH=backend python -m app.ingest
PYTHONPATH=backend python -m app.evaluate_retrieval
# PowerShell: $env:PYTHONPATH = 'backend'; python -m app.ingest
```

Ask `What is the return window?` or `SP-1001 return window` as Alice. Expand **Policy
sources** to inspect evidence. The authenticated endpoint is
`GET /api/conversations/{id}/sources/{chunk_id}`; a customer can only read a source
cited in their own conversation. Historical sources are marked superseded. New replies
revalidate active citations before publication, including after checkpoint recovery.

Local `EMBEDDING_PROVIDER=demo` uses deterministic hash vectors, not semantic AI.
For real embeddings set `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL`,
`EMBEDDING_DIMENSIONS`, and a server-side key, then re-ingest the whole corpus and rerun
evaluation. Production configuration rejects demo embeddings. Embedding calls have
bounded timeouts and no hidden SDK retries. No live OpenAI call was made in this milestone.
The tokenizer vocabulary is cached under `backend/tokenizer_cache` for offline ingestion.

Search merges exact cosine and PostgreSQL full-text rankings using RRF; full-text search
is not BM25. Replies quote supported retrieved sections rather than generating new policy
claims. The default evidence coverage floor is 0.7. Insufficient evidence produces a
clarification, with no invented handoff. Rankings are not confidence probabilities.

The retrieval regression suite finds expected evidence for 18/18 answerable questions;
the gate accepts 15/18 and rejects all eight negative cases. Three answerable questions
receive clarification. An initial held-out failure informed the revision, so fresh
independent validation remains required. See `evals/README.md` and measured verification
notes. This is not the full production-quality release gate.


## Milestone 4: confirmed tickets

Run migrations before starting the updated application (`docker compose up --build -d --wait`
or `PYTHONPATH=backend python -m app.migrate` for host development).

Sign in and start a conversation. In **Support ticket**, select **Describe an issue**,
enter the subject and issue details, and optionally supply an order you own. Select
**Review ticket**, review the saved details, then **Confirm and create ticket**.
Draft preparation creates no ticket. Editing replaces the old pending draft; pending
drafts expire after 24 hours. Refresh restores the saved draft or created ticket.

Repeated confirmations return the same durable ticket, including after a lost response
or application restart. A conversation may have multiple separately confirmed drafts;
earlier tickets remain listed. Ticket creation does not transfer the chat to a human
or execute refunds/cancellations. These are local synthetic ticket records.

Authenticated, CSRF-protected writes:

- `POST /api/conversations/{id}/ticket-drafts`: details plus stable `request_id`.
- `POST /api/conversations/{id}/ticket-drafts/{draft_id}/confirm`: stable `request_id`
  and `confirmed: true`. The body cannot override the saved details.
- `GET /api/conversations/{id}/ticket-draft`: restore the latest saved draft/ticket.
- `GET /api/conversations/{id}/tickets`: read this conversation's created tickets.

The model guides the customer to review; only the explicit confirmation path calls the
write service. See [the milestone 4 walkthrough](docs/learning/milestone-4.md) and
[the architecture decision](docs/decisions/0005-confirmed-tickets.md).


## Milestone 5: human support

Rebuild the updated API/frontend and apply migration 0005 before use. Sign in as a
customer and ask for a human, or select **Request human support**. The persisted status
becomes queued and automated substantive replies pause immediately. Exhausted automated
retry budgets also queue human support when the database is available.

In a separate browser profile, sign in as `support@example.test` using the seeded local
password above. The support dashboard shows queued, assigned, and resolved conversations.
Open a handoff to review its issue summary, reason, history, customer-owned orders,
confirmed tickets, and attempted actions. Select **Claim conversation**, send a human
reply, then **Resolve conversation** when appropriate. Claiming is atomic; only the
assigned agent can reply or resolve. Reply retries return the same saved message.

Customers see actual queue status and human authorship, with polling updates and no
invented wait estimate. Resolved conversations stay paused and reject new customer
messages. Starting a new chat restores automated help. No external notification is sent;
this milestone implements the local support queue and dashboard.

| Endpoint | Access and effect |
|---|---|
| POST `/api/conversations/{id}/handoff` | Customer-owned conversation; validated `reason` and `summary`; one durable handoff |
| GET `/api/conversations/{id}` | Customer reads live handoff status and ordered human/automated messages |
| GET `/api/support/handoffs?status=queued` | Support-only queue; also accepts `assigned` or `resolved`; up to 100 entries |
| GET `/api/support/handoffs/{id}` | Support-only conversation and customer context |
| POST `/api/support/handoffs/{id}/claim` | Atomic assignment to the signed-in support agent |
| POST `/api/support/handoffs/{id}/replies` | Assigned agent only; `{ "request_id": "<stable UUID>", "message": "..." }` |
| POST `/api/support/handoffs/{id}/resolve` | Assigned agent only; automation remains paused |

All writes require session CSRF protection. Model output cannot supply a customer identity
or human author. The final database transaction suppresses automated content even if a
handoff was queued while the model call was running or before checkpoint recovery.

See [the milestone 5 walkthrough](docs/learning/milestone-5.md) and
[verification notes](docs/verification.md). The dashboard queue cap, polling, demo routing,
and absence of staffed production operations remain explicit pilot limitations.

## Milestone 6 evaluation and security

Run `PYTHONPATH=backend python -m app.evaluate_release` with the local database running.
The CLI uses an isolated schema and demo providers, evaluates 50 scenarios (35 dev / 15
regression-heldout), repeats 24 critical cases five times, and writes the report, redacted
traces, and unfilled human-review packet to `evals/reports/milestone6/`. CI runs the same
machine gates and uploads artifacts. No paid model calls occur.

Requests now return `X-Request-ID`. Use `python -m app.inspect_trace UUID` to inspect
safe events and committed business audits. Migration `0006` adds trace/audit tables.
Input/output model budgets are configurable; unknown token usage or prices are not
reported as zero. Demo billing usage is zero and does not predict production costs.

Automated demo gates pass; independent human grounding/resolution review and live-provider
validation remain pending. Read the [milestone 6 walkthrough](docs/learning/milestone-6.md)
for measured results, review instructions, exercises and limitations. Deployment and
operational controls remain milestone 7.
