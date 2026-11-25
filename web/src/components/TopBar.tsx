import { useState } from 'react';
import { Link, NavLink, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useSession } from '../App';
import { Menu, Modal, PasswordInput, useDialogs, Field } from './ui';

export function AboutDialog({ onClose }: { onClose: () => void }) {
  const { info } = useSession();
  return (
    <Modal title="About ev2ahv" width={420} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>OK</button>}>
      <div className="about">
        <div className="mark big">ev2ahv</div>
        <p>Version {info.version}{info.build ? ` (build ${info.build})` : ''}</p>
        <p>Mode: <b>{info.mode === 'mock' ? 'MOCK (simulated data, no real systems)' : 'LIVE'}</b></p>
        <p className="muted">Migrates VMs from Stratus everRun to Nutanix AHV. The web console follows the layout and flows of Nutanix Move 6.2.</p>
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

export default function TopBar() {
  const { info, logout } = useSession();
  const [dlg, setDlg] = useState<'' | 'about' | 'pw'>('');
  const nav = useNavigate();
  return (
    <header className="topbar">
      <Link to="/dashboard" className="brand"><span className="mark">ev2ahv</span></Link>
      <nav>
        <NavLink to="/dashboard" className={({ isActive }) => (isActive ? 'active' : '')}>Migration Plans</NavLink>
        <NavLink to="/events" className={({ isActive }) => (isActive ? 'active' : '')}>Events</NavLink>
      </nav>
      <div className="spacer" />
      {info.mode === 'mock' && <span className="mock-badge" title="Simulated data; no real systems are contacted">MOCK MODE</span>}
      <a className="top-link" href={api.docsUrl} target="_blank" rel="noreferrer" title="REST API docs">?</a>
      <Menu kind="icon" align="right" label={<span className="gear" aria-label="Settings">&#9881;</span>} items={[
        { label: 'Appliance Settings', onClick: () => nav('/appliance-settings') },
        { label: 'Appliance Health', onClick: () => nav('/health') },
        { label: 'Bandwidth Throttling', onClick: () => nav('/bandwidth-configuration') },
        { label: 'Download Support Bundle', href: api.supportBundleUrl() },
        { label: 'Realtime Logs', onClick: () => nav('/realtime-logs') },
        { label: 'About ev2ahv', onClick: () => setDlg('about') },
      ]} />
      <Menu kind="link" align="right" id="user-menu" label={<span className="user">{info.user}</span>} items={[
        { label: 'Events', onClick: () => nav('/events') },
        { label: 'Change Password', onClick: () => setDlg('pw') },
        { label: 'REST API Docs', href: api.docsUrl },
        { label: 'About ev2ahv', onClick: () => setDlg('about') },
        { label: 'Logout', onClick: logout },
      ]} />
      {dlg === 'about' && <AboutDialog onClose={() => setDlg('')} />}
      {dlg === 'pw' && <ChangePasswordDialog onClose={() => setDlg('')} />}
    </header>
  );
}
