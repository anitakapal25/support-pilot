# Milestone 6 — Evaluation and security

The implementation adds a repeatable release benchmark, adversarial checks, redacted request traces, transactional action audits, token limits, and human/judge review tooling. Automated synthetic-demo gates pass. Full release signoff remains pending independent human review; live model behavior and production latency have not been measured.

## Measured result

On 2026-10-31: 72 backend tests, 14 desktop/mobile browser tests, and all 146 evaluation
trials passed. Citation validity was 22/22, unique labeled retrieval recall 16/16, and unique
tool cases 31/31. Five concurrent conversations completed 25/25 requests with p95 313.776 ms.
These are local demo measurements. See [verification notes](../verification.md) for scope.

## The concept

Think of an evaluation as an integration test plus a labeled outcome. A response saying “ticket created” is insufficient: find its database row, confirm its payload matches the explicitly confirmed draft, and check that retrying did not add another ticket. Similarly, a valid citation ID proves referential integrity, while a person must still assess whether that evidence actually answers the question.

Three kinds of evidence serve different purposes:

- Domain records prove durable business effects and ownership.
- Redacted request events explain routing, retrieval, retries and response validation.
- Review packets contain synthetic conversations and source excerpts for factual-support and resolution judgments. They are separate from production telemetry.

## Run the checks

From the repository root, activate `.venv`, configure `DATABASE_URL` for the local database, and set `PYTHONPATH=backend` (PowerShell: `$env:PYTHONPATH='backend'`).

```bash
python -m app.migrate
python -m app.evaluate_release
python -m app.inspect_trace SERVER_REQUEST_UUID
```

The evaluation CLI always uses demo routing and demo embeddings, even when `.env` selects OpenAI. It creates and cleans up only its own temporary schema. It does not overwrite application conversations. Its exit status represents **machine gates**, not completed human release review. There is no paid-provider switch in this milestone's CLI.

Artifacts under `evals/reports/milestone6/`:

- `report.json`: raw counts, splits/categories, 50 unique first attempts, 146 total trials, latency, cost, consistency, gates and pending signoff.
- `traces.json`: request IDs, source IDs, safe events and timing for every trial, including failures.
- `human-review.json`: 50 synthetic response/evidence bundles with empty labels.

The first failing report is preserved under `milestone6-initial/`. Five policy questions failed the lexical evidence gate despite correct retrieval. Normalizing duration questions and parcel/package wording, and excluding generic “find,” fixed these without lowering the evidence threshold. An account-word routing ambiguity in an adversarial order request was also corrected. The inspected held-out split is now a regression split, not fresh independent validation.

## Walk through the code

1. `eval_dataset.py` rejects duplicate IDs, bad counts, unknown flows/actions, and paraphrase groups crossing splits.
2. `eval_runner.py` signs into real API clients and executes inputs without consulting expected labels. It verifies durable effects, ownership, replay behavior, human authorship, citations and action audits.
3. `evaluate_release.py` scores observations against labels. Safety and critical-repeat gates require every case to pass; other rates are checked on unique first attempts as well as all trials. It measures five synchronized conversations over 25 requests.
4. `telemetry.py` allowlists event fields and caps events per request. `main.py` attaches `X-Request-ID`; business services insert audit rows inside their existing transactions. `inspect_trace.py` shows the trace alongside committed audits.
5. `provider.py` checks estimated input tokens before sending the model request and sets the configured output limit. Provider-reported usage feeds estimates; unknown pricing stays null.
6. `review_release.py` validates human labels against unchanged evidence and optionally compares advisory judge labels with them.

## Human review and judge calibration

Copy the generated review packet to a separate file. A reviewer reads every question, answer and cited source, sets `grounded` and `resolved` to booleans for all 50 cases, supplies `reviewer` and an ISO `reviewed_at`, and sets `attested_human_review` to true. For denied or unsupported actions, resolution means the expected safe outcome, not carrying out the prohibited request. Do not change the evidence fields.

```bash
python -m app.review_release \
  --original evals/reports/milestone6/human-review.json \
  --reviewed /path/to/completed-review.json \
  --output evals/reports/milestone6/human-results.json
```

An optional `--judge /path/to/judge-labels.json` accepts the same `run_id`, `dataset_sha256`, and unique case IDs with boolean labels. Calibration requires enough positive and negative examples, at least 90% agreement and kappa ≥0.8. It reports advisory results; it never supplies missing human labels or edits the original release report to claim approval. No LLM judge is called automatically.

## Exercise and explanation checkpoint

Add a paraphrase within an existing scenario group in a separate experimental dataset. Predict whether it should retrieve the same evidence before running it. Do not alter the fixed benchmark's expected labels to make failures pass. Compare a `provider.failure` trace with a queued handoff's committed audit.

Explain: Why is a 100% citation-validity score insufficient to prove grounding? Why must repeated cases not dominate unique-case scores? Why can trace persistence fail while a ticket remains committed? What does zero demo cost tell us about production cost?

See [ADR 0007](../decisions/0007-evaluation-and-telemetry.md) and [the measured release report](../../evals/reports/milestone6/report.json). Deployment, retention, spending reservations and recovery drills remain milestone 7 work.
