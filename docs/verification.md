# Milestone 6 verification — 2026-10-31

Measured locally against PostgreSQL and demo providers:

- **72 backend tests passed**. New coverage checks dataset split integrity, trace-field
  redaction and bounds, server-generated request IDs, usage/cost accounting, model input
  limits, transactional audit rollback, human-review evidence binding and judge calibration.
  After final review-validator changes, its six non-database tests passed again.
- **14 desktop/mobile Chromium tests passed**, including a human reply containing an
  HTML event handler. Both customer and support views render the payload as text, create
  no injected image element and execute no handler. Reply replay still produces one message.
- **50/50 unique scenarios and 146/146 trials passed** all deterministic checks. There are
  35 development and 15 regression-heldout cases. All 24 critical cases were consistent
  across five repetitions. No forbidden action was observed.
- **22/22 citations valid**, retrieval recall **16/16 unique labeled scenarios** (20/20
  repeated trials), tool selection/arguments **31/31 unique tool cases** (63/63 trials).
  All 38 required handoff trials produced durable handoffs. Eleven created tickets had
  matching confirmed drafts and committed audits. Human semantic review is still pending.
- Five simultaneous conversations completed **25/25 requests**: p50 **291.564 ms**, p95
  **313.776 ms**, below the provisional ten-second target for this local demo profile.
  Sequential multi-step scenario p50/p95 were **128.077/281.546 ms**. These measurements
  do not establish hosted or live-model performance.
- Demo provider-reported billing usage and estimated task cost are zero. No paid model
  calls were made. Unknown live-provider usage/prices remain unknown rather than zero.
- Existing retrieval regression: **18/18 recall**, **17/18 positive acceptance**, **8/8
  negative rejection**, unchanged threshold 0.7. One answerable legacy question still
  receives conservative clarification; this separate report does not claim perfect recall
  implies perfect answer acceptance.
- Migration **0006** applied locally; Alembic found no schema drift. Ruff lint/format,
  dependency consistency, TypeScript checking and frontend build passed.

Artifacts: [release report](../evals/reports/milestone6/report.json),
[redacted traces](../evals/reports/milestone6/traces.json),
[unfilled human-review packet](../evals/reports/milestone6/human-review.json),
[initial failing report](../evals/reports/milestone6-initial/report.json), and
[retrieval regression](../evals/reports/retrieval-milestone6.json).

The initial run exposed five evidence-gate misses and an adversarial routing ambiguity.
The labels were preserved and the implementation corrected. These inspected splits are
regression data; independent validation is still needed. `machine_passed` is true and
`release_ready` is false. No human or LLM-judge approval has been fabricated.

---

# Milestone 5 verification — 2026-10-30

Measured locally:

- **64 backend tests passed** against real PostgreSQL. New coverage includes role/CSRF
  boundaries, durable queue deduplication, rollback without a false transfer claim,
  model tool recovery after queue commit, competing support claims, human authorship,
  reply replay/payload conflicts, owner-only resolution, queued/assigned/resolved
  automation controls, recovery of completed checkpoints, and human message sequencing.
- Race tests hold an automated provider call open while another request queues, claims,
  replies to, and resolves that conversation. Both a successful and a failed late model
  call finish with handoff status and no order/citation content. Human replies do not
  consume the customer's configured turn budget.
- **14 Playwright tests passed** across desktop and mobile Chromium. The complete
  customer/support flow uses separate authenticated sessions: queue, claim, reply,
  resolution, refresh persistence, paused order requests, and automation in a new chat.
  The human-reply test commits on the server, drops the response, and retries its same
  request ID; only one human message appears. Both handoff browser cases passed again
  after final dashboard presentation and failure-handling refinements.
- Final support dashboard screenshots for desktop/mobile and customer handoff status
  were inspected. Text wraps, human authorship is visible, and attempted actions are
  presented as readable outcomes. Artifacts: `frontend/test-results/support-desktop.png`,
  `support-mobile.png`, `handoff-desktop.png`, and `handoff-mobile.png`.
- Migration **0005** applied to the local development database; `alembic check` found
  no model/schema drift. Ruff lint/format, dependency checks, TypeScript checking, and
  production frontend build passed. CI's milestone name was updated; no remote run occurred.

