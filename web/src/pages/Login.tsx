import { FormEvent, useState } from 'react';
import { api } from '../api';

export default function Login(props: { onDone: () => void; mode?: string }) {
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr('');
    try { await api.login(u, p); props.onDone(); }
    catch (x) { setErr((x as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><span className="mark">ev2ahv</span></div>
        <p className="login-sub">Stratus everRun to Nutanix AHV migration</p>
        {err && <div className="alert bad" role="alert">{err}</div>}
        <label className="field"><span className="field-label">User Name</span>
          <input autoFocus value={u} placeholder="Enter user name" onChange={(e) => setU(e.target.value)} autoComplete="username" /></label>
        <label className="field"><span className="field-label">Password</span>
          <input type="password" value={p} placeholder="Enter password" onChange={(e) => setP(e.target.value)} autoComplete="current-password" /></label>
        <button className="btn primary block" disabled={busy || !u || !p}>{busy ? 'Signing in...' : 'Log In'}</button>
        {props.mode === 'mock' && <div className="login-mock">MOCK MODE: no real systems are contacted</div>}
      </form>
    </div>
  );
}
