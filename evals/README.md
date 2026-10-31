# Retrieval evaluation

`retrieval.json` contains 26 synthetic scenarios: 18 answerable policy questions across
four domains and eight negative questions. The original dev/heldout groups are retained
for audit; there are no paraphrase pairs across splits.

Run `PYTHONPATH=backend python -m app.evaluate_retrieval` after full ingestion. It writes
`reports/retrieval.json` and exits nonzero if recall@5 is below 90%, a negative case is
accepted, or the configured threshold differs from the development-selected threshold.
The report also includes answerable questions rejected by the gate; passing retrieval
checks does not mean passing the planned end-to-end production gate.

The initial 0.5 threshold produced a false positive in the original held-out group.
`reports/initial-retrieval.json` preserves that result. The revised conservative floor
of 0.7 means the final dataset is a regression set, NOT untouched held-out validation.
Within that revised range, the threshold sweep uses only development labels. Fresh
independent labels are needed for a generalization claim. Do not silently tune on new
held-out failures and continue describing the same data as unseen.

Current measurements use deterministic demo hash vectors and real PostgreSQL full-text
and vector queries. They do not establish OpenAI semantic retrieval quality. Changing
embedding identity requires re-ingestion and a new report. The separate 50-case release evaluation is implemented in milestone 6; this remains the retrieval-specific regression set.


Milestone 5 also records `reports/retrieval-milestone5.json`. A full-suite failure exposed
random UUID tie ordering; ties now use stable article IDs and chunk positions. The
threshold and labels are unchanged. The refreshed regression report passes at 18/18
recall, 15/18 positive acceptance, and 8/8 negative rejection. An integration test remaps
chunk UUIDs and confirms that the complete set of query rankings remains unchanged.

## Full release benchmark

`scenarios.json` contains 15 policy, 10 order, 10 ticket, 10 handoff and five adversarial
cases. Expected evidence, actions, arguments, outcomes and forbidden actions are fixed.
Run `PYTHONPATH=backend python -m app.evaluate_release`; it migrates and seeds its own
schema, uses real HTTP routes and SQL observations, and removes only that schema.

`reports/milestone6/report.json` records automated results, with first-attempt and repeated
counts reported separately. `traces.json` contains allowlisted metadata; `human-review.json`
contains synthetic messages and source evidence for a human reviewer. Do not treat the
unfilled review packet as approval. The CLI exits successfully for machine gates only.
The initial failure report remains under `reports/milestone6-initial/`. Fixes used inspected
failures, so both partitions are now regression data; no unseen generalization claim is made.

See [review and calibration instructions](../docs/learning/milestone-6.md). An optional
external judge is advisory and cannot replace human labels or automatically authorize release.
