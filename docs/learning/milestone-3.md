# Milestone 3: Grounded policy answers

Goal: trace how a Markdown policy becomes a searchable vector and an authorized,
versioned citation. Stop at this checkpoint before implementing ticket creation.

## 1. Concept lesson

**Retrieval-augmented generation (RAG)** adds retrieved evidence to an answering flow.
This implementation starts with extractive answers: the customer receives actual policy
sections, not new model prose. This makes grounding inspectable while you learn the
retrieval and update mechanics.

An **embedding** is a numeric vector representing a text. Similarity search ranks
vectors; it does not determine whether a statement is true. The demo adapter hashes
words into 256 dimensions, which is reproducible but does not understand meaning.
The separate OpenAI adapter can provide semantic embeddings after explicit configuration.

**Lexical retrieval** uses PostgreSQL's English text normalization and full-text rank.
Its GIN index supports text matching. This small-corpus implementation computes exact
rankings over compatible active rows; query tuning should follow measurements. It is
not BM25. **Dense retrieval** uses pgvector cosine distance. Exact search avoids the
recall/latency tradeoff of approximate indexes for our small corpus.

**Reciprocal rank fusion** adds `1 / (60 + rank)` for each candidate in each ranked list.
It compares positions rather than incompatible raw scores. A high RRF score means a
candidate ranks well; it is not a probability that an answer is correct.

The **evidence gate** checks term coverage separately. If it cannot find enough evidence,
the assistant asks for clarification. Handoff is not implemented yet, so it explicitly
does not claim a support transfer happened.

## 2. Read and trace the code

1. Open `knowledge/return-window.md`. Frontmatter records article identity, locale,
   and update time. Headings carry context into each chunk.
2. Open `backend/app/ingest.py`. `chunk_markdown` preserves heading ancestry, limits
   `cl100k_base` tokens, overlaps prose fragments, and repeats table headers. Oversized
   indivisible table rows fail ingestion rather than silently losing their headers.
3. Follow `ingest`: validate all files, fingerprint them, embed changed chunks, then
   activate the complete snapshot in one transaction. Missing files mean retired
   articles; do not point the command at a partial directory.
4. Read `embeddings.py`. The protocol is like a TypeScript interface: demo and OpenAI
   implementations satisfy the same contract. Neither the caller nor retrieval needs
   provider-specific HTTP details. Validation rejects malformed vectors.
5. Follow `Retriever.search` in `retrieval.py`: compatible active rows → dense and
   lexical top-20 rankings → RRF → top five candidates. Both rankings share one SQL
   snapshot. Read the `coverage` calculation separately from the `rrf` score.
6. Follow `answer`: choose supported sections, quote their exact contents, and attach
   immutable chunk/version IDs. The model does not invent citation IDs.
7. Read `agent.py`: `lookup_order_and_policy` runs lookup → policy → respond in one turn.
   Order ownership still lives in the domain service. Retrieval never grants order access.
8. Follow `DurableTurn.finalize`: `validate_current_citations` checks active versions
   and exact contents inside the final transaction. Why must that check happen again
   after a graph checkpoint has already been saved?
9. Follow the source endpoint in `api.py` and `citation_source`: it checks both ownership
   and whether the source was actually cited in the conversation. Then inspect `Sources`
   in `frontend/src/ConversationChat.tsx`: source content is displayed as escaped text.

## 3. Run the milestone

Docker startup and seed commands remain in the README. Ingest explicitly:

```sh
docker compose run --rm ingest
```

For host development with the virtual environment active:

```sh
# Linux/macOS
PYTHONPATH=backend python -m app.migrate
PYTHONPATH=backend python -m app.ingest
PYTHONPATH=backend python -m app.evaluate_retrieval
# PowerShell: first set $env:PYTHONPATH = 'backend', then run python -m ...
```

Sign in at http://localhost:58000. Try `What is the return window?`, expand its policy
source, and refresh. The citation remains. Try `SP-1001 return window` as Alice: the
answer includes an authorized order card and policy evidence. Bob must not receive
Alice's order facts. Ask about a 90-day trial membership: there is no such policy in
the corpus, so the assistant should clarify rather than invent one.

## 4. Exercise: update without breaking history

1. Save a return-window response and note its source version ID.
2. In a disposable copy of the complete knowledge directory, change the return window
   from 30 to 21 days. This is an exercise using synthetic policy, not business approval.
3. Ingest that directory. Ask a new question: it should use 21 days.
4. Open the old response's source: it should retain 30 days and say historical/superseded.
5. Re-ingest the original `knowledge` directory to restore the baseline.
6. Predict what happens if embedding fails halfway through staging. Verify your answer
   by reading `test_ingestion_idempotency_updates_deletions_and_failed_staging`.

Do not edit a live production policy this way. This exercise is for the synthetic local
corpus; production publication controls are future operational work.

## 5. Evaluate what actually improved

The dataset has 18 answerable and eight negative scenarios. The initial report is kept
beside the final report. Read both before celebrating a percentage:

- Expected article retrieved in the top five: 18/18.
- Conservative gate accepts answerable scenarios: 15/18; three ask for clarification.
- Negative scenarios rejected: 8/8 after the revision.
- These results use demo hash embeddings, not live OpenAI embeddings.

A failing initial held-out case informed the revision, so the final set is regression
evidence. It is not an untouched estimate of generalization. The planned 90% end-to-end
success release gate remains unmet; retrieval recall and answer success are different.
Run fresh independent examples and human review before claiming production quality.

## 6. Your explanation checkpoint

Explain these without reading the code:

1. Why do lexical and vector retrieval complement each other? Why is this not BM25?
2. What does RRF combine, and why is its score not answer confidence?
3. Where is the token limit enforced? How are table headers preserved?
4. What happens when one embedding call fails during ingestion?
5. Why must changing embedding dimensions trigger re-ingestion?
6. Why can an old conversation still display a retired policy, while a new answer cannot?
7. Which transaction prevents policy activation racing with citation publication?
8. Why is 18/18 recall compatible with only 15/18 accepted answers?
9. Why can't we still call the revised evaluation set untouched held-out evidence?
10. Which test proves policy retrieval and authorized order lookup work in the same run?

Architecture note: [0004 — grounded retrieval](../decisions/0004-grounded-retrieval.md).
Rewrite its interview explanation in your own words before milestone 4.
