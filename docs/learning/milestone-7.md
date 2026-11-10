# Milestone 7 — Operations and the hosted demo

Milestone 7 adds the operational controls a deployment needs: shared request and spend
admission, a pilot switch, retention, backup/restore rehearsal and CI release gates. It
then packages the app as a **public demo on Render's free tier**. Read
[ADR 0008](../decisions/0008-hosted-demo.md) for the reasoning behind each choice.

## What changed for the hosted demo

| Gap from the deployment review | Resolution |
|---|---|
| Login throttle keyed on the proxy's IP | `FORWARDED_ALLOW_IPS` passed to Uvicorn ([serve.py](../../backend/app/serve.py)) |
| Shared demo accounts leak chats | Per-visitor guest groups ([demo.py](../../backend/app/demo.py)), support scoped by group ([handoffs.py](../../backend/app/handoffs.py)) |
| No pre-deploy step on free tier | `RUN_MIGRATIONS_ON_START` migrates and ingests before serving |
| Retention never scheduled | [maintenance.yml](../../.github/workflows/maintenance.yml) calls `/api/internal/maintenance` every 6 hours |
| No alerts | That workflow fails on readiness failure or spend ≥ 80% of budget; GitHub emails you |
| No live-model evidence | `python -m app.evaluate_release --live` (you run it; it costs money) |
| Human review never done | Still yours to do; see below |

## Critical code path: a visitor clicks "Try the demo"

1. `POST /api/demo/session` → `demo.create_guest`. It returns 404 unless `APP_ENV=demo`, then checks the
   origin and `X-Requested-With`, like login.
2. `admit_guest` increments two PostgreSQL counters (`guest-ip:<ip>:<hour>` and
   `guest-day:<date>`) with `operations.consume`, inside the same transaction.
3. It creates three users sharing a new `demo_group`, plus two orders with random references in
   `SP-3000..8999`, then calls `auth.start_session` (the same helper password login uses).
4. Every later request resolves a `Principal` that carries `demo_group`. Customer data is
   already owner-scoped. Support functions call `visible_to`, which compares the conversation
   customer's group with the agent's.
5. `POST /api/demo/switch` rotates the cookie to the group's other member.
6. After `GUEST_TTL_HOURS`, `purge_expired_guests` deletes the group's conversations (each
   under its advisory lock), orders, audit rows, traces and users.

## Deploy runbook (Render free tier)

1. **CI first.** Push to GitHub and confirm the `ci` workflow passes. Render deploys only
   commits whose checks passed (`autoDeployTrigger: checksPass`).
2. **Blueprint.** Render dashboard → New → Blueprint → select the repository and set
   *Blueprint path* to `deploy/render.yaml`. Fill the `sync: false` values:
   - `OPENAI_API_KEY`, `OPENAI_MODEL` (a small tool-calling model)
   - `MODEL_INPUT_USD_PER_MILLION`, `MODEL_OUTPUT_USD_PER_MILLION`, `EMBEDDING_USD_PER_MILLION`
     from OpenAI's current price page. Startup refuses to run without them.
   - `ALLOWED_ORIGINS` as a JSON list of the service URL, e.g. `["https://support-pilot.onrender.com"]`.
     If Render assigns a different subdomain, use that and redeploy.
3. **First boot** migrates, embeds the 18 articles (a fraction of a cent) and becomes ready.
   If it fails, read the deploy logs. Configuration errors name the missing setting.
4. **GitHub secrets.** Copy the generated `MAINTENANCE_TOKEN` from the Render environment page
   into the repository secret `MAINTENANCE_TOKEN`, and add `SUPPORT_PILOT_URL`
   (e.g. `https://support-pilot.onrender.com`, no trailing slash). Run *hosted-demo-maintenance*
   once with **Run workflow**.
5. **Smoke test** (two browsers or a private window):
   - Try the demo → ask about your order: a verified status appears.
   - Ask about the other order shown in the banner: "not available for your account".
   - "What is the return window?": an answer with policy sources.
   - Create and confirm a ticket.
   - Request a human → Support view → your handoff is listed; the other browser's is not.

## Operating it

- **Cold start:** after about 15 idle minutes the instance sleeps. The first visit then takes about a minute.
- **Spend:** reservations count against `MONTHLY_MODEL_BUDGET_USD` (5). When it is exhausted,
  automated answers stop and human-support requests still work. The maintenance run warns at 80%.
- **Pause automation:** set `PILOT_ENABLED=false` in Render and save; the service restarts.
- **Rotate the maintenance token:** Render → environment → *Generate*, then update the GitHub secret.
- **Database expiry (every 30 days):** Render emails before the free database expires. Delete
  it, re-sync the Blueprint so a new `support-pilot-db` is created, then redeploy. Startup
  rebuilds schema and knowledge; guest data is intentionally not preserved. Nothing to restore.
- **Scheduled workflows** are disabled by GitHub after 60 days without repository activity;
  re-enable them from the Actions tab.

## Still open (yours)

1. **Live evaluation.** With `.env` set to `AGENT_PROVIDER=openai`, `EMBEDDING_PROVIDER=openai`,
   key, model and the three price variables:
   `PYTHONPATH=backend python -m app.evaluate_release --live`. It runs each of the 50
   scenarios once (critical cases are not repeated) plus a 25-request concurrency probe, and writes
   `evals/reports/live/`. Also run `python -m app.evaluate_retrieval` against an OpenAI-ingested
   database, and record whether the 0.7 evidence floor still separates answerable from
   unanswerable questions. Commit the reports whatever the result.
2. **Human review.** Open `evals/reports/live/human-review.json`, set `grounded` and `resolved`
   for each case after reading its messages and sources, then fill `reviewer`, `reviewed_at`
   and `attested_human_review: true`. No one else can attest this for you.
3. **Not a customer pilot.** Real customers need the production profile, a real commerce and
   ticket integration, staffed support with notifications, and a paid database with backups.

## Checkpoint

Explain, without the code open: why would sharing one demo account let a single visitor deny
service to everyone, and which two changes in this milestone prevent it? Then trace what
happens when the maintenance job runs while a guest's agent run is still `running`.
