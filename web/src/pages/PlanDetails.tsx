import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, ApiError } from '../api';
import { Spinner, useDialogs } from '../components/ui';
import { fmtBytes, fmtDate, fmtDateSec, fmtMem, tzLabel, useLive } from '../util';
import { PrepScripts } from '../components/VmParts';
import { StatusText } from './Dashboard';
import { typeLabel } from '../components/EnvDialogs';

export default function PlanDetails() {
  const { id = '' } = useParams();
  const nav = useNavigate();
  const { showError, confirm, toast } = useDialogs();
  const plan = useLive(() => api.plan(id), [id]);
  const envs = useLive(() => api.providers(), []);
  const [tab, setTab] = useState<'summary' | 'events'>('summary');
  const ev = useLive(() => api.planEvents(id), [id]);
  const p = plan.data;
  if (plan.error && !p) return <div className="content"><div className="alert bad">{plan.error.message}</div><Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link></div>;
  if (!p) return <div className="content"><Spinner /></div>;
  const src = envs.data?.entities.find((e) => e.id === p.source?.id);
  const tgt = envs.data?.entities.find((e) => e.id === p.target?.id);
  const can = (a: string) => p.actions.includes(a);
  const started = !!p.started_at;
  const start = async () => { try { await api.startPlan(id); toast('Plan started'); nav(`/migrationprogress/${id}`); } catch (e) { showError(e, 'Start plan'); } };
  const del = async () => {
    if (!(await confirm({ title: 'Delete Migration Plan', danger: true, ok: 'Delete', body: <p>Delete plan <b>{p.name}</b>? Target VMs that were already created are kept.</p> }))) return;
    try { await api.deletePlan(id); nav('/dashboard'); }
    catch (e) {
      if (!(e instanceof ApiError && e.status === 409 && /staging disks/.test(e.message))) { showError(e, 'Delete plan'); return; }
      if (!(await confirm({ title: 'Remove staging disks?', danger: true, ok: 'Remove and delete', body: <p>{e.message}</p> }))) return;
      try { await api.deletePlan(id, true); toast('Removing staging disks; the plan disappears when that is done'); nav('/dashboard'); }
      catch (e2) { showError(e2, 'Delete plan'); }
    }
  };
  return (
    <div className="content narrow">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <div className="title-row">
        <h2 className="page-title">{p.name}</h2><StatusText s={p.status} /><div className="spacer" />
        {started && <button className="btn" onClick={() => nav(`/migrationprogress/${id}`)}>View Details</button>}
        {can('Start') && <button className="btn primary" onClick={start}>Start</button>}
      </div>
      <p className="muted">Summary of migration plan. Click on the edit below to make a change.</p>
      <div className="tabs"><button className={tab === 'summary' ? 'active' : ''} onClick={() => setTab('summary')}>Summary</button>
        <button className={tab === 'events' ? 'active' : ''} onClick={() => setTab('events')}>Events</button></div>
      {tab === 'summary' ? (
        <>
          <div className="card"><h4 className="section">Source Environment Details</h4>
            <dl className="kv"><dt>Environment Type</dt><dd>{src ? typeLabel(src.type) : 'Stratus everRun'}</dd><dt>Name</dt><dd>{p.source?.name || '-'}</dd>
              <dt>Source IP</dt><dd>{src?.address || '-'}</dd><dt>No. of VMs to Migrate</dt><dd>{p.vms.length}</dd></dl></div>
          <div className="card"><h4 className="section">Target Environment Details</h4>
            <dl className="kv"><dt>Environment Type</dt><dd>{tgt ? typeLabel(tgt.type) : 'Nutanix AOS'}</dd><dt>Name</dt><dd>{p.target?.name || '-'}</dd>
              <dt>Cluster</dt><dd>{p.cluster || '-'}</dd><dt>Container</dt><dd>{p.container || '-'}</dd><dt>Project</dt><dd>{p.project || 'None'}</dd></dl></div>
          <div className="card"><h4 className="section">Network Mapping</h4>
            <table className="grid"><thead><tr><th>Source Network</th><th>Target Network</th>{p.test_network && <th>Test Network</th>}</tr></thead>
              <tbody>{p.network_map.map((n) => <tr key={n.source_network}><td>{n.source_network}</td><td>{n.target_subnet}</td>{p.test_network && <td>{n.test_subnet || '-'}</td>}</tr>)}
                {p.network_map.length === 0 && <tr><td colSpan={2} className="muted">No mapping.</td></tr>}</tbody></table></div>
          <div className="card"><h4 className="section">VMs</h4>
            <table className="grid"><thead><tr><th>Source VM</th><th>Target VM name</th><th>vCPU(s)</th><th>Memory</th><th>Disk bus</th><th>Size</th></tr></thead>
              <tbody>{p.vms.map((v) => { const s = p.vm_settings[v.id]; return (
                <tr key={v.id}><td>{v.name}</td><td>{s?.target_name || v.name}</td><td>{s?.vcpus ?? '-'}</td><td>{s?.memory_mib ? fmtMem(s.memory_mib) : '-'}</td>
                  <td>{(s?.disk_bus || 'scsi').toUpperCase()}{s?.disk_bus === 'pci' && s.convert_to_scsi ? ' → SCSI' : ''}</td><td>{fmtBytes(v.total_bytes)}</td></tr>); })}</tbody></table></div>
          <div className="card"><h4 className="section">Preparation and Schedule</h4>
            <dl className="kv"><dt>Preparation</dt><dd>{p.prep.mode === 'manual' ? 'Manual' : 'Automatic'}{p.prep.bypass_guest_ops ? ' (guest operations bypassed)' : ''}</dd>
              <dt>Retain IP / MAC</dt><dd>{p.prep.retain_ip ? 'IP' : 'no IP'}, {p.prep.retain_mac ? 'MAC' : 'no MAC'}</dd>
              <dt>Uninstall Stratus guest tools</dt><dd>{p.prep.uninstall_guest_tools ? 'Yes' : 'No'}</dd><dt>Install NGT</dt><dd>{p.prep.install_ngt ? 'Yes' : 'No'}</dd>
              <dt>Start</dt><dd>{p.schedule.seed_at ? `${fmtDate(p.schedule.seed_at)} (${tzLabel()})` : started ? fmtDate(p.started_at) : 'On Start'}</dd>
              <dt>Resync interval</dt><dd>{p.schedule.resync_minutes} minutes</dd><dt>Created</dt><dd>{fmtDate(p.created_at)} by {p.created_by}</dd></dl>
            <PrepScripts planId={id} os={p.vms.map((v) => v.os_type)} mode={p.prep.mode === 'manual' ? 'manual' : 'automatic'} />
          </div>
          <div className="form-actions left">
            {can('Edit') && <button className="btn primary" id="edit-plan" onClick={() => nav(`/migrationplan/${id}`)}>Edit</button>}
            {can('Delete') && <button className="btn danger-outline" onClick={del}>Delete</button>}
          </div>
        </>
      ) : (
        <div className="table-wrap"><table className="grid"><thead><tr><th>Severity</th><th>Time ({tzLabel()})</th><th>VM</th><th>Message</th></tr></thead>
          <tbody>{(ev.data?.entities || []).map((e, i) => <tr key={i}><td><span className={'sev ' + e.severity}>{e.severity}</span></td><td className="nowrap">{fmtDateSec(e.time)}</td><td>{e.vm || '-'}</td><td>{e.message}</td></tr>)}
            {ev.data && ev.data.entities.length === 0 && <tr><td colSpan={4} className="empty">No events for this plan yet.</td></tr>}</tbody></table></div>
      )}
    </div>
  );
}
