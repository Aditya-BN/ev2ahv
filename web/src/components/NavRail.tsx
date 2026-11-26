import { useState } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useSession } from '../App';
import { Modal, PasswordInput, useDialogs, Field } from './ui';
import { Icon } from './Icons';

export function AboutDialog({ onClose }: { onClose: () => void }) {
  const { info } = useSession();
  return (
    <Modal title="About ev2ahv" width={420} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>OK</button>}>
      <div className="about">
        <div className="mark big">ev2ahv</div>
        <p>Version {info.version}{info.build ? ` (build ${info.build})` : ''}</p>
        <p>Mode: <b>{info.mode === 'mock' ? 'MOCK (simulated data, no real systems)' : 'LIVE'}</b></p>
        <p className="muted">Migrates VMs from Stratus everRun to Nutanix AHV. The web console keeps the plan, seed, test and cutover workflow that Nutanix Move users know, in its own Material 3 Expressive design.</p>
      </div>
    </Modal>
  );
}

export function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const { info } = useSession();
  const { showError, toast } = useDialogs();
  const [cur, setCur] = useState('');
  const [n1, setN1] = useState('');
  const [n2, setN2] = useState('');
  const [busy, setBusy] = useState(false);
  const mismatch = n2 !== '' && n1 !== n2;
  const short = n1 !== '' && n1.length < 8;
  const ok = cur && n1.length >= 8 && n1 === n2;
  const submit = async () => {
    setBusy(true);
    try { await api.changePassword(cur, n1); toast('Password changed'); onClose(); }
    catch (e) { showError(e, 'Change Password'); }
    finally { setBusy(false); }
  };
  return (
    <Modal title="Change Password" width={460} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!ok || busy} onClick={submit}>Change Password</button></>}>
      <Field label="User"><input value={info.user || ''} disabled /></Field>
      <Field label="Enter Current Password"><PasswordInput value={cur} onChange={setCur} autoComplete="current-password" /></Field>
      <Field label="Enter new Password" error={short ? 'At least 8 characters' : undefined}><PasswordInput value={n1} onChange={setN1} /></Field>
      <Field label="Re-enter new Password" error={mismatch ? 'Passwords do not match' : undefined}><PasswordInput value={n2} onChange={setN2} /></Field>
    </Modal>
  );
}

export default function NavRail() {
  const { info, logout } = useSession();
  const [dlg, setDlg] = useState<'' | 'about' | 'pw'>('');
  const nav = useNavigate();
  const item = (to: string, icon: Parameters<typeof Icon>[0]['n'], label: string) => (
    <NavLink to={to} className={({ isActive }) => 'rail-item' + (isActive ? ' active' : '')}><Icon n={icon} /><span className="lbl">{label}</span></NavLink>
  );
  return (
    <aside className="rail">
      <Link to="/dashboard" className="brand" aria-label="ev2ahv home"><span className="mark">ev2ahv</span></Link>
{info.role !== 'viewer' && (<button className="fab" id="new-plan" onClick={() => nav('/migrationplan')} title="New migration plan"><Icon n="add" /><span className="lbl">New migration plan</span></button>)}
      <nav className="rail-nav" aria-label="Main">
        {item('/dashboard', 'plans', 'Migration plans')}
        {item('/events', 'events', 'Events')}
        <div className="rail-label">Appliance</div>
        {item('/health', 'health', 'Health')}
        {item('/bandwidth-configuration', 'bandwidth', 'Bandwidth')}
        {item('/realtime-logs', 'logs', 'Realtime logs')}
        {item('/appliance-settings', 'settings', 'Settings')}
        {info.role === 'admin' && item('/users', 'users', 'Users')}
      </nav>
      <div className="rail-foot">
        {info.mode === 'mock' && <span className="mock-badge" title="Simulated data; no real systems are contacted">MOCK MODE</span>}
        {info.role === 'admin' && <a className="rail-item mini" href={api.supportBundleUrl()}><Icon n="bundle" /><span className="lbl">Support bundle</span></a>}
        <a className="rail-item mini" href={api.docsUrl} target="_blank" rel="noreferrer"><Icon n="help" /><span className="lbl">REST API docs</span></a>
        <button className="rail-item mini" onClick={() => setDlg('about')}><Icon n="info" /><span className="lbl">About ev2ahv</span></button>
        <button className="rail-item mini" onClick={() => setDlg('pw')}><Icon n="key" /><span className="lbl">Change password</span></button>
        <div className="rail-user">
          <span className="avatar" aria-hidden="true">{(info.user || '?')[0]}</span>
          <span className="who" id="user-menu">{info.user}{info.role === 'viewer' && <span className="muted small"> (viewer)</span>}</span>
          <button className="icon-btn" aria-label="Log out" title="Log out" onClick={logout}><svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><path d="M17 7l-1.4 1.4 2.6 2.6H9v2h9.2l-2.6 2.6L17 17l5-5-5-5zM4 5h8V3H4a2 2 0 00-2 2v14a2 2 0 002 2h8v-2H4V5z" /></svg></button>
        </div>
      </div>
      {dlg === 'about' && <AboutDialog onClose={() => setDlg('')} />}
      {dlg === 'pw' && <ChangePasswordDialog onClose={() => setDlg('')} />}
    </aside>
  );
}
