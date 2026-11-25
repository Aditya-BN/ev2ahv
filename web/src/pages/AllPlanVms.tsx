import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { Kebab, Menu, Pager, Spinner, usePager } from '../components/ui';
import { ACTION_LABEL, ACTION_ORDER, ChecksBadge, PhaseInfo, ProgressBar, useVmActions, VmStatusBadge } from '../components/VmParts';
import { fmtBytes, GROUPS, useLive, vmGroup, vmTone } from '../util';

export default function AllPlanVms() {
  const { status = '' } = useParams();
  const g = GROUPS.find((x) => x.slug === status);
  // /plans/vms?status= only matches exact VM states, so fetch all and group like the dashboard tiles do.
  const { data, loading, reload } = useLive(() => api.planVms(), []);
  const [q, setQ] = useState('');
  const acts = useVmActions(() => { reload(); });
  const vms = useMemo(() => (data?.entities || []).filter((v) => vmGroup(v.status) === status && v.name.toLowerCase().includes(q.toLowerCase())), [data, status, q]);
  const pager = usePager(vms.length, [status, q]);
  const rows = pager.slice(vms);
  return (
    <div className="content">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <h2 className="page-title">{g ? `${g.label} VMs` : 'VMs'}</h2>
      <div className="list-head"><b>Viewing {vms.length} VM{vms.length === 1 ? '' : 's'}</b><div className="spacer" />
        <div className="search"><span>&#128269;</span><input value={q} placeholder="e.g. VM Apache" onChange={(e) => setQ(e.target.value)} /></div>
        <Pager pager={pager} total={vms.length} /></div>
      <div className="table-wrap">
        <table className="grid">
          <thead><tr><th>VM Name</th><th>Migration Plan</th><th>Migration Status</th><th>Seed Progress</th><th>Migrated Data Size</th><th>Checks</th><th>Details</th><th></th></tr></thead>
          <tbody>
            {loading && <tr><td colSpan={8}><Spinner /></td></tr>}
            {rows.map((v) => (
              <tr key={v.plan_id + v.id}>
                <td>{v.name}</td>
                <td><Link className="plan-link" to={`/plan-details/${v.plan_id}`}>{v.plan_name}</Link></td>
                <td><VmStatusBadge s={v.status} /><PhaseInfo vm={v} /></td>
                <td className="prog-cell"><ProgressBar pct={v.seed_percent} tone={vmTone(v.status)} /></td>
                <td>{fmtBytes(v.data_transferred_bytes)} <span className="muted">/ {fmtBytes(v.total_bytes)}</span></td>
                <td><ChecksBadge vm={v} /></td>
                <td>{v.target_vm_url ? <a href={v.target_vm_url} target="_blank" rel="noreferrer">View Target VM</a> : v.error ? <span className="vm-error" title={v.error}>{v.error}</span> : '-'}
                  {' '}<Link className="linkbtn small" to={`/migrationprogress/${v.plan_id}`}>View Details</Link></td>
                <td className="row-actions">
                  <Menu kind="icon" align="right" label={Kebab} disabled={v.actions.length === 0}
                    items={ACTION_ORDER.filter((a) => v.actions.includes(a)).map((a) => ({ label: ACTION_LABEL[a], danger: a === 'cancel' || a === 'rollback', onClick: () => acts.run(v.plan_id, a, [{ id: v.id, name: v.name }]) }))} />
                </td>
              </tr>
            ))}
            {!loading && rows.length === 0 && <tr><td colSpan={8} className="empty">No VMs in this state.</td></tr>}
          </tbody>
        </table>
      </div>
      {acts.dialog}
    </div>
  );
}
