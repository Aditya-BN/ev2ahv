import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Field, Spinner, useDialogs } from '../components/ui';
import { setDisplayTz, timezones, useLive } from '../util';

export const RESYNC = [15, 30, 60, 120, 240, 720, 1440];

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
    </div>
  );
}
