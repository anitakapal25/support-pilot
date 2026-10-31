import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from './api';
import { TicketPanel } from './TicketPanel';
import type { Handoff } from './SupportDashboard';

export type Session = { id: string; role: 'customer' | 'support'; name: string; email: string; csrf_token: string };
type Order = { order_reference: string; status: string; estimated_delivery: string | null; checked_at: string };
type Citation = { id: string; title: string; section: string; updated_at: string; excerpt: string };
type Reply = { citations?: Citation[]; request_id: string; outcome: string; message: string; order: Order | null };
type Message = { id: string; run_id: string; sequence: number; role: 'user' | 'assistant' | 'support'; author_name?: string | null; content: string; result: Reply | null };
type Run = { id: string; request_id: string; status: 'pending' | 'running' | 'retryable' | 'completed' | 'failed'; response: Reply | null };
type Conversation = { id: string; created_at: string };
type Detail = Conversation & { handoff?: Handoff | null; messages: Message[]; runs: Run[] };
type Pending = { conversationId: string; requestId: string; message: string };
const unfinished = (run: Run) => ['pending', 'running', 'retryable'].includes(run.status);

export function ConversationChat({ session, signingOut, onLogout, onExpired, sessionError }: {
  session: Session; sessionError: string; signingOut: boolean; onLogout: () => void; onExpired: () => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [handoffBusy, setHandoffBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const creationId = useRef<string | null>(null);

  function report(e: unknown) {
    if (e instanceof ApiError && e.status === 401) { onExpired(); return; }
    setError(e instanceof Error ? e.message : 'Unable to connect. Please try again.');
  }

  async function load(id: string, preserve?: Pending) {
    const loaded = await api<Detail>(`/conversations/${id}`);
    setDetail(loaded);
    window.history.replaceState(null, '', `#chat=${id}`);
    const run = loaded.runs.find(unfinished);
    const message = run && loaded.messages.find(item => item.run_id === run.id && item.role === 'user');
    if (run && message) {
      setPending({ conversationId: id, requestId: run.request_id, message: message.content });
      setNotice(run.status === 'retryable' ? 'Your message is saved. Resume the request to try again.' : 'Your request is saved. Check its status or resume if it was interrupted.');
    } else {
      // A dropped response may precede acceptance. Keep that same ID for the next retry.
      setPending(preserve && !loaded.runs.some(item => item.request_id === preserve.requestId) ? preserve : null);
      setNotice('');
    }
  }

  async function newChat() {
    setBusy(true); setError('');
    try {
      creationId.current ||= crypto.randomUUID();
      const conversation = await api<Conversation>('/conversations', 'POST', { request_id: creationId.current }, session.csrf_token);
      creationId.current = null;
      setConversations(await api<Conversation[]>('/conversations'));
      await load(conversation.id);
      setQuestion('');
    } catch (e) { report(e); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    let disposed = false;
    async function restore() {
      try {
        const list = await api<Conversation[]>('/conversations');
        if (disposed) return;
        setConversations(list);
        const selection = new URLSearchParams(window.location.hash.slice(1)).get('chat');
        const selected = list.find(item => item.id === selection) || list[0];
        if (selected) await load(selected.id);
        // An empty account waits for its first explicit New chat or Send action.
      } catch (e) { if (!disposed) report(e); }
      finally { if (!disposed) setBusy(false); }
    }
    void restore();
    return () => { disposed = true; };
  }, [session.email]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'auto', block: 'nearest' }); }, [detail, pending, busy]);

  useEffect(() => {
    if (!detail?.handoff || busy) return;
    let disposed = false;
    const id = detail.id;
    const timer = window.setInterval(() => {
      void api<Detail>(`/conversations/${id}`).then(loaded => {
        if (!disposed) setDetail(current => current?.id === id ? loaded : current);
      }).catch(e => { if (!disposed) report(e); });
    }, 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [detail?.id, !!detail?.handoff, busy]);

  async function askHuman() {
    if (!detail) return;
    const id = detail.id;
    setHandoffBusy(true); setError('');
    try {
      const summary = question.trim() || [...detail.messages].reverse().find(item => item.role === 'user')?.content || 'Customer requested human assistance.';
      const handoff = await api<Handoff>(`/conversations/${id}/handoff`, 'POST', { reason: 'customer_request', summary: summary.slice(0, 1000) }, session.csrf_token);
      setDetail(current => current?.id === id ? { ...current, handoff } : current);
    } catch (e) { report(e); }
    finally { setHandoffBusy(false); }
  }

  async function submit(turn: Pending) {
    setBusy(true); setError(''); setNotice(''); setPending(turn);
    try {
      try {
        await api<Run>(`/conversations/${turn.conversationId}/messages`, 'POST', { request_id: turn.requestId, message: turn.message }, session.csrf_token);
      } catch (e) {
        // A retryable/failed run is a saved result even when the HTTP response is 503.
        if (!(e instanceof ApiError && e.status === 503 && (e.data as Run | undefined)?.status)) report(e);
      }
      await load(turn.conversationId, turn);
    } catch (e) { report(e); }
    finally { setBusy(false); }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy || pending || detail?.handoff?.status === 'resolved') return;
    setBusy(true); setError('');
    let conversation = detail;
    if (!conversation) {
      try {
        creationId.current ||= crypto.randomUUID();
        const created = await api<Conversation>('/conversations', 'POST', { request_id: creationId.current }, session.csrf_token);
        creationId.current = null;
        conversation = { ...created, messages: [], runs: [] };
        setDetail(conversation);
        setConversations(await api<Conversation[]>('/conversations'));
        window.history.replaceState(null, '', `#chat=${created.id}`);
      } catch (e) { report(e); setBusy(false); return; }
    }
    setQuestion('');
    await submit({ conversationId: conversation.id, requestId: crypto.randomUUID(), message: text });
  }

  async function selectConversation(id: string) {
    setBusy(true); setError('');
    try { await load(id, pending?.conversationId === id ? pending : undefined); setQuestion(''); } catch (e) { report(e); }
    finally { setBusy(false); }
  }

  const extraPending = pending && !detail?.runs.some(run => run.request_id === pending.requestId);
  return <div className="chat-view">
    <div className="chat-header"><div><span className="online-dot" /><strong>Customer support</strong><p>Signed in as {session.name}</p></div><button className="text-button" disabled={busy || signingOut} onClick={onLogout}>Sign out</button></div>
    <div className="conversation-controls">
      <label className="sr-only" htmlFor="conversation">Conversation</label>
      <select id="conversation" value={detail?.id || ''} disabled={busy || !conversations.length} onChange={e => void selectConversation(e.target.value)}>
        {!detail && <option value="">Your conversations</option>}
        {conversations.map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.id.slice(0, 4)}</option>)}
      </select>
      <button className="text-button" disabled={busy} onClick={() => void newChat()}>New chat</button>
    </div>
    {detail && <section className="handoff-status" aria-label="Human support status">
      {detail.handoff ? <p role="status">{detail.handoff.status === 'queued' ? 'Queued for human support. Automated answers are paused.' : detail.handoff.status === 'assigned' ? 'A support agent has claimed your conversation. Automated answers are paused.' : 'Human support has resolved this conversation. Start a new chat for automated help.'}</p> : <button disabled={handoffBusy} onClick={() => void askHuman()}>{handoffBusy ? 'Requesting support…' : 'Request human support'}</button>}
    </section>}
    <div className="messages" role="log" aria-label="Messages" aria-live="polite">
      <article className="bubble support"><span className="message-author">SUPPORT PILOT</span><p>Hi {session.name}. Send your order reference for a status check, or ask about shipping, returns, damaged items, or account help.</p></article>
      {detail?.messages.map(message => <article key={message.id} className={`bubble ${message.role === 'user' ? 'you' : 'support'} ${message.result?.outcome === 'error' ? 'failed' : ''}`}>
        <span className="message-author">{message.role === 'user' ? 'YOU' : message.role === 'support' ? `${message.author_name || 'Support'} · HUMAN` : 'SUPPORT PILOT'}</span><p>{message.content}</p>
        {message.result?.order && <OrderCard order={message.result.order} />}
        {!!message.result?.citations?.length && detail && <Sources conversationId={detail.id} citations={message.result.citations} />}
      </article>)}
      {extraPending && <article className="bubble you"><span className="message-author">YOU</span><p>{pending.message}</p></article>}
      {busy && <p className="checking" role="status">Checking…</p>}<div ref={end} />
    </div>
    {(error || sessionError) && <p role="alert" className="error chat-error">{error || sessionError}</p>}
    {pending && !busy && <div className="recovery"><p role="status">{notice || 'The response was interrupted. Retry this saved request safely.'}</p><button onClick={() => void submit(pending)}>Resume request</button><button onClick={() => void selectConversation(pending.conversationId)}>Check status</button></div>}
    {detail && <TicketPanel key={detail.id} conversationId={detail.id} csrf={session.csrf_token} disabled={busy || !!pending} onExpired={onExpired} />}
    <form className="composer" onSubmit={send}><label className="sr-only" htmlFor="question">Your message</label><input id="question" placeholder="Where is my order SP-1001?" maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} disabled={busy || !!pending || detail?.handoff?.status === 'resolved'} /><button className="send" disabled={busy || !!pending || detail?.handoff?.status === 'resolved' || !question.trim()} aria-label="Send message">↑</button></form>
    <p className="chat-note">Your conversation is saved. Start a new chat to change topics.</p>
  </div>;
}

