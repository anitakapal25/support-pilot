# 0004: Versioned hybrid retrieval with extractive policy answers

Status: accepted for milestone 3, 2026-10-29.

## Problem and decision

Order facts come from a transactional service, but policies come from a versioned
knowledge corpus. A model's plausible recollection is not evidence of store policy.
Use 18 explicitly synthetic articles, section-aware chunks, exact vector retrieval,
PostgreSQL English full-text ranking, and reciprocal rank fusion (RRF). There is no
BM25 implementation here and no approximate vector index at this corpus size.

Use a separate embedding adapter. Development hashing exercises the vector storage
and query path without spending API credit; it is not a semantic embedding model.
OpenAI embeddings are configurable by model and dimension, validated for count,
index, dimension, finiteness, and nonzero norm. Both branches filter by the stored
embedding identity, locale, and active version. Changing models requires full ingestion
and fresh evaluation; there is no fallback to incompatible vectors.

The provider chooses policy retrieval, order lookup, or both. The application supplies
the original customer question to retrieval, not arbitrary model-generated SQL or URLs.
Policy replies contain at most two exact source excerpts from the top five retrieved
sections, with distinct article IDs. This sacrifices conversational synthesis for an
inspectable grounding boundary. RRF scores sort candidates; lexical term coverage and
a minimum of two matching terms form a separate conservative evidence gate.

## Ingestion and citation consistency

A complete input directory is one authoritative snapshot. Parse and embed changed
articles before any mutation. Activate all staged versions in one short transaction
under a corpus advisory lock. Missing articles become inactive. Unchanged fingerprints
reuse vectors; the fingerprint includes content, embedding identity, and chunk settings.
A failed embedding or database transaction leaves the prior active snapshot intact.

Each citation points to an immutable chunk/version. Before publishing a new reply, lock
its version rows for sharing and verify active status and exact excerpt metadata. Corpus
activation waits until publication commits. A stale recovered checkpoint produces a
clarification, never a newly published obsolete policy quotation. Already completed
request replay intentionally preserves its historical response.

The source API requires conversation ownership and proof that the chunk was cited in
that conversation. Historical versions remain readable through those citations and are
marked superseded. The browser renders text, not document HTML, and builds only fixed
same-origin source API paths. No source-supplied URL is opened.

## Evidence gate tradeoff

The first threshold of 0.5 retrieved all expected articles but accepted one unsupported
scenario. Preserve `evals/reports/initial-retrieval.json`. A conservative minimum of 0.7
now rejects all eight negative scenarios but clarifies three of the 18 answerable ones.
The calibration runner compares thresholds 0.7–1.0 on development labels only. Because
an initial held-out result motivated this revision, the final dataset is honestly a
regression suite, even though its original split names remain for audit. Fresh independent
validation is required before claiming generalization or a production quality gate.

The retrieval gate (recall@5 >= 90%) is distinct from answer acceptance and end-to-end
task success. The latter has NOT met the planned 90% release target. Coverage does not
prove entailment, handle every adversarial query, or establish calibrated probabilities.
The corpus and labels are synthetic; business approval and human review remain necessary.

## Alternatives deferred

No reranker, generated answer synthesis, ANN index, autonomous handoff, or external
write tool is needed for this milestone. Measure failure categories first. Retention
must eventually clean checkpoints and unreferenced historical versions together.

## Interview explanation

“I combine lexical and vector rankings because exact policy terms and semantic
similarity fail differently. I keep ranking separate from the decision to answer.
Versioned sources and publication-time validation make citations auditable even when
policies change during a request. Extractive replies reduce unsupported generation,
but retrieval relevance still needs evaluation and a conservative refusal path.”

References: [OpenAI embeddings](https://developers.openai.com/api/docs/guides/embeddings),
[pgvector exact and hybrid search](https://github.com/pgvector/pgvector).
