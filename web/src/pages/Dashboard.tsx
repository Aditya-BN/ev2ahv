import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, Plan, Provider } from '../api';
import { Kebab, Menu, Modal, Pager, Spinner, useDialogs, usePager } from '../components/ui';
import { EnvDialog, EnvView, typeLabel } from '../components/EnvDialogs';
import { fmtBytes, fmtDate, fmtElapsed, GROUPS, useLive } from '../util';

const FILTERS = ['All Plans', 'Cancelled', 'Completed', 'Failed', 'In Progress', 'Not Started', 'Paused', 'Scheduled'];
const TILE_ICON: Record<string, string> = { 'in-progress': '○', 'ready-to-cutover': '⇅', failed: '▲', paused: '❚❚', completed: '✓' };

export function StatusText({ s }: { s: string }) {
  const icon = s === 'Completed' ? '✓' : s === 'Failed' ? '▲' : s === 'Not Started' ? '⏻' : s === 'Paused' ? '❚❚' : s === 'Scheduled' ? '⏰' : s === 'Cancelled' ? '✕' : '○';
  const tone = s === 'Completed' ? 'good' : s === 'Failed' ? 'bad' : s === 'Paused' || s === 'Scheduled' ? 'warn' : s === 'In Progress' ? 'info' : '';
  return <span className={'status-text ' + tone}><span className="si">{icon}</span> {s}</span>;
}