function OrderCard({ order }: { order: Order }) {
  return <div className="order-card" aria-label="Verified order">
    <div className="order-heading"><strong>{order.order_reference}</strong><span className="verified">✓ Verified</span></div>
    <dl><div><dt>Status</dt><dd className="capitalize">{order.status}</dd></div>{order.estimated_delivery && <div><dt>Estimated delivery</dt><dd>{new Date(order.estimated_delivery).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</dd></div>}</dl>
    <small>Checked {new Date(order.checked_at).toLocaleString()}</small>
  </div>;
}


function Sources({ conversationId, citations }: { conversationId: string; citations: Citation[] }) {
  const [source, setSource] = useState<{ id: string; title: string; section: string; content: string; active: boolean; updated_at: string } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function open(id: string) {
    setBusy(true); setError('');
    try { setSource(await api(`/conversations/${encodeURIComponent(conversationId)}/sources/${encodeURIComponent(id)}`)); }
    catch { setError('This source could not be loaded. Please try again.'); }
    finally { setBusy(false); }
  }
  return <div className="citations" aria-label="Policy sources">
    <strong>Policy sources</strong>
    {citations.map((item, index) => <button key={item.id} disabled={busy} onClick={() => void open(item.id)}>[{index + 1}] {item.title}</button>)}
    {error && <p role="alert">{error}</p>}
    {source && <section className="source-detail" aria-label="Citation source">
      <strong>{source.title}</strong><p>{source.section}</p>
      <small>{source.active ? 'Current policy' : 'Historical policy — superseded'} · Updated {new Date(source.updated_at).toLocaleDateString()}</small>
      <pre>{source.content}</pre><button onClick={() => setSource(null)}>Close source</button>
    </section>}
  </div>;
}
