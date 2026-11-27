import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, LdapSettings } from '../api';
import { Field, Spinner, useDialogs } from '../components/ui';
import { setDisplayTz, timezones, useLive } from '../util';

export const RESYNC = [5, 15, 30, 60, 120, 240, 720, 1440];

export default function ApplianceSettings() {
  const { showError, toast } = useDialogs();
  const { data, loading, reload } = useLive(() => api.settings(), []);
  const [resync, setResync] = useState(60);
  const [tz, setTz] = useState('Asia/Jakarta');
  const [wvm, setWvm] = useState('');
  const [whost, setWhost] = useState('');
  const [wuser, setWuser] = useState('');
  const [prefix, setPrefix] = useState('ev2ahv-');
  const [init, setInit] = useState(false);
  useEffect(() => {
    if (data && !init) {
      setResync(data.resync_minutes); setTz(data.timezone);
      setWvm(data.writer?.vm || ''); setWhost(data.writer?.host || ''); setWuser(data.writer?.user || '');
      setPrefix(data.source_name_prefix ?? 'ev2ahv-');
      setInit(true);
    }
  }, [data, init]);
  const save = async () => {
    if (prefix.trim() === '' && (data?.source_name_prefix ?? 'ev2ahv-') !== ''
        && !window.confirm('An empty prefix lets this appliance migrate any VM it can see. Continue?')) return;
    try {
      await api.putSettings({ resync_minutes: resync, timezone: tz, source_name_prefix: prefix.trim(), writer: { vm: wvm || null, host: whost || null, user: wuser || null } });
      setDisplayTz(tz); toast('Settings saved'); reload();
    } catch (e) { showError(e, 'Settings'); }
  };
  return (
    <div className="content narrow">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <h2 className="page-title">Appliance Settings</h2>
      {loading ? <Spinner /> : (
        <div className="card form-card">
          <h4 className="section">General</h4>
          <Field label="Default resync interval" hint="How often a seeded VM is resynced while it waits for cutover. A plan can override this in its schedule.">
            <select value={resync} onChange={(e) => setResync(Number(e.target.value))}>
              {(RESYNC.includes(resync) ? RESYNC : [...RESYNC, resync].sort((a, b) => a - b)).map((m) => <option key={m} value={m}>{m >= 60 ? `${m / 60} hour${m === 60 ? '' : 's'}` : `${m} minutes`}</option>)}
            </select></Field>
          <Field label="Timezone" hint="Used to show dates in the console and as the default for schedules. Default Asia/Jakarta (WIB).">
            <select value={tz} onChange={(e) => setTz(e.target.value)}>{timezones().map((z) => <option key={z}>{z}</option>)}</select></Field>
          <h4 className="section">Migration scope</h4>
          <Field label="Source VM name prefix" hint="Only source VMs (and plan-file targets) whose name starts with this can be migrated. Leave empty to allow any VM. Changes are logged in Events.">
            <input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="empty = any VM" /></Field>
          <h4 className="section">Writer VM</h4>
          <p className="muted">The AHV helper VM that receives disk data during seeding.</p>
          <Field label="Writer VM name"><input value={wvm} onChange={(e) => setWvm(e.target.value)} placeholder="e.g. ev2ahv-writer" /></Field>
          <Field label="Writer host (address)"><input value={whost} onChange={(e) => setWhost(e.target.value)} placeholder="e.g. 10.0.0.25" /></Field>
          <Field label="Writer user"><input value={wuser} onChange={(e) => setWuser(e.target.value)} placeholder="e.g. root" /></Field>
          <div className="form-actions"><button className="btn primary" onClick={save}>Save</button></div>
        </div>
      )}
      {data && <LdapCard initial={data.ldap} reload={reload} />}
    </div>
  );
}

// D13: directory sign-in. Local accounts are tried first; directory users get admin or viewer from group membership
// at every login. The bind password is write-only (stored encrypted, never shown again).
function LdapCard({ initial, reload }: { initial?: LdapSettings; reload: () => void }) {
  const { showError, toast } = useDialogs();
  const [c, setC] = useState<LdapSettings>({ enabled: false, verify_tls: true, user_filter: '(sAMAccountName={username})', ...(initial || {}) });
  const [pw, setPw] = useState('');
  const set = (p: Partial<LdapSettings>) => setC({ ...c, ...p });
  const text = (k: keyof LdapSettings, label: string, ph: string, hint?: string) => (
    <Field label={label} hint={hint}><input value={(c[k] as string) || ''} placeholder={ph} onChange={(e) => set({ [k]: e.target.value } as Partial<LdapSettings>)} /></Field>);
  const save = async () => {
    try { await api.putSettings({ ldap: { ...c, ...(pw ? { bind_password: pw } : {}) } }); setPw(''); toast('Directory sign-in saved'); reload(); }
    catch (e) { showError(e, 'Directory sign-in'); }
  };
  return (
    <div className="card form-card">
      <h4 className="section">Directory sign-in (LDAP / Active Directory)</h4>
      <label className="cb"><input type="checkbox" checked={!!c.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Allow directory users to sign in</label>
      {c.enabled && (<>
        {text('url', 'Server URL', 'ldaps://dc1.example.com', 'Use ldaps://, or ldap:// with StartTLS. Clear-text ldap:// is refused.')}
        <label className="cb"><input type="checkbox" checked={!!c.start_tls} onChange={(e) => set({ start_tls: e.target.checked })} /> StartTLS (for ldap://)</label>
        <label className="cb"><input type="checkbox" checked={c.verify_tls !== false} onChange={(e) => set({ verify_tls: e.target.checked })} /> Verify the server certificate</label>
        {text('bind_dn', 'Service account (bind DN)', 'CN=svc-ev2ahv,OU=Service,DC=example,DC=com')}
        <Field label="Service account password" hint="Write-only. Leave empty to keep the stored one."><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" /></Field>
        {text('user_base', 'User search base', 'OU=Users,DC=example,DC=com')}
        {text('user_filter', 'User filter', '(sAMAccountName={username})', 'Must contain {username}. OpenLDAP: (uid={username}).')}
        {text('admin_group', 'Admin group (DN)', 'CN=ev2ahv-admins,OU=Groups,DC=example,DC=com', 'Members get the admin role.')}
        {text('viewer_group', 'Viewer group (DN)', 'CN=ev2ahv-viewers,OU=Groups,DC=example,DC=com', 'Members get the read-only viewer role. Users in neither group cannot sign in.')}
      </>)}
      <div className="form-actions"><button className="btn primary" onClick={save}>Save</button></div>
    </div>
  );
}
