import { FormEvent, useState } from 'react';
import { api } from '../api';
import { PasswordInput } from '../components/ui';

export default function ForcePassword(props: { user: string | null; mode?: string; onDone: () => void; onLogout: () => void }) {
  const [cur, setCur] = useState('');
  const [n1, setN1] = useState('');
  const [n2, setN2] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const short = n1 !== '' && n1.length < 8;
  const same = n1 !== '' && n1 === cur;
  const mismatch = n2 !== '' && n1 !== n2;
  const ok = cur && n1.length >= 8 && n1 === n2 && n1 !== cur;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ok) return;
    setBusy(true); setErr('');
    try { await api.changePassword(cur, n1); props.onDone(); }
    catch (x) { setErr((x as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><span className="mark">ev2ahv</span></div>
        <h3 style={{ margin: '4px 0 6px' }}>Change default password</h3>
        <p className="login-sub">You are signed in as <b>{props.user}</b> with the default password. Set a new password to continue.</p>
        {err && <div className="alert bad" role="alert">{err}</div>}
        <label className="field"><span className="field-label">Current Password</span>
          <PasswordInput value={cur} onChange={setCur} autoComplete="current-password" /></label>
        <label className="field"><span className="field-label">New Password</span>
          <PasswordInput value={n1} onChange={setN1} />
          {short && <span className="field-error">At least 8 characters</span>}
          {same && <span className="field-error">Must differ from the current password</span>}</label>
        <label className="field"><span className="field-label">Confirm New Password</span>
          <PasswordInput value={n2} onChange={setN2} />
          {mismatch && <span className="field-error">Passwords do not match</span>}</label>
        <button className="btn primary block" disabled={!ok || busy}>{busy ? 'Saving...' : 'Change Password'}</button>
        <div style={{ textAlign: 'center', marginTop: 10 }}><button type="button" className="linkbtn small" onClick={props.onLogout}>Log out</button></div>
        {props.mode === 'mock' && <div className="login-mock">MOCK MODE: no real systems are contacted</div>}
      </form>
    </div>
  );
}
