# 0005: Explicitly confirmed, transactional synthetic tickets

Status: accepted for milestone 4.

A ticket write needs evidence that the authenticated customer approved the exact
payload. Neither model output nor conversational “yes” provides an unambiguous binding
between permission and mutable draft details.

Use a typed form to collect subject, description, and an optional authorized order.
Persist an immutable server-issued draft. The separate confirmation endpoint requires
`confirmed: true`, a draft reference in the URL, session authentication, and CSRF.
The routing provider can select `prepare_ticket` to explain this flow; the write domain
tool `create_ticket` is invoked by the confirmation endpoint. This deliberately keeps
write authorization outside model routing and graph replay. The current provider is a
bounded action selector; adding a model-visible write would provide no useful autonomy
for this form-confirmed operation.

Serialize draft/confirmation writers with a PostgreSQL conversation row lock. Store
confirmation request receipts, the confirmation timestamp, and the ticket atomically.
Compare normalized draft payloads for preparation replay; bind confirmation request IDs
to draft IDs for confirmation replay. A unique ticket per draft protects against retries
using new request IDs. Supersede old pending drafts on edit, expire pending drafts after
24 hours, and recheck order ownership when confirming. Return an existing confirmed
ticket even after its draft expiry, because that is a read of an already committed effect.

Persisted receipts and ticket reads survive process restarts and dropped HTTP responses.
The API returns success only after transaction commit; database errors return 503.
The UI restores the latest draft and ticket, preserves request IDs during network retries,
and lists earlier tickets in the conversation. Arbitrary text remains escaped React text.

Tradeoffs: explicit review adds a customer step and does not support free-text confirmation.
Pending draft expiry is checked on access; no cleanup worker is introduced here. Tickets
are local synthetic records, with no external notification or human assignment. Future
external adapters require provider idempotency and/or an outbox/reconciliation design;
this transaction cannot make remote side effects atomic with PostgreSQL.

Validation covers strict confirmation/schema checks, ownership/CSRF, stale and expired
drafts, conflicting operation IDs, concurrent confirmations, rollback, persistent replay,
and the desktop/mobile lost-response workflow. Test migrations explicitly scope their
version table to each test schema, avoiding the public version-table fallback.

Interview explanation: “I bind confirmation to an immutable draft, then commit its
receipt and ticket together. A unique draft key and database lock make concurrent retries
return one persisted result. A failed or lost response never becomes evidence of success;
the next request reads the committed result or retries a rolled-back operation.”
