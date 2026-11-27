import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, UpgradeState } from '../api';
import { useSession } from '../App';
import { Modal, Spinner, useDialogs } from '../components/ui';
import { fmtDateSec, tzLabel, useLive } from '../util';

const stepTime = (t: number | string) => (typeof t === 'number' ? fmtDateSec(t) : t);

// One line to paste on an everRun node: append the key to root's authorized_keys, limited to this appliance's address.
export const authLine = (key: string, from = window.location.hostname) =>
  `install -d -m 700 /root/.ssh && echo 'from="${from}",no-agent-forwarding,no-port-forwarding,no-X11-forwarding ${key}' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys`;

// D7: upload an upgrade bundle; root (ev2ahv-upgrade.path) verifies and applies it, then the app restarts.
function UpgradeCard() {
  const { showError, toast } = useDialogs();
  const [up, setUp] = useState<UpgradeState | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = () => api.upgradeStatus().then(setUp).catch(() => { /* app restarting */ });
  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 4000);
    return () => window.clearInterval(t);
  }, []);
  const s = up?.status || {};
  const active = !!up?.pending || s.state === 'running';
  const send = async () => {
    if (!file) return;
    setBusy(true);
    try { setUp(await api.uploadUpgrade(file)); toast('Bundle uploaded; the appliance verifies and applies it'); setFile(null); }
    catch (e) { showError(e, 'Upgrade'); }
    finally { setBusy(false); }
  };
  return (
    <div className="card">
      <h4 className="section">Upgrade</h4>
      {up && !up.enabled ? <p className="muted">This appliance cannot upgrade from the UI yet; use <code>sudo ev2ahv-upgrade apply</code> once.</p> : (<>
        <p className="muted">Upload an <code>ev2ahv-upgrade-&lt;version&gt;.tar.gz</code> bundle. The appliance checks every file (and the signature, if signing keys are installed), backs up the app and its data, installs, and restores the old version by itself if the new one does not start. The page reloads when the app is back.</p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input type="file" accept=".tar.gz,.tgz,application/gzip" disabled={active || busy} onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <button className="btn primary" disabled={!file || active || busy} onClick={send}>{busy ? 'Uploading…' : 'Upload and apply'}</button>
        </div>
        {(active || s.state) && (
          <p className={s.state === 'failed' ? 'vm-error' : 'muted'} style={{ marginTop: 8 }}>
            {up?.pending ? 'Waiting for the appliance to start the upgrade…' : s.state === 'running' ? `Upgrading from ${s.from_version || '?'}… the app restarts` : s.state === 'done' ? `Done: now ${s.version}.` : `Failed: ${s.message}`}
            {s.time ? ` (${fmtDateSec(s.time)})` : ''}
          </p>)}
      </>)}
    </div>
  );
}

export default function Health() {
  const { info } = useSession();
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
          <p className="muted">ev2ahv logs in as root with this key to every everRun node (read-only <code>virsh -r</code>, reading guest disk files, and avcli to stop and start the source) and to Linux guests for automatic prep.</p>
          {data.ssh_public_key && (<>
            <p className="muted" style={{ marginBottom: 4 }}>On each everRun node (and each Linux guest that uses automatic prep), as root:</p>
            <div style={{ ...mono, background: '#f4f6f9', border: '1px solid var(--line)', borderRadius: 3, padding: 10, wordBreak: 'break-all' }}>{authLine(data.ssh_public_key)}</div>
            <p className="muted">The <code>from=</code> option limits the key to this appliance's address ({window.location.hostname}); replace it with the appliance IP if you opened this page through a DNS name or a proxy. Then add or refresh the everRun environment: it fails if any node refuses the key.</p>
          </>)}
        </div>

        {info.role === 'admin' && <UpgradeCard />}
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