The first full-suite run exposed an existing nondeterministic retrieval tie: random
chunk IDs could push shipping destinations outside the top five. SQL rankings and RRF
now break ties by stable article ID/position. A regression test changes all chunk UUIDs
twice and verifies unchanged rankings for all 26 retrieval questions. The measured report
is `evals/reports/retrieval-milestone5.json`: **18/18 recall**, **15/18 answerable questions
accepted**, and **8/8 unsupported questions rejected**, using the same 0.7 threshold and
demo hash embeddings. This remains regression evidence, not fresh held-out validation.

Runtime and scope:

- Browser tests ran against a disposable, migrated and seeded PostgreSQL database,
  avoiding changes to customer data and login-rate counters in the development database.
  The temporary host API on 58001 and the test database were removed after verification.
- Development PostgreSQL remains on localhost:55432. Rebuild the Docker API/frontend
  before using the updated application; no new Docker image was built or deployed here.
- Use the existing seeded `support@example.test` account and local demo password to
  open the dashboard. Customer and support endpoints enforce separate role permissions.
- One handoff is retained per conversation, including after resolution. Queue views
  return at most 100 entries per status. Polling updates customer status every three
  seconds and the dashboard every four seconds. There is no external notification,
  assignment transfer, staffing scheduler, or queue pagination in this milestone.
- The provider can request a typed handoff; the server supplies identity and ownership.
  Exhausted automation retries create a durable fallback queue entry when the database
  works. A database failure produces an unavailable response, never a claim that the
  transfer succeeded. No live OpenAI call was made; local routing uses demo mode.
- The milestone 5 explanation checkpoint remains for the learner. Full evaluation,
  adversarial release gates, production staffing, and operational readiness remain later
  milestones. No production-release claim is made.

---

# Milestone 4 verification — 2026-10-30

Measured locally:

- **51 backend tests passed** against real PostgreSQL, including six new ticket tests.
  They cover draft replay/payload conflicts, explicit boolean confirmation, forbidden
  payload overrides, CSRF/ownership, cross-conversation references, optional order
  ownership, replaced/expired drafts, confirmation-ID conflicts (including replay aliases),
  six concurrent confirmations returning one ticket, transaction rollback, and replay
  without in-memory state. Chat text alone cannot create a ticket.
- **12 Playwright tests passed** across desktop and mobile Chromium. The new workflow
  reviews a draft, reloads before confirmation, commits confirmation then drops its HTTP
  response, retries with the same request ID, and reloads the same ticket. Existing
  order, saved conversation, recovery, and citation workflows also pass.
- Desktop/mobile ticket screenshots were inspected: escaped details and full ticket
  references fit the panel; the mobile reference wraps without horizontal overflow.
  Artifacts: `frontend/test-results/ticket-desktop.png` and `ticket-mobile.png`.
- Migration **0004** applied to the local development database. `alembic check` reports
  no model/schema drift. Ruff lint/format, Python dependency checks, and TypeScript/
  production frontend build passed. Corpus ingestion remains 18 articles / 19 chunks.
- Backend tests now explicitly scope Alembic's version table to the isolated test
  schema. The old fixture could discover the public version table through `search_path`;
  its test-induced public marker change was repaired and migration 0004 applied in one
  transaction. The full suite passed after the fix, then the public schema check passed.

Implementation boundaries:

- Tickets are synthetic PostgreSQL records, with no external delivery or human queue.
  Local atomic/idempotent creation does not establish exactly-once external delivery.
- The model selects `prepare_ticket` for guidance. Authenticated customers review and
  confirm through the form; only that endpoint calls the write domain service. No live
  OpenAI call was made. Existing SDK contracts use mocks and local routing uses demo mode.
- Drafts expire after 24 hours; editing supersedes the old pending draft. Confirmation
  receipts, confirmation evidence, and the ticket commit together. Earlier ticket
  references remain readable after preparing another draft.
- PostgreSQL is running on localhost:55432. Browser verification used the temporary host
  API on 58001, which Playwright stopped afterward. No updated Docker API image was
  built or deployed during this milestone; rebuild before using Compose's API service.
- Chromium was downloaded to `/tmp/support-pilot-m4-browsers` for browser verification.
  Tests require execution outside this environment's restricted sandbox for PostgreSQL,
  TestClient threads, and Chromium. CI was updated but no remote Actions run occurred.
- The learner explanation checkpoint in `docs/learning/milestone-4.md` remains for the
  user. Human handoff, full release evaluation, and production operations remain later
  milestones. These results establish the implementation milestone, not production readiness.

---

# Milestone 3 verification — 2026-10-29

