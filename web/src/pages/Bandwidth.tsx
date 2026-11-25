import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, BandwidthPolicy } from '../api';
import { Field, Modal, Spinner, useDialogs } from '../components/ui';
import { useLive } from '../util';

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
  const sources = (envs.data?.entities || []).filter((p) => p.role === 'source');
  const policies = st.data?.bandwidth_policies || [];
  const save = async (list: BandwidthPolicy[]) => {
    try { await api.putSettings({ bandwidth_policies: list }); st.reload(); } catch (e) { showError(e, 'Bandwidth Throttling'); }
  };
  const ok = name.trim() && src && freq && Number(limit) > 0;
  const create = async () => {
    const s = sources.find((x) => x.id === src)!;
    await save([...policies, { id: 'bw-' + Date.now().toString(36), name: name.trim(), source_id: s.id, source_name: s.name, frequency: freq, limit_mbps: Number(limit) }]);
    toast('Policy created'); setDlg(false); setName(''); setSrc(''); setFreq(''); setLimit('');
  };
  const del = async (p: BandwidthPolicy) => {
    if (await confirm({ title: 'Delete Policy', danger: true, ok: 'Delete', body: <p>Delete bandwidth policy <b>{p.name}</b>?</p> })) save(policies.filter((x) => x.id !== p.id));
  };
  return (
    <div className="content narrow">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <div className="title-row"><h2 className="page-title">Bandwidth Throttling</h2><div className="spacer" />
        {policies.length > 0 && <button className="btn primary" onClick={() => setDlg(true)}>Create Policy</button>}</div>
      {st.loading ? <Spinner /> : policies.length === 0 ? (
        <div className="card empty-state">
          <h3>You have no bandwidth throttling policies</h3>
          <p className="muted">Bandwidth throttling policies help you to define the allowed bandwidth cap at the source level.</p>
          <button className="btn primary" id="create-policy" onClick={() => setDlg(true)}>Create Policy</button>
        </div>
      ) : (
        <div className="table-wrap"><table className="grid"><thead><tr><th>Policy Name</th><th>Source</th><th>Frequency</th><th>Bandwidth Limit</th><th></th></tr></thead>
          <tbody>{policies.map((p) => <tr key={p.id}><td>{p.name}</td><td>{p.source_name}</td><td>{p.frequency}</td><td>{p.limit_mbps} Mbps</td>
            <td className="row-actions"><button className="linkbtn danger-text" onClick={() => del(p)}>Delete</button></td></tr>)}</tbody></table></div>
      )}
      {dlg && (
        <Modal title="Create Policy" onClose={() => setDlg(false)} footer={<><button className="btn" onClick={() => setDlg(false)}>Cancel</button>
          <button className="btn primary" disabled={!ok} onClick={create}>Create</button></>}>
          <Field label="Policy Name"><input value={name} placeholder="Enter Policy Name" onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Select Source"><select value={src} onChange={(e) => setSrc(e.target.value)}><option value="">Select Source</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Frequency"><select value={freq} onChange={(e) => setFreq(e.target.value)}><option value="">Select Schedule</option>
            {FREQ.map((f) => <option key={f}>{f}</option>)}</select></Field>
          <Field label="Bandwidth Limit (Mbps)"><input type="number" min={1} value={limit} placeholder="0" onChange={(e) => setLimit(e.target.value)} /></Field>
        </Modal>
      )}
    </div>
  );
}
