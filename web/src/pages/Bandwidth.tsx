import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, BandwidthPolicy, EffectiveBandwidth } from '../api';
import { Field, Modal, Spinner, useDialogs } from '../components/ui';
import { useLive } from '../util';

const DEFAULT_MBPS = 100;

// Same four schedules the server understands (server/ev2ahv_server/bandwidth.py). Times are in the appliance timezone.
const FREQ = ['Always', 'Weekdays 08:00-18:00', 'Nights 18:00-08:00', 'Weekends'];

export default function Bandwidth() {
  const { showError, confirm, toast } = useDialogs();
  const st = useLive(() => api.settings(), []);
  const envs = useLive(() => api.providers(), []);
  const [dlg, setDlg] = useState(false);
  const [name, setName] = useState('');
  const [src, setSrc] = useState('');
  const [freq, setFreq] = useState('');
  const [limit, setLimit] = useState('');
  const [unlimited, setUnlimited] = useState(false);
  const sources = (envs.data?.entities || []).filter((p) => p.role === 'source');
  const policies = st.data?.bandwidth_policies || [];
  const save = async (list: BandwidthPolicy[]) => {
    try { await api.putSettings({ bandwidth_policies: list }); st.reload(); } catch (e) { showError(e, 'Bandwidth Throttling'); }
  };
  const tz = st.data?.timezone || 'Asia/Jakarta';
  const ok = name.trim() && freq && (unlimited || Number(limit) > 0);
  const create = async () => {
    const s = sources.find((x) => x.id === src);   // empty = every environment
    await save([...policies, { id: 'bw-' + Date.now().toString(36), name: name.trim(), source_id: s?.id || '', source_name: s?.name || 'All environments', frequency: freq, limit_mbps: unlimited ? 0 : Number(limit) }]);
    toast('Policy created'); setDlg(false); setName(''); setSrc(''); setFreq(''); setLimit(''); setUnlimited(false);
  };
  const del = async (p: BandwidthPolicy) => {
    if (await confirm({ title: 'Delete Policy', danger: true, ok: 'Delete', body: <p>Delete bandwidth policy <b>{p.name}</b>?</p> })) save(policies.filter((x) => x.id !== p.id));
  };
  return (
    <div className="content narrow">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <div className="title-row"><h2 className="page-title">Bandwidth Throttling</h2><div className="spacer" />
        {policies.length > 0 && <button className="btn primary" onClick={() => setDlg(true)}>Create Policy</button>}</div>
      <p className="muted" id="bw-explain">Limits cap how fast data is read from the source during seeds and resyncs, in MiB/s. Schedules follow the appliance timezone ({tz}).
        A change takes effect within a few seconds, also while a seed or resync is running. With no matching policy the limit is {DEFAULT_MBPS} MiB/s. A plan can override the policies.</p>
      {st.loading ? <Spinner /> : policies.length === 0 ? (
        <div className="card empty-state">
          <h3>You have no bandwidth throttling policies</h3>
          <p className="muted">Bandwidth throttling policies help you to define the allowed bandwidth cap at the source level.</p>
          <button className="btn primary" id="create-policy" onClick={() => setDlg(true)}>Create Policy</button>
        </div>
      ) : (
        <div className="table-wrap"><table className="grid"><thead><tr><th>Policy Name</th><th>Source</th><th>Frequency</th><th>Bandwidth Limit (MiB/s)</th><th></th></tr></thead>
          <tbody>{policies.map((p) => <tr key={p.id}><td>{p.name}</td><td>{p.source_id ? p.source_name : 'All environments'}</td><td>{p.frequency}</td><td>{p.limit_mbps ? `${p.limit_mbps} MiB/s` : 'Unlimited'}</td>
            <td className="row-actions"><button className="linkbtn danger-text" onClick={() => del(p)}>Delete</button></td></tr>)}</tbody></table></div>
      )}
      {dlg && (
        <Modal title="Create Policy" onClose={() => setDlg(false)} footer={<><button className="btn" onClick={() => setDlg(false)}>Cancel</button>
          <button className="btn primary" disabled={!ok} onClick={create}>Create</button></>}>
          <Field label="Policy Name"><input value={name} placeholder="Enter Policy Name" onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Environment" hint="A policy for one environment beats a policy for all environments."><select value={src} onChange={(e) => setSrc(e.target.value)}><option value="">All environments</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Frequency"><select value={freq} onChange={(e) => setFreq(e.target.value)}><option value="">Select Schedule</option>
            {FREQ.map((f) => <option key={f}>{f}</option>)}</select></Field>
          <Field label="Bandwidth Limit (MiB/s)" hint="Where policies overlap, the lowest limit wins.">
            <input type="number" min={1} value={unlimited ? '' : limit} disabled={unlimited} placeholder={unlimited ? 'Unlimited' : `e.g. ${DEFAULT_MBPS}`} onChange={(e) => setLimit(e.target.value)} /></Field>
          <label className="cb"><input type="checkbox" id="bw-unlimited" checked={unlimited} onChange={(e) => setUnlimited(e.target.checked)} /> Unlimited (no cap while this policy is active)</label>
        </Modal>
      )}
    </div>
  );
}

/** "100 MiB/s", "Unlimited", with where it comes from. */
export function fmtLimit(b: EffectiveBandwidth): string {
  const v = b.unlimited ? 'Unlimited' : `${b.limit_mbps} MiB/s`;
  const why = b.origin === 'plan' ? 'plan override' : b.origin === 'policy' ? `policy "${b.policy}"` : 'default';
  return `${v} (${why})`;
}