Measured locally:

- **45 backend tests passed**: full 44-test suite, then the added forged-excerpt test.
  Real PostgreSQL tests cover idempotent ingestion, version replacement/deletion,
  failed staging, historical source authorization, combined policy/order lookup,
  insufficient evidence, embedding-identity isolation, and stale checkpoint publication.
  Unit checks cover table/header/token boundaries, RRF, and the embedding SDK contract.
- **10 Playwright tests passed** across desktop and mobile Chromium. Citation expansion
  and persistence after refresh passed; the two citation cases were rerun for screenshots.
  The mobile screenshot was inspected; expanded sources remain in the scrollable chat.
- Ingestion activated **18 articles / 19 chunks**. Migration 0003 and pgvector extension
  applied; Alembic reports no model/schema drift. TypeScript/build, Ruff, and pip checks
  passed. CI now includes ingestion and the retrieval regression gate.
- Local Docker image built using pinned wheels, built frontend, corpus, and cached
  tokenizer. API/database healthy. Running-container smoke passed: authorized order
  plus policy response, valid source contents, identical replay, and logout.
- App: http://localhost:58000. PostgreSQL remains on localhost:55432.

Retrieval measurements (demo hash embeddings; real vector/full-text SQL):

| Measure | Result |
|---|---|
| Expected article in top five | 18/18 (100%) |
| Answerable questions accepted by evidence gate | 15/18 (83.3%) |
| Answerable questions clarified | 3/18 |
| Negative questions rejected | 8/8 |

The first 0.5 gate accepted one negative question. Its report is preserved at
`evals/reports/initial-retrieval.json`. The revised floor of 0.7 is conservative; the
final report at `evals/reports/retrieval.json` passes the retrieval regression gate.
Because the original held-out failure informed this revision, the final set is regression
evidence, not untouched held-out validation. Fresh independent evaluation and human
review remain required. Retrieval recall is not end-to-end task success: the planned
90% production task-success gate has NOT been established.

Limits:

- No live OpenAI routing or embedding call was made. OpenAI SDK tests use mocks;
  local runtime uses explicitly labeled demo routing and hash vectors.
- Policy replies quote exact excerpts. The lexical evidence gate can still miss relevant
  paraphrases or accept misleading overlap outside this small synthetic dataset.
- Tickets, real handoff, business-approved policies, retention cleanup, production
  operations, and full release evaluation remain future milestones.
- Docker used the existing offline-build workaround (`/tmp/support-pilot-m3-build`,
  `/tmp/support-pilot-m3-wheels`); the online multi-stage Dockerfile was not rebuilt
  end-to-end. This Compose version rejects `run --no-build`; plain `run --rm ingest`
  uses the already-built image. Browser tests used the temporary host API on 58001.
- CI configuration was updated, but no remote Actions run occurred. Windows was not
  executed. These results support the learning milestone, not production readiness.

---

# Milestone 2 verification — 2026-10-29

Measured locally:

- **35 backend tests passed** against real PostgreSQL, with isolated test schemas.
  Coverage includes ownership/CSRF, identical replay, conflicting request IDs,
  history across application recreation, fresh authorized follow-ups, separate
  conversation context, bounded retries, and five simultaneous conversations.
- A subprocess exits after committing an order-tool result; another process resumes
  the saved checkpoint and publishes one response. A separate failure test closes
  the lock-owning connection before publication and verifies recovery by a new worker.
- **Eight Playwright tests passed** across desktop and mobile Chromium. They cover
  sign-in/out, order authorization, saved history after reload, follow-ups, switching
  conversations, and resuming a dropped submission with its original request ID.
  After fixing Check status to preserve an unaccepted request, both affected browser
  cases were rerun and passed. The mobile layout screenshot was inspected.
- TypeScript checking/production build, Ruff lint/format, and `pip check` passed.
- Application migration 0002 and official LangGraph checkpoint setup applied;
  `alembic check` found no model/schema drift.
- Local runtime image built; Compose migration exited successfully; API and PostgreSQL
  report healthy. Running-container smoke passed: readiness, UI, sign-in, persisted
  messages, identical duplicate response, follow-up, and logout.
- Current app: http://localhost:58000, explicit demo provider, database port 55432.

Limits and environment notes:

- No live OpenAI call was made; provider SDK tests are mocked. Model quality and the
  production evaluation/latency gates remain unmeasured.
