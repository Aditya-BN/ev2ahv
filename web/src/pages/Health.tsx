import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Modal, Spinner, useDialogs } from '../components/ui';
import { fmtDateSec, tzLabel, useLive } from '../util';

const stepTime = (t: number | string) => (typeof t === 'number' ? fmtDateSec(t) : t);

export default function Health() {
  const { showError, toast } = useDialogs();
  const { data, loading, reload } = useLive(() => api.health(), []);
  const [certDlg, setCertDlg] = useState(false);
  const [cert, setCert] = useState('');
  const [key, setKey] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const st = data?.selftest;
  const running = st?.status === 'running';

  const run = async () => {
    try { await api.runSelftest(); toast('Self-test started'); reload(); } catch (e) { showError(e, 'Self-test'); }
  };
  const replace = async () => {
    setBusy(true); setErr('');
    try { await api.putTls(cert, key); toast('Certificate replaced'); setCertDlg(false); setCert(''); setKey(''); reload(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(data?.ssh_public_key || ''); toast('Public key copied'); }
    catch { toast('Copy failed: select the key and copy it manually'); }
  };
  const row = (k: string, v: React.ReactNode) => <div className="kv"><span className="muted" style={{ display: 'inline-block', width: 150 }}>{k}</span>{v}</div>;
  const mono = { fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 12.5 } as const;

  return (
    <div className="content narrow">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <h2 className="page-title">Appliance Health</h2>
      {loading && !data ? <Spinner /> : data && st && (<>
        <div className="card">
          <h4 className="section">This appliance</h4>
          {row('VM UUID', <span style={mono}>{data.vm_uuid || '-'}</span>)}
          {row('AHV VM', data.ahv_vm ? `${data.ahv_vm.name} (${data.ahv_vm.power}, ${data.ahv_vm.disks} disks)` : '-')}
          {row('Prism', <span><span className={'dot ' + (data.prism && /^ok|connected|reachable/i.test(data.prism) ? 'ok' : 'err')} style={{ marginLeft: 0, marginRight: 6 }} />{data.prism || 'Not configured'}</span>)}
        </div>

        <div className="card">
          <h4 className="section">Disk hot-add self-test</h4>
          <p className="muted">Hot-adds a disk to this appliance and removes it again to prove the Prism and AHV path works.</p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
            <button className="btn primary" disabled={running} onClick={run}>{running ? 'Running...' : 'Run self-test'}</button>
            <span className={'chk-badge ' + (st.status === 'PASS' ? 'pass' : st.status === 'FAIL' ? 'fail' : 'warn')}>{st.status.toUpperCase()}</span>
            {st.ended ? <span className="muted">Finished {fmtDateSec(st.ended)} {tzLabel()}</span> : null}
          </div>
          {st.error && <div className="alert bad" role="alert">{st.error}</div>}
          {(st.steps?.length ?? 0) > 0 && (
            <div className="logview" style={{ height: 'auto', maxHeight: 260 }}>
              {(st.steps || []).map((s, i) => <div key={i}>{stepTime(s.time)} {tzLabel()}  {s.message}</div>)}
            </div>
          )}
        </div>

        <div className="card">
          <h4 className="section">TLS certificate</h4>
          {data.tls ? (<>
            {row('Subject', data.tls.subject)}
            {row('Issuer', data.tls.issuer)}
            {row('Expires', typeof data.tls.not_after === 'number' ? fmtDateSec(data.tls.not_after) : data.tls.not_after)}
            {row('Self-signed', data.tls.self_signed ? 'Yes' : 'No')}
            {row('SHA-256', <span style={{ ...mono, wordBreak: 'break-all' }}>{data.tls.sha256}</span>)}
          </>) : <p className="muted">No certificate information available.</p>}
          <div style={{ marginTop: 10 }}><button className="btn" onClick={() => { setErr(''); setCertDlg(true); }}>Replace certificate</button></div>
        </div>

        <div className="card">
          <h4 className="section">Appliance SSH key</h4>
          {data.ssh_public_key ? (<>
            <div style={{ ...mono, background: '#f4f6f9', border: '1px solid var(--line)', borderRadius: 3, padding: 10, wordBreak: 'break-all' }}>{data.ssh_public_key}</div>
            <div style={{ marginTop: 8 }}><button className="btn" onClick={copy}>Copy</button></div>
          </>) : <p className="muted">No SSH key generated yet.</p>}
          <p className="muted">Add this key to root's authorized_keys on each everRun node so ev2ahv can read guest disks.</p>
        </div>
      </>)}
      {certDlg && (
        <Modal title="Replace certificate" width={640} onClose={() => setCertDlg(false)}
          footer={<><button className="btn" onClick={() => setCertDlg(false)}>Cancel</button>
            <button className="btn primary" disabled={!cert.trim() || !key.trim() || busy} onClick={replace}>Replace</button></>}>
          {err && <div className="alert bad" role="alert">{err}</div>}
          <label className="field"><span className="field-label">Certificate (PEM)</span>
            <textarea rows={7} style={mono} value={cert} onChange={(e) => setCert(e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" /></label>
          <label className="field"><span className="field-label">Private key (PEM)</span>
            <textarea rows={7} style={mono} value={key} onChange={(e) => setKey(e.target.value)} placeholder="-----BEGIN PRIVATE KEY-----" /></label>
        </Modal>
      )}
    </div>
  );
}
