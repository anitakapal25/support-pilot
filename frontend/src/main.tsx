import { StrictMode, useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { api, ApiError } from './api';
import { ConversationChat, type Session } from './ConversationChat';
import { SupportDashboard } from './SupportDashboard';

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [demo, setDemo] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    api<{ demo: boolean }>('/config').then(data => { if (mounted) setDemo(data.demo); }).catch(() => {});
    api<Session>('/auth/me').then(user => { if (mounted) setSession(user); }).catch(e => {
      if (mounted && (!(e instanceof ApiError) || e.status !== 401)) setError('Unable to connect. Please try signing in again.');
    }).finally(() => { if (mounted) setInitializing(false); });
    return () => { mounted = false; };
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      setSession(await api<Session>('/auth/login', 'POST', { email, password }));
      setPassword('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to sign in.'); }
    finally { setBusy(false); }
  }

  async function logout() {
    if (!session) return;
    setBusy(true); setError('');
    try {
      await api('/auth/logout', 'POST', undefined, session.csrf_token);
      setSession(null); window.history.replaceState(null, '', window.location.pathname);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { setSession(null); window.history.replaceState(null, '', window.location.pathname); }
      else setError(e instanceof Error ? e.message : 'Unable to sign out.');
    } finally { setBusy(false); }
  }

  const Chat = session?.role === 'support' ? SupportDashboard : ConversationChat;
  return <div className="shell">
    <header className="topbar">
      <a className="brand" href="/" aria-label="Support Pilot home"><span className="brand-icon">↗</span> support<span className="brand-light">pilot</span></a>
      <span className="top-label">A little help, right here.</span>
    </header>
    <main className={session?.role === 'support' ? 'staff-main' : undefined}>
      {session?.role !== 'support' && <aside className="intro">
        <span className="eyebrow">CUSTOMER CARE</span>
        <h1>Good things.<br />On their way.</h1>
        <p>Check in on your order with a little help from Support Pilot.</p>
        <div className="promise"><span className="promise-icon">✓</span><div><strong>Your orders, your account.</strong><p>Sign in to view verified order updates.</p></div></div>
        {demo && <div className="demo-note">Local demo · Synthetic orders<br />Demo routing and hash embeddings · Synthetic policies.</div>}
      </aside>}
      <section className="panel" aria-label={session?.role === 'support' ? 'Support dashboard' : session ? 'Order support chat' : 'Sign in'}>
        {initializing ? <p className="loading" role="status">Getting things ready…</p> : !session ? <div className="login-view">
          <span className="eyebrow">WELCOME BACK</span><h2>Let’s find your order.</h2><p className="muted">Sign in with your customer or support account to get started.</p>
          <form onSubmit={login}>
            <label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)} />
            <label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={e => setPassword(e.target.value)} />
            {error && <p role="alert" className="error">{error}</p>}
            <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button>
          </form>
          <p className="privacy">Order details are available only to the account that placed the order.</p>
        </div> : <Chat key={session.email} session={session} sessionError={error} signingOut={busy} onLogout={logout} onExpired={() => { setSession(null); window.history.replaceState(null, '', window.location.pathname); setError('Your session expired. Please sign in again.'); }} />}
      </section>
    </main>
    <footer><span>SUPPORT PILOT</span><span>Thoughtful support. One order at a time.</span></footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