- Playwright Chromium was downloaded because the previous temporary binary was absent.
  Browser tests used a temporary host API on port 58001 with the real development DB.
- Repeated test logins triggered Alice's 20-minute login throttle during container
  smoke testing. Bob's seeded account completed the smoke test; throttling stayed
  enabled. Login may temporarily return 429 for Alice after this verification run.
- The existing Docker DNS workaround was retained: host-downloaded pinned wheels and
  built frontend under `/tmp/support-pilot-m2-build`, wheels under
  `/tmp/support-pilot-m2-wheels`. The supplied online multi-stage Dockerfile was not
  built end-to-end here. The runtime image and Compose startup were tested.
- Recovery is explicitly resumed by the client; there is no background worker. A read
  interrupted before its tool-result commit may repeat. No exactly-once external
  action guarantee is claimed. Tickets, retrieval, handoff, retention cleanup, and
  production operations remain future milestones.
- CI was updated but no remote Actions run occurred. Windows instructions were not
  executed. These checks establish this milestone, not production readiness.

---

# Milestone 1 verification — 2026-10-28

Measured locally:

- **22 backend tests passed**, including real PostgreSQL auth/ownership tests, CSRF,
  session expiry/revocation, login throttling, schema validation, retries, provider
  cancellation, and honest failure responses. PostgreSQL tests used isolated schemas.
- **Four Playwright tests passed**, covering desktop and mobile Chromium, sign-in,
  verified own-order lookup, cross-customer denial, unknown-order failure, refresh,
  and sign-out. Tests used the explicit demo provider and a real seeded database.
- React/TypeScript production build passed. Desktop/mobile screenshots were inspected.
- Ruff lint/format checks and Python dependency checks passed.
- Alembic migration applied successfully; `alembic check` reports no model/schema drift.
- Pinned runtime packages were installed in the container. The migration service
  exited successfully and the API/database containers both report healthy.
- Running-container smoke test passed: UI delivery, demo-mode flag, sign-in, own-order
  lookup, cross-customer denial, and session revocation on logout.
- Current app: http://localhost:58000 (demo mode); PostgreSQL: localhost:55432.

Important limits:

- No live OpenAI call was made. SDK contract/error tests use mocks. Configure the
  server-side key and model for a separate real-provider smoke test and quality evals.
- Docker's previously observed DNS issue was avoided using host-downloaded pinned
  wheels and the locally built frontend. The temporary verification build is under
  `/tmp/support-pilot-m1-build`, wheels under `/tmp/support-pilot-m1-wheels`.
  The regular multi-stage online Dockerfile is supplied but was not built end-to-end
  in this environment. The runtime image and Compose migration/startup were tested.
- Browser tests used a temporary host API on port 58001; container health/startup were
  checked separately. Linux was tested; Windows setup instructions were not executed.
- CI was updated but no remote GitHub Actions run has occurred.
- Conversation memory, checkpoints, duplicate-request recovery, retrieval, tickets,
  and handoff remain future milestones. No production-release gate is claimed.

---

# Milestone 0 verification — 2026-10-27

Measured locally:

- Python 3.14.6; pinned dependencies resolve and `pip check` passes.
- Four pytest cases pass, including real PostgreSQL readiness.
- Ruff lint and format checks pass; Python compilation passes.
- PostgreSQL and API containers both report healthy.
- Running API: http://localhost:58000/docs
- PostgreSQL: localhost:55432 (the pre-existing service on 5432 was left untouched).
- Local `.env` records those port choices and is ignored by Git.

Environment limitations and workarounds:

- Sandboxed Starlette TestClient hangs while starting its event-loop thread.
  Tests pass outside the sandbox.
- Docker build networking cannot resolve PyPI, including with host networking.
  Normal online Dockerfile build therefore remains unverified here. For runtime
  verification, host-downloaded wheels for the same pinned requirements were installed
  with `--no-index --find-links` in a temporary build context. That image ran healthy
  under the project Compose configuration. Temporary files are under
  `/tmp/support-pilot-wheels` and `/tmp/support-pilot-offline-build`.
- Starlette emits an httpx TestClient deprecation warning. Tests pass; changing its
  test-client transport should be addressed when updating the framework pins.
- Windows instructions are supplied, but execution was verified on Linux only.
- CI configuration is present; no remote GitHub Actions run has occurred.

This validates the foundation only. No model, retrieval, authorization, ticket, handoff,
performance, or production-release gate has been claimed as passed.
