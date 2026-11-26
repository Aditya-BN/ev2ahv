import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, Plan, PlanVm } from '../api';
import { Kebab, Menu, Modal, Pager, Spinner, usePager } from '../components/ui';
import { ACTION_LABEL, ACTION_ORDER, ChecksBadge, PhaseInfo, ProgressBar, useVmActions, VmLogDialog, VmStatusBadge } from '../components/VmParts';
import { fmtBytes, fmtDate, fmtDuration, fmtMem, useLive, vmTone } from '../util';
import { StatusText } from './Dashboard';

const FILTERS: { key: string; label: string; match: (v: PlanVm) => boolean }[] = [
  { key: 'in-progress', label: 'In Progress', match: (v) => ['Queued', 'Preparing', 'Seeding', 'Cutover in Progress', 'Testing', 'Rolling Back'].includes(v.status) },
  { key: 'ready', label: 'Ready to Cutover', match: (v) => v.status === 'Ready to Cutover' },
  { key: 'scheduled', label: 'Scheduled for Cutover', match: (v) => v.status === 'Scheduled for Cutover' },
  { key: 'tested', label: 'Tested VMs', match: (v) => v.status === 'Tested' },
  { key: 'failed', label: 'Failed', match: (v) => v.status === 'Failed' },
  { key: 'paused', label: 'Paused', match: (v) => v.status === 'Paused' },
  { key: 'completed', label: 'Completed', match: (v) => v.status === 'Completed' },
  { key: 'cancelled', label: 'Cancelled', match: (v) => v.status === 'Cancelled' },
];

export function VmConfigDialog({ plan, vm, onClose }: { plan: Plan; vm: PlanVm; onClose: () => void }) {
  const s = plan.vm_settings[vm.id];
  return (
    <Modal title={`VM Configurations: ${vm.name}`} width={560} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>OK</button>}>
      <dl className="kv">
        <dt>Source VM</dt><dd>{vm.name} ({vm.os_type})</dd>
        <dt>Target VM name</dt><dd>{s?.target_name || vm.name}</dd>
        <dt>vCPU(s)</dt><dd>{s?.vcpus ?? 'as source'}</dd>
        <dt>Memory</dt><dd>{s?.memory_mib ? fmtMem(s.memory_mib) : 'as source'}</dd>
        <dt>Disk bus</dt><dd>{(s?.disk_bus || 'scsi').toUpperCase()}{s?.disk_bus === 'pci' ? (s.convert_to_scsi ? ', converted to SCSI after cutover' : ', stays PCI') : ''}</dd>
        <dt>Guest IP (automatic prep)</dt><dd>{s?.guest_address || '-'}</dd>
        <dt>Firmware</dt><dd>{s?.firmware ? s.firmware.toUpperCase() : 'as source'}</dd>
        <dt>Timezone</dt><dd>{s?.timezone || 'as source'}</dd>
        <dt>Preparation</dt><dd>{plan.prep.mode === 'manual' ? 'Manual' : 'Automatic'}{plan.prep.bypass_guest_ops ? ', guest operations bypassed' : ''}</dd>
        <dt>Network</dt><dd>{plan.network_map.map((n) => `${n.source_network} → ${n.target_subnet}`).join(', ') || '-'}</dd>
        <dt>Total size</dt><dd>{fmtBytes(vm.total_bytes)}</dd>
      </dl>
    </Modal>
  );
}

