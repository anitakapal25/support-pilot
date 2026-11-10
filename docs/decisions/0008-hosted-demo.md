# ADR 0008: Public hosted demo on Render's free tier

Status: accepted for the portfolio demo, 2026-11-10.

The deployment goal is a public demo anyone can try, using synthetic data and the live
OpenAI router. It is not a customer pilot. `APP_ENV=demo` is a third profile: it is internet-facing,
so its configuration validator applies every production guard (HTTPS origins, secure cookies,
OpenAI router and embeddings, explicit prices, request and spend limits). It additionally allows
synthetic guest accounts. The offline `AGENT_PROVIDER=demo` router is unrelated and is still
rejected there.

**Per-visitor guest groups, not shared demo logins.** Shared accounts would show every
visitor's chats to every other visitor, and the per-account login throttle would let one
person lock everyone out. `POST /api/demo/session` creates a private group: the visitor's
customer, a neighbour customer whose order demonstrates non-disclosure, and a support agent.
Support queue, detail, claim, reply and resolve all require the conversation's customer to
share the agent's `demo_group` (`IS NOT DISTINCT FROM`, so regular staff see only non-guest
work and guests see only their own). Guests have an unusable password hash and password
login rejects them. Creation is rate limited per client IP and per day with the existing
shared PostgreSQL counters. Guest sessions last at most `GUEST_TTL_HOURS`; maintenance then
deletes the group with the same locked per-conversation purge that retention uses, skipping
any conversation with an unfinished run.

**Client IP behind the proxy.** Uvicorn ignores `X-Forwarded-For` unless the peer is trusted,
so every request appeared to come from Render's proxy. Login and guest limits keyed on that
address would have become one global bucket. `FORWARDED_ALLOW_IPS` is explicit; the Blueprint
sets `*` because only the platform proxy can reach the container. The default stays loopback.

**Free-tier substitutes.** There is no pre-deploy command, so `RUN_MIGRATIONS_ON_START` runs
Alembic, checkpoint setup and idempotent ingest before Uvicorn binds (unchanged articles are
not re-embedded). There are no cron jobs, so a GitHub Actions schedule calls a bearer-token
maintenance endpoint (constant-time comparison; 404 when no token is configured). It fails,
and GitHub emails the owner, when readiness fails or reserved spend reaches 80% of the
monthly budget. The free database expires after 30 days and has no backups; the data is
synthetic and short-lived, so the runbook recreates it instead of restoring it.

Trade-offs accepted: idle sleep makes the first request slow; the monthly model budget
(US$5) can be exhausted by abuse, which pauses automated answers rather than spending more;
an IP limit can be shared by visitors behind one NAT. Real customers would need the
production profile, provisioned accounts, real commerce and ticket integrations, staffed
support, completed human review and a paid database with backups.