export default function Dashboard() {
  const nav = useNavigate();
  const { showError, confirm, toast } = useDialogs();
  const envs = useLive(() => api.providers(), []);
  const plans = useLive(() => api.plans(), []);
  const [envDlg, setEnvDlg] = useState<null | { existing?: Provider }>(null);
  const [envView, setEnvView] = useState<Provider | null>(null);
  const [popup, setPopup] = useState<null | { kind: 'src' | 'tgt' | 'data'; plan: Plan }>(null);
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('All Plans');
  const [sel, setSel] = useState<Set<string>>(new Set());

  const all = plans.data?.entities || [];
  const list = useMemo(() => all.filter((p) => (filter === 'All Plans' || p.status === filter) && p.name.toLowerCase().includes(q.toLowerCase())), [all, filter, q]);
  const pager = usePager(list.length, [filter, q]);
  const rows = pager.slice(list);
  const tiles = GROUPS.map((g) => ({
    ...g,
    n: all.reduce((a, p) => a + ({ 'in-progress': p.counts.in_progress, 'ready-to-cutover': p.counts.ready_to_cutover, failed: p.counts.failed, paused: p.counts.paused, completed: p.counts.completed })[g.slug], 0),
  }));
  const provs = envs.data?.entities || [];
  const byId = (id?: string | null) => provs.find((p) => p.id === id);

  const doAction = async (p: Plan, a: string) => {
    try {
      if (a === 'Start') { await api.startPlan(p.id); toast(`Plan ${p.name} started`); nav(`/migrationprogress/${p.id}`); }
      else if (a === 'Edit') nav(`/migrationplan/${p.id}`);
      else if (a === 'Delete') {
        if (await confirm({ title: 'Delete Migration Plan', danger: true, ok: 'Delete', body: <p>Delete plan <b>{p.name}</b>? Target VMs that were already created on AHV are kept. This cannot be undone.</p> })) {
          await api.deletePlan(p.id); toast(`Plan ${p.name} deleted`); plans.reload();
        }
      }
    } catch (e) { showError(e, `${a} plan`); }
  };
  const removeEnv = async (e: Provider) => {
    if (!(await confirm({ title: 'Remove Environment', danger: true, ok: 'Remove', body: <p>Remove environment <b>{e.name}</b>? Plans that use it block removal.</p> }))) return;
    try { await api.deleteProvider(e.id); envs.reload(); toast('Environment removed'); } catch (x) { showError(x, 'Remove Environment'); }
  };
  const refreshEnv = async (e: Provider) => {
    try { await api.refreshProvider(e.id); envs.reload(); toast(`${e.name} refreshed`); } catch (x) { showError(x, 'Refresh Environment'); }
  };
  const selPlans = all.filter((p) => sel.has(p.id));
  const bulk = (a: string) => selPlans.length > 0 && selPlans.every((p) => p.actions.includes(a));
  const bulkRun = async (a: string) => { for (const p of selPlans) await doAction(p, a); setSel(new Set()); };
  const allSel = rows.length > 0 && rows.every((p) => sel.has(p.id));

  return (
    <div className="dash">
      <aside className="env-panel">
        <div className="env-head"><b>Environments</b>
          <button className="linkbtn" id="add-env" onClick={() => setEnvDlg({})}>+ Add Environment</button></div>
        {envs.loading && <Spinner />}
        {provs.map((e) => (
          <div className="env-card" key={e.id} data-testid="env-card">
            <div className="env-tag">{e.type === 'everrun' ? 'everRun' : e.type === 'plans' ? 'Files' : 'AOS'}</div>
            <div className="env-body">
              <div className="env-top"><span className="env-name" title={e.name}>{e.name}</span>
                <Menu kind="icon" align="right" label={Kebab} items={[
                  { label: 'View Details', onClick: () => setEnvView(e) },
                  { label: 'Edit', onClick: () => setEnvDlg({ existing: e }) },
                  { label: 'Refresh', onClick: () => refreshEnv(e) },
                  { label: 'Remove', danger: true, onClick: () => removeEnv(e) },
                ]} /></div>
              <div className="env-type">{typeLabel(e.type)} <span className={'dot ' + (e.status === 'Connected' ? 'ok' : 'err')} title={e.status} /></div>
              <div className="env-foot"><span>{e.address}</span><span className="pill">{e.vm_count} VMs</span></div>
            </div>
          </div>
        ))}
        {!envs.loading && provs.length === 0 && <p className="muted">No environments yet. Add a Stratus everRun source and a Nutanix AOS target.</p>}
      </aside>

      <section className="dash-main">
        <div className="tiles">
          {tiles.map((t) => (
            <div className={'tile ' + t.slug} key={t.slug}>
              <div><div className="tile-label">{t.label}</div>
                <Link className="tile-count" to={`/all-plan-vms/${t.slug}`}>{t.n} VMs</Link></div>
              <span className="tile-icon">{TILE_ICON[t.slug]}</span>
            </div>
          ))}
        </div>
        <div className="action-row">
          <button className="btn primary" id="new-plan" onClick={() => nav('/migrationplan')}>+ New Migration Plan</button>
          <Menu kind="btn" label="Actions" disabled={selPlans.length === 0} items={[
            { label: 'Start', disabled: !bulk('Start'), onClick: () => bulkRun('Start') },
            { label: 'Delete', danger: true, disabled: !bulk('Delete'), onClick: () => bulkRun('Delete') },
          ]} />
        </div>
        <div className="list-head">
          <b>Viewing {list.length} plan{list.length === 1 ? '' : 's'}</b>
          <div className="spacer" />
          <div className="search"><span>&#128269;</span><input value={q} placeholder="e.g. My Migration Plan" onChange={(e) => setQ(e.target.value)} /></div>
          <select className="filter-select" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Plan status filter">
            {FILTERS.map((f) => <option key={f}>{f}</option>)}
          </select>
          <Pager pager={pager} total={list.length} showSize={false} />
        </div>
        <div className="table-wrap">
          <table className="grid plans">
            <thead><tr>
              <th className="chk"><input type="checkbox" checked={allSel} onChange={(e) => setSel(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())} aria-label="Select all" /></th>
              <th>Plan name</th><th>Created By</th><th>Source</th><th>Target</th><th>Data Migrated</th><th>Elapsed Time</th>
              <th>Plan Status</th><th>VMs Status</th><th>Total VMs</th><th></th></tr></thead>
            <tbody>
              {plans.loading && <tr><td colSpan={11}><Spinner /></td></tr>}
              {rows.map((p) => {
                const started = !!p.started_at;
                return (
                  <tr key={p.id} data-testid="plan-row">
                    <td className="chk"><input type="checkbox" checked={sel.has(p.id)} aria-label={`Select ${p.name}`}
                      onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(p.id) : n.delete(p.id); setSel(n); }} /></td>
                    <td><Link to={`/plan-details/${p.id}`} className="plan-link">{p.name}</Link><div className="sub">{fmtDate(p.created_at)}</div></td>
                    <td>{p.created_by}</td>
                    <td><button className="linkbtn underline" onClick={() => setPopup({ kind: 'src', plan: p })}>everRun</button></td>
                    <td><button className="linkbtn underline" onClick={() => setPopup({ kind: 'tgt', plan: p })}>AOS</button></td>
                    <td>{started && p.data_migrated_bytes > 0
                      ? <button className="linkbtn underline" onClick={() => setPopup({ kind: 'data', plan: p })}>{fmtBytes(p.data_migrated_bytes)}</button>
                      : <span className="muted">Not Available</span>}</td>
                    <td>{fmtElapsed(p)}</td>
                    <td><StatusText s={p.status} /></td>
                    <td className="counters">
                      <span className="c ready" title="Ready to Cutover">&#8645; {p.counts.ready_to_cutover}</span>
                      <span className="c bad" title="Failed">&#9650; {p.counts.failed}</span>
                      <span className="c warn" title="Paused">&#9670; {p.counts.paused}</span>
                    </td>
                    <td>{p.counts.total}</td>
                    <td className="row-actions">
                      {started && <Link className="linkbtn" to={`/migrationprogress/${p.id}`}>View Details</Link>}
                      <Menu kind="link" label="Actions" align="right" disabled={p.actions.length === 0}
                        items={p.actions.map((a) => ({ label: a, danger: a === 'Delete', onClick: () => doAction(p, a) }))} />
                    </td>
                  </tr>
                );
              })}
              {!plans.loading && rows.length === 0 && <tr><td colSpan={11} className="empty">No migration plans{filter !== 'All Plans' || q ? ' match the filter' : ' yet. Click New Migration Plan to create one'}.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      {envDlg && <EnvDialog existing={envDlg.existing} onClose={() => setEnvDlg(null)} onDone={() => envs.reload()} />}
      {envView && <EnvView env={envView} onClose={() => setEnvView(null)} />}
      {popup?.kind === 'src' && (() => { const e = byId(popup.plan.source?.id); return (
        <Modal title="Source Environment Details" onClose={() => setPopup(null)} footer={<button className="btn primary" onClick={() => setPopup(null)}>OK</button>}>
          <dl className="kv"><dt>Environment Type</dt><dd>{e ? typeLabel(e.type) : 'Stratus everRun'}</dd>
            <dt>Source Name</dt><dd>{popup.plan.source?.name || '-'}</dd><dt>IP/FQDN</dt><dd>{e?.address || '-'}</dd></dl></Modal>); })()}
      {popup?.kind === 'tgt' && (() => { const e = byId(popup.plan.target?.id); return (
        <Modal title="Target Environment Details" onClose={() => setPopup(null)} footer={<button className="btn primary" onClick={() => setPopup(null)}>OK</button>}>
          <dl className="kv"><dt>Environment Type</dt><dd>{e ? typeLabel(e.type) : 'Nutanix AOS'}</dd>
            <dt>Target Name</dt><dd>{popup.plan.target?.name || '-'}</dd><dt>IP/FQDN</dt><dd>{e?.address || '-'}</dd>
            <dt>Cluster</dt><dd>{popup.plan.cluster || '-'}</dd><dt>Container</dt><dd>{popup.plan.container || '-'}</dd></dl></Modal>); })()}
      {popup?.kind === 'data' && (
        <Modal title="Data Migrated" width={560} onClose={() => setPopup(null)} footer={<button className="btn primary" onClick={() => setPopup(null)}>OK</button>}>
          <table className="grid"><thead><tr><th>VM</th><th>Data Migrated</th><th>Total Data Size</th></tr></thead>
            <tbody>{popup.plan.vms.map((v) => <tr key={v.id}><td>{v.name}</td><td>{fmtBytes(v.data_transferred_bytes)}</td><td>{fmtBytes(v.total_bytes)}</td></tr>)}
              <tr className="total"><td>Total</td><td>{fmtBytes(popup.plan.data_migrated_bytes)}</td><td>{fmtBytes(popup.plan.vms.reduce((a, v) => a + v.total_bytes, 0))}</td></tr></tbody></table>
        </Modal>
      )}
    </div>
  );
}