export default function Progress() {
  const { id = '' } = useParams();
  const nav = useNavigate();
  const { data: plan, error, loading, reload } = useLive(() => api.plan(id), [id]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');
  const [fil, setFil] = useState<Set<string>>(new Set());
  const [cfg, setCfg] = useState<PlanVm | null>(null);
  const [logVm, setLogVm] = useState<PlanVm | null>(null);
  const acts = useVmActions(() => { reload(); });

  const vms = useMemo(() => (plan?.vms || []).filter((v) => v.name.toLowerCase().includes(q.toLowerCase()) &&
    (fil.size === 0 || FILTERS.some((f) => fil.has(f.key) && f.match(v)))), [plan, q, fil]);
  const pager = usePager(vms.length, [q, fil]);
  const rows = pager.slice(vms);
  const selVms = (plan?.vms || []).filter((v) => sel.has(v.id));
  const testSubnet = plan?.network_map.find((n) => n.test_subnet)?.test_subnet;
  const runFor = (action: PlanVm['actions'][number], from: PlanVm[]) => {
    const t = from.filter((v) => v.actions.includes(action));
    if (t.length) acts.run(id, action, t.map((v) => ({ id: v.id, name: v.name })), testSubnet);
  };
  const allSel = rows.length > 0 && rows.every((v) => sel.has(v.id));
  const toggleFilter = (k: string) => { const n = new Set(fil); n.has(k) ? n.delete(k) : n.add(k); setFil(n); };

  return (
    <div className="overlay">
      <header className="overlay-head">
        <h2>{plan?.name || 'Migration progress'}</h2>
        {plan && <StatusText s={plan.status} />}
        <div className="spacer" />
        {plan && <Link className="linkbtn" to={`/plan-details/${id}`}>Plan Summary</Link>}
        <a className="icon-btn" href={api.docsUrl} target="_blank" rel="noreferrer" title="REST API docs">?</a>
        <button className="icon-btn" aria-label="Close" onClick={() => nav('/dashboard')}>&#x2715;</button>
      </header>
      {loading && <div className="overlay-body"><Spinner text="Loading..." /></div>}
      {error && !plan && <div className="overlay-body"><div className="alert bad">{error.message}</div></div>}
      {plan && (
        <div className="overlay-cols">
          <div className="overlay-main">
            <div className="toolbar">
              {ACTION_ORDER.filter((a) => a !== 'complete' || plan.vms.some((v) => v.actions.includes(a))).map((a) => (
                <button key={a} className={'btn' + (a === 'cutover' ? ' primary-outline' : '')} id={`act-${a}`}
                  disabled={!selVms.some((v) => v.actions.includes(a))} onClick={() => runFor(a, selVms)}>{ACTION_LABEL[a]}</button>
              ))}
            </div>
            <div className="list-head"><b>Viewing {vms.length} VM{vms.length === 1 ? '' : 's'}</b><div className="spacer" /><Pager pager={pager} total={vms.length} /></div>
            <div className="table-wrap">
              <table className="grid vmtable">
                <thead><tr>
                  <th className="chk"><input type="checkbox" checked={allSel} aria-label="Select all" onChange={(e) => setSel(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())} /></th>
                  <th>VM Name</th><th>Migration Status</th><th>Seed Progress</th><th>Migrated Data Size</th><th>ETA</th><th>Last Sync</th><th>Checks</th><th>Details</th><th></th></tr></thead>
                <tbody>
                  {rows.map((v) => (
                    <tr key={v.id} data-testid="vm-row">
                      <td className="chk"><input type="checkbox" checked={sel.has(v.id)} aria-label={`Select ${v.name}`}
                        onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(v.id) : n.delete(v.id); setSel(n); }} /></td>
                      <td><div>{v.name}</div><button className="linkbtn small" onClick={() => setCfg(v)}>VM Configurations</button>{' '}<button className="linkbtn small" onClick={() => setLogVm(v)}>View Log</button></td>
                      <td><VmStatusBadge s={v.status} /><PhaseInfo vm={v} /></td>
                      <td className="prog-cell"><ProgressBar pct={v.seed_percent} tone={vmTone(v.status)} /></td>
                      <td>{fmtBytes(v.data_transferred_bytes)} <span className="muted">/ {fmtBytes(v.total_bytes)}</span></td>
                      <td>{v.status === 'Seeding' ? fmtDuration(v.eta_seconds) : '-'}</td>
                      <td>{fmtDate(v.last_sync_at)}</td>
                      <td><ChecksBadge vm={v} /></td>
                      <td>
                        {v.target_vm_url && <a href={v.target_vm_url} target="_blank" rel="noreferrer">View Target VM</a>}
                        {v.status === 'Completed' && v.cutover_result?.downtime_seconds != null && (
                          <div className="muted">Downtime {fmtDuration(v.cutover_result.downtime_seconds)}, final sync {fmtDuration(v.cutover_result.final_sync_seconds)}</div>
                        )}
                        {v.test_vm_id && v.status === 'Tested' && <div className="muted">Test VM: {v.test_vm_id}</div>}
                        {v.error && <div className="vm-error" title={v.error}>{v.error}</div>}
                        {!v.target_vm_url && !v.error && !(v.test_vm_id && v.status === 'Tested') && '-'}
                      </td>
                      <td className="row-actions">
                        <Menu kind="icon" align="right" label={Kebab} disabled={v.actions.length === 0}
                          items={ACTION_ORDER.filter((a) => v.actions.includes(a)).map((a) => ({ label: ACTION_LABEL[a], danger: a === 'cancel' || a === 'rollback', onClick: () => runFor(a, [v]) }))} />
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && <tr><td colSpan={10} className="empty">No VMs match.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
          <aside className="filter-panel">
            <div className="fp-head"><b>Filters</b><button className="linkbtn" onClick={() => { setFil(new Set()); setQ(''); }}>Clear</button></div>
            <input value={q} placeholder="e.g. VM Apache" onChange={(e) => setQ(e.target.value)} />
            <div className="fp-title">Migration Status</div>
            <label className="cb"><input type="checkbox" checked={fil.size === 0} onChange={() => setFil(new Set())} /> All VMs</label>
            {FILTERS.map((f) => <label className="cb" key={f.key}><input type="checkbox" checked={fil.has(f.key)} onChange={() => toggleFilter(f.key)} /> {f.label}</label>)}
          </aside>
        </div>
      )}
      {cfg && plan && <VmConfigDialog plan={plan} vm={cfg} onClose={() => setCfg(null)} />}
      {logVm && <VmLogDialog planId={id} vm={logVm} onClose={() => setLogVm(null)} />}
      {acts.dialog}
    </div>
  );
}
