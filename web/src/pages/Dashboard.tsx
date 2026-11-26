import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, Plan, Provider } from '../api';
import { Kebab, Menu, Modal, Pager, Spinner, SplitButton, useDialogs, usePager } from '../components/ui';
import { EnvDialog, EnvView, typeLabel } from '../components/EnvDialogs';
import { useSession } from '../App';
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
  const { info } = useSession();
  const hl = useLive(() => (info.appliance ? api.health() : Promise.resolve(null)), [info.appliance]);
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

      <section className="dash-main">
        {info.appliance && hl.data && hl.data.selftest.status !== 'PASS' && (
          <div className="alert warn" role="status">
            The disk hot-add self-test has not passed yet ({hl.data.selftest.status}). <Link to="/health">Open Appliance Health</Link>
          </div>
        )}
        <div className="dash-head">
          <div><h2 className="page-title">Migration plans</h2>
            <p className="muted">Seed while the VM runs, test, then cut over. Rollback is powering the source back on.</p></div>
        </div>
        <div className="tiles">
          {tiles.map((t) => (
            <div className={'tile ' + t.slug} key={t.slug}>
              <div className="tile-label">{t.label}</div>
              <div className="tile-num">{t.n}</div>
              <Link className="tile-count" to={`/all-plan-vms/${t.slug}`}>VMs, view all</Link>
              <span className="tile-icon">{TILE_ICON[t.slug]}</span>
            </div>
          ))}
        </div>
      <section className="env-strip" aria-label="Environments">
        <div className="env-head"><b>Environments</b><div className="spacer" />
          <button className="btn" id="add-env" onClick={() => setEnvDlg({})}>+ Add environment</button></div>
        {envs.loading && <Spinner />}
        <div className="env-row">
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
        </div>
        {!envs.loading && provs.length === 0 && <p className="muted">No environments yet. Add a Stratus everRun source and a Nutanix AOS target.</p>}
      </section>
        <div className="list-head">
          <b>Viewing {list.length} plan{list.length === 1 ? '' : 's'}</b>
          <div className="spacer" />
          <Menu kind="btn" label="Bulk actions" disabled={selPlans.length === 0} items={[
            { label: 'Start', disabled: !bulk('Start'), onClick: () => bulkRun('Start') },
            { label: 'Delete', danger: true, disabled: !bulk('Delete'), onClick: () => bulkRun('Delete') },
          ]} />
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
              <th>Plan name</th><th>Source / Target</th><th>Data Migrated</th><th>Elapsed Time</th>
              <th>Status</th><th>VMs</th><th></th></tr></thead>
            <tbody>
              {plans.loading && <tr><td colSpan={9}><Spinner /></td></tr>}
              {rows.map((p) => {
                const started = !!p.started_at;
                return (
                  <tr key={p.id} data-testid="plan-row">
                    <td className="chk"><input type="checkbox" checked={sel.has(p.id)} aria-label={`Select ${p.name}`}
                      onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(p.id) : n.delete(p.id); setSel(n); }} /></td>
                    <td><Link to={`/plan-details/${p.id}`} className="plan-link">{p.name}</Link><div className="sub">{fmtDate(p.created_at)} by {p.created_by}</div></td>
                    <td className="nowrap"><button className="linkbtn underline" onClick={() => setPopup({ kind: 'src', plan: p })}>everRun</button>
                      <span className="muted" aria-hidden="true">&rarr;</span><button className="linkbtn underline" onClick={() => setPopup({ kind: 'tgt', plan: p })}>AOS</button></td>
                    <td>{started && p.data_migrated_bytes > 0
                      ? <button className="linkbtn underline" onClick={() => setPopup({ kind: 'data', plan: p })}>{fmtBytes(p.data_migrated_bytes)}</button>
                      : <span className="muted">Not Available</span>}</td>
                    <td>{fmtElapsed(p)}</td>
                    <td><StatusText s={p.status} /></td>
                    <td className="counters">
                      <span className="c" title="Total VMs">{p.counts.total} total</span>
                      <span className="c ready" title="Ready to Cutover">&#8645; {p.counts.ready_to_cutover}</span>
                      <span className="c bad" title="Failed">&#9650; {p.counts.failed}</span>
                      <span className="c warn" title="Paused">&#9670; {p.counts.paused}</span>
                    </td>
                    <td className="row-actions">
                      {started
                        ? <SplitButton label="View details" onClick={() => nav(`/migrationprogress/${p.id}`)} moreLabel={`More actions for ${p.name}`}
                            items={p.actions.map((a) => ({ label: a, danger: a === 'Delete', onClick: () => doAction(p, a) }))} />
                        : p.actions.length > 0
                          ? <SplitButton label={p.actions[0]} onClick={() => doAction(p, p.actions[0])} moreLabel={`More actions for ${p.name}`}
                              items={p.actions.slice(1).map((a) => ({ label: a, danger: a === 'Delete', onClick: () => doAction(p, a) }))} />
                          : null}
                    </td>
                  </tr>
                );
              })}
              {!plans.loading && rows.length === 0 && <tr><td colSpan={9} className="empty">No migration plans{filter !== 'All Plans' || q ? ' match the filter' : ' yet. Click New Migration Plan to create one'}.</td></tr>}
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
