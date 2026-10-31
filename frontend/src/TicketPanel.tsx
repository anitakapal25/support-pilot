import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';

type Details = { subject: string; description: string; order_reference: string | null };
type Ticket = { id: string; status: string; payload: Details };
type Draft = { id: string; status: string; payload: Details; expires_at: string; ticket: Ticket | null };

export function TicketPanel({ conversationId, csrf, disabled, onExpired }: {
  conversationId: string; csrf: string; disabled: boolean; onExpired: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [subject, setSubject] = useState('');
  const [description, setDescription] = useState('');
  const [order, setOrder] = useState('');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const attempt = useRef<{ key: string; requestId: string } | null>(null);
  const confirmation = useRef<string | null>(null);
  function report(e: unknown) {
    if (e instanceof ApiError && e.status === 401) { onExpired(); return; }
    setError(e instanceof Error ? e.message : 'Unable to save ticket. Please retry.');
  }
  useEffect(() => {
    let disposed = false;
    Promise.all([
      api<Draft | null>(`/conversations/${conversationId}/ticket-draft`),
      api<Ticket[]>(`/conversations/${conversationId}/tickets`),
    ]).then(([value, saved]) => { if (!disposed) { setDraft(value); setTickets(saved); } })
      .catch(e => { if (!disposed) report(e); })
      .finally(() => { if (!disposed) setBusy(false); });
    return () => { disposed = true; };
  }, [conversationId]);
  async function prepare(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    const payload = { subject: subject.trim(), description: description.trim(), order_reference: order.trim() || null };
    const key = JSON.stringify(payload);
    if (attempt.current?.key !== key) attempt.current = { key, requestId: crypto.randomUUID() };
    try {
      const saved = await api<Draft>(`/conversations/${conversationId}/ticket-drafts`, 'POST', { ...payload, request_id: attempt.current.requestId }, csrf);
      setDraft(saved); setEditing(false); confirmation.current = null;
    } catch (e) { report(e); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!draft) return;
    setBusy(true); setError(''); confirmation.current ||= crypto.randomUUID();
    try {
      const ticket = await api<Ticket>(`/conversations/${conversationId}/ticket-drafts/${draft.id}/confirm`, 'POST', { request_id: confirmation.current, confirmed: true }, csrf);
      setDraft({ ...draft, status: 'confirmed', ticket });
      setTickets(items => [...items.filter(item => item.id !== ticket.id), ticket]);
    } catch (e) { report(e); }
    finally { setBusy(false); }
  }
  const expired = !!draft && new Date(draft.expires_at).getTime() <= Date.now();
  return <section className="ticket-panel" aria-label="Support ticket">
    <strong>Support ticket</strong>
    {error && <p role="alert">{error}</p>}
    {draft?.ticket && <p role="status">Ticket created: <strong>{draft.ticket.id}</strong> · {draft.ticket.status}</p>}
    {draft && !editing && <div>
      <h3>{draft.payload.subject}</h3><p className="ticket-description">{draft.payload.description}</p>
      {draft.payload.order_reference && <p>Order: {draft.payload.order_reference}</p>}
      {draft.status === 'pending' && !draft.ticket && <>
        <p>{expired ? 'Draft expired. Review a new draft.' : 'Review these exact details. Confirming creates one support ticket.'}</p>
        <button className="ticket-confirm" disabled={disabled || busy || expired} onClick={() => void confirm()}>Confirm and create ticket</button>
      </>}
    </div>}
    {!!tickets.filter(ticket => ticket.id !== draft?.ticket?.id).length && <details>
      <summary>Earlier tickets in this conversation</summary>
      {tickets.filter(ticket => ticket.id !== draft?.ticket?.id).map(ticket => <p key={ticket.id}>{ticket.payload.subject} · {ticket.id} · {ticket.status}</p>)}
    </details>}
    {!editing && <button disabled={disabled || busy} onClick={() => {
      setSubject(draft?.payload.subject || ''); setDescription(draft?.payload.description || '');
      setOrder(draft?.payload.order_reference || ''); attempt.current = null; setEditing(true);
    }}>{draft?.ticket ? 'New ticket' : draft ? 'Edit details' : 'Describe an issue'}</button>}
    {editing && <form onSubmit={prepare}>
      <label>Subject<input required minLength={3} maxLength={160} value={subject} onChange={e => setSubject(e.target.value)} disabled={busy} /></label>
      <label>Issue details<textarea required minLength={10} maxLength={2000} value={description} onChange={e => setDescription(e.target.value)} disabled={busy} /></label>
      <label>Order reference (optional)<input maxLength={64} pattern="[A-Za-z0-9-]+" value={order} onChange={e => setOrder(e.target.value)} disabled={busy} /></label>
      <button disabled={disabled || busy}>Review ticket</button>
      <button type="button" disabled={busy} onClick={() => setEditing(false)}>Cancel editing</button>
    </form>}
  </section>;
}
