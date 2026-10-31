import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';
import type { Session } from './ConversationChat';

export type Handoff = { id: string; conversation_id: string; status: 'queued' | 'assigned' | 'resolved'; reason: string; summary: string; assigned_to: string | null; created_at: string; assigned_at: string | null; resolved_at: string | null };
type Detail = {
  handoff: Handoff; customer: { name: string; email: string };
  messages: { id: string; role: string; content: string; author_name: string | null }[];
  orders: { reference: string; status: string; estimated_delivery: string | null }[];
  tickets: { id: string; status: string; payload: { subject: string; description: string } }[];
  attempted_actions: { run_id: string; tool: string; arguments: { order_reference?: string }; result: { outcome?: string; order?: { status: string }; handoff?: { status: string } } }[];
  runs: { id: string; status: string; error_code: string | null }[];
};

export function SupportDashboard({ session, signingOut, onLogout, onExpired, sessionError }: {
  session: Session; signingOut: boolean; onLogout: () => void; onExpired: () => void; sessionError: string;
}) {
  const [status, setStatus] = useState<Handoff['status']>('queued');
  const [queue, setQueue] = useState<Handoff[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [text, setText] = useState('');
  const [pending, setPending] = useState<{ request_id: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const active = useRef<string | null>(null);
  const refreshVersion = useRef(0);
  function report(e: unknown) {
    if (e instanceof ApiError && e.status === 401) { onExpired(); return; }
    setError(e instanceof Error ? e.message : 'Unable to connect. Please retry.');
  }
  async function refresh() {
    const version = ++refreshVersion.current;
    const id = active.current;
    const [items, current] = await Promise.all([
      api<Handoff[]>(`/support/handoffs?status=${status}`),
      id ? api<Detail>(`/support/handoffs/${id}`) : Promise.resolve(null),
    ]);
    if (version === refreshVersion.current && id === active.current) { setQueue(items); setDetail(current); }
  }
  useEffect(() => {
    let disposed = false;
    const update = () => { if (!disposed) void refresh().catch(report); };
    update();
    const timer = window.setInterval(() => { if (!busy) update(); }, 4000);
    return () => { disposed = true; refreshVersion.current++; window.clearInterval(timer); };
  }, [status, selected, busy]);
  function select(id: string) {
    active.current = id; setSelected(id); setDetail(null); setText(''); setError('');
  }
  async function action(operation: 'claim' | 'resolve') {
    if (!selected) return;
    setBusy(true); setError('');
    try { await api(`/support/handoffs/${selected}/${operation}`, 'POST', {}, session.csrf_token); await refresh(); }
    catch (e) { report(e); await refresh().catch(report); }
    finally { setBusy(false); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!selected || (!pending && !text.trim())) return;
    const body = pending || { request_id: crypto.randomUUID(), message: text.trim() };
    setPending(body); setBusy(true); setError('');
    try {
      await api(`/support/handoffs/${selected}/replies`, 'POST', body, session.csrf_token);
      setPending(null); setText(''); await refresh();
    } catch (e) { report(e); }
    finally { setBusy(false); }
  }
  const mine = detail?.handoff.assigned_to === session.id;
  return <div className="support-dashboard">
    <div className="chat-header"><div><strong>Human support dashboard</strong><p>Signed in as {session.name}</p></div><button className="text-button" disabled={busy || signingOut} onClick={onLogout}>Sign out</button></div>
    {(error || sessionError) && <p role="alert" className="error">{error || sessionError}</p>}
    <div className="support-filters"><label>Queue status<select value={status} disabled={busy || !!pending} onChange={e => setStatus(e.target.value as Handoff['status'])}><option value="queued">Queued</option><option value="assigned">Assigned</option><option value="resolved">Resolved</option></select></label><button disabled={busy} onClick={() => void refresh().catch(report)}>Refresh queue</button></div>
    <ul className="handoff-queue" aria-label="Support queue">{queue.map(item => <li key={item.id}><button aria-pressed={selected === item.id} disabled={busy || !!pending} onClick={() => select(item.id)}><strong>{item.status}</strong> · {item.summary}<small>{new Date(item.created_at).toLocaleString()}</small></button></li>)}</ul>
    {!queue.length && <p>No {status} conversations.</p>}
    {queue.length === 100 && <p>Showing the first 100 conversations in this queue.</p>}
    {detail && <section aria-label="Support conversation" className="support-detail">
      <h2>{detail.customer.name}</h2><p>{detail.customer.email}</p>
      <p role="status">Handoff: {detail.handoff.status}{mine ? ' · Assigned to you' : detail.handoff.assigned_to ? ' · Assigned to another support agent' : ''}. Automated answers are paused.</p>
      <p><strong>Reason:</strong> {detail.handoff.reason.replaceAll('_', ' ')}</p><p><strong>Issue summary:</strong> {detail.handoff.summary}</p>
      {detail.handoff.status === 'queued' && <button disabled={busy} onClick={() => void action('claim')}>Claim conversation</button>}
      <div className="support-history" role="log" aria-label="Conversation history">{detail.messages.map(message => <article key={message.id} className={`bubble ${message.role === 'user' ? 'you' : 'support'}`}><span className="message-author">{message.role === 'support' ? `${message.author_name || 'Support'} · HUMAN` : message.role === 'user' ? detail.customer.name : 'SUPPORT PILOT'}</span><p>{message.content}</p></article>)}</div>
      <details><summary>Customer orders, tickets, and attempted actions</summary>
        <h3>Customer orders</h3>{detail.orders.map(order => <p key={order.reference}>{order.reference} · {order.status}{order.estimated_delivery ? ` · Estimated delivery ${new Date(order.estimated_delivery).toLocaleDateString()}` : ''}</p>)}
        <h3>Confirmed tickets</h3>{detail.tickets.map(ticket => <p key={ticket.id}>{ticket.id} · {ticket.status}<br />{ticket.payload.subject}: {ticket.payload.description}</p>)}
        <h3>Attempted actions</h3>{detail.attempted_actions.map((item, index) => <article className="attempted-action" key={index}>
          <strong>{item.tool.replaceAll('_', ' ')}</strong>
          {item.arguments.order_reference && <p>Order: {item.arguments.order_reference}</p>}
          {item.result.order && <p>Verified status at lookup: {item.result.order.status}</p>}
          {item.result.handoff ? <p>Queue status when requested: {item.result.handoff.status}</p> : <p>Outcome: {item.result.outcome || 'Recorded'}</p>}
        </article>)}
        <h3>Run outcomes</h3>{detail.runs.map(run => <p key={run.id}>{run.status}{run.error_code ? ` · ${run.error_code.replaceAll('_', ' ')}` : ''}</p>)}
      </details>
      {mine && (detail.handoff.status === 'assigned' || pending) && <form onSubmit={send}>
        <label>Human reply<textarea value={text} maxLength={2000} required disabled={busy || !!pending} onChange={e => setText(e.target.value)} /></label>
        {pending && <p>Retry this same reply safely; it will not be sent twice.</p>}
        <button disabled={busy || (!pending && !text.trim())}>{pending ? 'Retry saved reply' : 'Send human reply'}</button>
      </form>}
      {mine && detail.handoff.status === 'assigned' && <button disabled={busy || !!pending} onClick={() => void action('resolve')}>Resolve conversation</button>}
      {detail.handoff.status === 'resolved' && <p>This conversation remains closed to automation. The customer can start a new chat.</p>}
    </section>}
  </div>;
}
