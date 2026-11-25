import { ReactNode, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, DEFAULT_PREP, Plan, PlanIn, Prep, Provider, VmSettings, Workload } from '../api';
import { Field, Modal, PasswordInput, Pager, Spinner, Tip, useDialogs, usePager } from '../components/ui';
import { epochToZoned, fmtBytes, fmtMem, getDisplayTz, timezones, tzLabel, useLive, zonedToEpoch } from '../util';
import { typeLabel } from '../components/EnvDialogs';

const STEPS = ['Source & Target', "Select VM's", 'Network and Policy', 'VM Preparation', 'VM Settings', 'Summary'];
const RESYNC = [15, 30, 60, 120, 240, 720, 1440];
const PROJECTS = ['None', '_internal', 'default'];

interface WS {
  name: string; sourceId: string; targetId: string; project: string; cluster: string; container: string;
  vmIds: string[]; netMap: Record<string, string>; testNet: boolean; testMap: Record<string, string>;
  prep: Prep; vmSet: Record<string, VmSettings>; schedMode: 'now' | 'later'; schedAt: string; schedTz: string; resync: number;
}
const EMPTY: WS = {
  name: '', sourceId: '', targetId: '', project: 'None', cluster: '', container: '', vmIds: [], netMap: {}, testNet: false, testMap: {},
  prep: { ...DEFAULT_PREP }, vmSet: {}, schedMode: 'now', schedAt: '', schedTz: 'Asia/Jakarta', resync: 60,
};

export const vmWarnings = (w: Workload): string[] => {
  const l = [...w.warnings];
  if (w.power_state === 'Off') l.push('This VM is powered off. Automatic guest preparation needs a running guest; use manual preparation or bypass guest operations.');
  return l;
};
const defaultSettings = (w: Workload): VmSettings => ({
  target_name: w.name, vcpus: w.vcpus, memory_mib: w.memory_mib,
  disk_bus: w.os_type === 'windows' && w.warnings.some((x) => x.includes('virtio-blk only')) ? 'pci' : 'scsi',
  convert_to_scsi: true, firmware: w.firmware, timezone: null, guest_address: w.nics[0]?.ip || null,
});

export default function Wizard() {
  const { id } = useParams();
  const editing = !!id;
  const nav = useNavigate();
  const { showError, confirm } = useDialogs();
  const [ws, setWs] = useState<WS>({ ...EMPTY, schedTz: getDisplayTz() });
  const [step, setStep] = useState(0);
  const [done, setDone] = useState(0);
  const [named, setNamed] = useState(editing);
  const [renaming, setRenaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(!editing);
  const [editPlan, setEditPlan] = useState<Plan | null>(null);
  const envs = useLive(() => api.providers(), []);
  const plans = useLive(() => api.plans(), []);
  const [wl, setWl] = useState<Workload[]>([]);
  const [wlLoading, setWlLoading] = useState(false);
  const set = (p: Partial<WS>) => setWs((s) => ({ ...s, ...p }));

  const provs = envs.data?.entities || [];
  const sources = provs.filter((p) => p.role === 'source');
  const targets = provs.filter((p) => p.role === 'target');
  const target = targets.find((t) => t.id === ws.targetId);
  const source = sources.find((s) => s.id === ws.sourceId);

  // edit mode: load the plan into the wizard state once
  useEffect(() => {
    if (!id) return;
    api.plan(id).then((p) => {
      setEditPlan(p);
      setWs({
        name: p.name, sourceId: p.source?.id || '', targetId: p.target?.id || '', project: p.project || 'None', cluster: p.cluster || '', container: p.container || '',
        vmIds: p.vms.map((v) => v.id), netMap: Object.fromEntries(p.network_map.map((n) => [n.source_network, n.target_subnet])),
        testNet: p.test_network, testMap: Object.fromEntries(p.network_map.filter((n) => n.test_subnet).map((n) => [n.source_network, n.test_subnet as string])),
        prep: { ...DEFAULT_PREP, ...p.prep, windows_password: '', linux_password: '' }, vmSet: p.vm_settings || {},
        schedMode: p.schedule.seed_at ? 'later' : 'now', schedAt: p.schedule.seed_at ? epochToZoned(p.schedule.seed_at, p.schedule.timezone) : '',
        schedTz: p.schedule.timezone, resync: p.schedule.resync_minutes,
      });
      setNamed(true); setLoaded(true);
    }).catch((e) => { showError(e, 'Edit Migration Plan'); nav('/dashboard'); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    if (!ws.sourceId) { setWl([]); return; }
    setWlLoading(true);
    api.workloads(ws.sourceId).then((r) => setWl(r.entities)).catch((e) => showError(e, 'Source inventory')).finally(() => setWlLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws.sourceId]);

  const selected = useMemo(() => ws.vmIds.map((i) => wl.find((w) => w.id === i)).filter(Boolean) as Workload[], [ws.vmIds, wl]);
  const networks = useMemo(() => Array.from(new Set(selected.flatMap((w) => w.nics.map((n) => n.network)))), [selected]);
  const hasWin = selected.some((w) => w.os_type === 'windows');
  const hasLin = selected.some((w) => w.os_type === 'linux');
  const setting = (w: Workload): VmSettings => ws.vmSet[w.id] || defaultSettings(w);

  const canNext = (): boolean => {
    switch (step) {
      case 0: return !!(ws.sourceId && ws.targetId && ws.cluster && ws.container);
      case 1: return ws.vmIds.length > 0;
      case 2: return networks.every((n) => ws.netMap[n]) && (!ws.testNet || networks.every((n) => ws.testMap[n]));
      case 3: {
        const p = ws.prep;
        if (p.mode === 'manual' || p.bypass_guest_ops) return true;
        if (hasWin && (!p.windows_user || (!editing && !p.windows_password))) return false;
        if (hasLin && !p.linux_user) return false;
        return true;
      }
      case 4: return selected.every((w) => { const s = setting(w); return !!s.target_name?.trim() && (s.vcpus || 0) >= 1 && (s.memory_mib || 0) >= 256; });
      case 5: return ws.schedMode === 'now' || (!!ws.schedAt && zonedToEpoch(ws.schedAt, ws.schedTz) > Date.now() / 1000);
    }
    return false;
  };

  const leave = async () => {
    const ok = await confirm({ title: 'Are you sure you want to leave this page?', cancel: 'Discard', ok: 'Continue Editing', dismiss: true,
      body: <p>The migration plan is not saved. If you leave this page, all of your changes will be discarded.</p> });
    // Move puts "Discard" on the left and "Continue Editing" (primary) on the right
    if (!ok) nav(editing ? `/plan-details/${id}` : '/dashboard');
  };

  const build = (start: boolean): PlanIn => {
    const vm_settings: Record<string, VmSettings> = {};
    selected.forEach((w) => { vm_settings[w.id] = { ...setting(w), convert_to_scsi: setting(w).disk_bus === 'pci' ? setting(w).convert_to_scsi : false }; });
    const p = ws.prep;
    return {
      name: ws.name, source_id: ws.sourceId, target_id: ws.targetId, cluster: ws.cluster, container: ws.container,
      project: ws.project === 'None' ? null : ws.project, vm_ids: ws.vmIds,
      network_map: networks.map((n) => ({ source_network: n, target_subnet: ws.netMap[n], test_subnet: ws.testNet ? ws.testMap[n] : null })),
      test_network: ws.testNet,
      prep: { ...p, windows_password: p.windows_password || null, linux_password: p.linux_password || null, virtio_source: p.virtio_source || null },
      vm_settings,
      schedule: { seed_at: ws.schedMode === 'later' ? zonedToEpoch(ws.schedAt, ws.schedTz) : null, timezone: ws.schedTz, resync_minutes: ws.resync },
      start,
    };
  };
  const save = async (start: boolean) => {
    setBusy(true);
    try {
      const body = build(start);
      const plan = editing ? await api.editPlan(id!, body) : await api.createPlan(body);
      nav(start ? `/migrationprogress/${plan.id}` : `/plan-details/${plan.id}`);
    } catch (e) { showError(e, editing ? 'Edit Migration Plan' : 'Create Migration Plan'); }
    finally { setBusy(false); }
  };

  const nameTaken = (n: string) => !editing && (plans.data?.entities || []).some((p) => p.name === n.trim());

  if (!loaded || (!envs.data && envs.loading)) return <div className="overlay"><div className="overlay-body"><Spinner text="Loading..." /></div></div>;

  return (
    <div className="overlay wizard">
      <header className="overlay-head">
        <h2>{ws.name || 'New Migration Plan'}</h2>
        {named && <button className="icon-btn" aria-label="rename migration plan" onClick={() => setRenaming(true)}>&#9998;</button>}
        <div className="spacer" />
        <a className="icon-btn" href={api.docsUrl} target="_blank" rel="noreferrer" title="REST API docs">?</a>
        <button className="icon-btn" aria-label="Close" onClick={leave}>&#x2715;</button>
      </header>
      <ol className="stepper">
        {STEPS.map((s, i) => (
          <li key={s} className={(i === step ? 'cur ' : '') + (i < done || i < step ? 'done' : '')}>
            <button disabled={i > Math.max(done, step)} onClick={() => setStep(i)}>
              <span className="num">{i < step || i < done && i !== step ? '✓' : i + 1}</span> {s}</button>
          </li>
        ))}
      </ol>
      <div className={'wiz-body' + (step === 1 ? ' two-col' : '')}>
        {step === 0 && <Step1 {...{ ws, set, sources, targets, target, source, envsReload: envs.reload }} />}
        {step === 1 && <Step2 {...{ ws, set, wl, wlLoading, source }} />}
        {step === 2 && <Step3 {...{ ws, set, networks, target }} />}
        {step === 3 && <Step4 {...{ ws, set, hasWin, hasLin, editing, planId: id, selected }} />}
        {step === 4 && <Step5 {...{ ws, set, selected, setting }} />}
        {step === 5 && <Step6 {...{ ws, set, selected, source, target, networks, setting, goto: setStep }} />}
      </div>
      <footer className="wiz-foot">
        <button className="btn" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
        <div className="spacer" />
        <button className="btn" onClick={leave}>Cancel</button>
        {step < 5 ? (
          <button className="btn primary" id="wiz-next" disabled={!canNext()} onClick={() => { setDone(Math.max(done, step + 1)); setStep(step + 1); }}>Next</button>
        ) : (
          <>
            <button className="btn" id="wiz-save" disabled={!canNext() || busy} onClick={() => save(false)}>Save</button>
            <button className="btn primary" id="wiz-save-start" disabled={!canNext() || busy} onClick={() => save(true)}>Save and Start</button>
          </>
        )}
      </footer>

      {!named && <NameGate taken={nameTaken} onCancel={() => nav('/dashboard')} onProceed={(n) => { set({ name: n.trim() }); setNamed(true); }} />}
      {renaming && <NameGate initial={ws.name} rename taken={(n) => n.trim() !== ws.name && nameTaken(n)} onCancel={() => setRenaming(false)}
        onProceed={(n) => { set({ name: n.trim() }); setRenaming(false); }} />}
      {editing && editPlan?.started_at && step === 0 && null}
    </div>
  );
}

function NameGate(props: { initial?: string; rename?: boolean; taken: (n: string) => boolean; onCancel: () => void; onProceed: (n: string) => void }) {
  const [n, setN] = useState(props.initial || '');
  const taken = n.trim() !== '' && props.taken(n);
  const ok = n.trim() !== '' && !taken;
  return (
    <Modal title={props.rename ? 'Rename Migration Plan' : 'New Migration Plan'} width={480} onClose={props.onCancel}
      footer={<><button className="btn" onClick={props.onCancel}>Cancel</button><button className="btn primary" id="gate-proceed" disabled={!ok} onClick={() => props.onProceed(n)}>{props.rename ? 'OK' : 'Proceed'}</button></>}>
      <Field label="Plan Name" hint="Provide a unique name for your plan" error={taken ? 'A plan with this name already exists' : undefined}>
        <input autoFocus id="plan-name" value={n} placeholder="Enter Migration Plan Name" onChange={(e) => setN(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && ok) props.onProceed(n); }} /></Field>
    </Modal>
  );
}

type SP = { ws: WS; set: (p: Partial<WS>) => void };

function Step1({ ws, set, sources, targets, target, source, envsReload }: SP & { sources: Provider[]; targets: Provider[]; target?: Provider; source?: Provider; envsReload: () => void }) {
  const { showError, toast } = useDialogs();
  const refresh = async () => { try { await api.refreshProvider(ws.targetId); envsReload(); toast('Target refreshed'); } catch (e) { showError(e, 'Refresh'); } };
  return (
    <div className="wiz-col">
      <Field label="Select a Source">
        <select id="sel-source" value={ws.sourceId} onChange={(e) => set({ sourceId: e.target.value, targetId: '', cluster: '', container: '', vmIds: [], netMap: {}, testMap: {}, vmSet: {} })}>
          <option value="">Select a Source...</option>{sources.map((s) => <option key={s.id} value={s.id}>{s.name} ({typeLabel(s.type)})</option>)}</select></Field>
      <Field label="Select a Target">
        <select id="sel-target" value={ws.targetId} disabled={!ws.sourceId} onChange={(e) => set({ targetId: e.target.value, cluster: '', container: '', netMap: {}, testMap: {} })}>
          <option value="">Select a Target...</option>{targets.map((s) => <option key={s.id} value={s.id}>{s.name} ({typeLabel(s.type)})</option>)}</select></Field>
      <div className="refresh-row">
        <button className="linkbtn" aria-label="refresh target list" disabled={!ws.targetId} onClick={refresh}
          title={ws.targetId ? '' : 'Refresh is disabled until you select a Target Environment'}>Refresh</button>
        {!ws.targetId && <span className="muted small">Refresh is disabled until you select a Target Environment</span>}
      </div>
      {source && !targets.length && <div className="alert warn">No Nutanix AOS target environment is registered. Add one on the dashboard first.</div>}
      {target && (
        <>
          <Field label="Target Project"><select value={ws.project} onChange={(e) => set({ project: e.target.value })}>{PROJECTS.map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Target Cluster"><select id="sel-cluster" value={ws.cluster} onChange={(e) => set({ cluster: e.target.value, container: '' })}>
            <option value="">Select a Cluster...</option>{target.clusters.map((c) => <option key={c.id}>{c.name}</option>)}</select></Field>
          {ws.cluster && <Field label="Target Container"><select id="sel-container" value={ws.container} onChange={(e) => set({ container: e.target.value })}>
            <option value="">Select a Container...</option>{target.containers.map((c) => <option key={c.id}>{c.name}</option>)}</select></Field>}
        </>
      )}
    </div>
  );
}

function Step2({ ws, set, wl, wlLoading, source }: SP & { wl: Workload[]; wlLoading: boolean; source?: Provider }) {
  const [q, setQ] = useState('');
  const [showF, setShowF] = useState(false);
  const [fos, setFos] = useState<Set<string>>(new Set());
  const [fpw, setFpw] = useState<Set<string>>(new Set());
  const [fav, setFav] = useState<Set<string>>(new Set());
  const [asc, setAsc] = useState(true);
  const [tab, setTab] = useState<'all' | 'warn'>('all');
  const tg = (s: Set<string>, f: (x: Set<string>) => void, k: string) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); f(n); };
  const rows = useMemo(() => wl.filter((w) => w.name.toLowerCase().includes(q.toLowerCase()) &&
    (!fos.size || fos.has(w.os_type)) && (!fpw.size || fpw.has(w.power_state)) && (!fav.size || fav.has(w.availability || 'none')))
    .sort((a, b) => (asc ? 1 : -1) * a.name.localeCompare(b.name)), [wl, q, fos, fpw, fav, asc]);
  const pager = usePager(rows.length, [q, fos, fpw, fav]);
  const added = new Set(ws.vmIds);
  const add = (ids: string[]) => set({ vmIds: Array.from(new Set([...ws.vmIds, ...ids])) });
  const rem = (ids: string[]) => set({ vmIds: ws.vmIds.filter((i) => !ids.includes(i)) });
  const addedWl = ws.vmIds.map((i) => wl.find((w) => w.id === i)).filter(Boolean) as Workload[];
  const warned = addedWl.filter((w) => vmWarnings(w).length);
  const shown = tab === 'warn' ? warned : addedWl;
  const nFilters = fos.size + fpw.size + fav.size;
  return (
    <>
      <div className="two-main">
        <div className="src-line">Source: <b>{source?.name}</b></div>
        <div className="list-head">
          <button className={'btn' + (nFilters ? ' primary-outline' : '')} onClick={() => setShowF(!showF)}>&#9776; Filter{nFilters ? ` (${nFilters})` : ''}</button>
          <div className="search"><span>&#128269;</span><input value={q} placeholder="e.g. VM Apache" onChange={(e) => setQ(e.target.value)} /></div>
          <button className="btn" onClick={() => add(rows.map((r) => r.id))}>Add all VMs</button>
          <div className="spacer" /><Pager pager={pager} total={rows.length} />
        </div>
        {showF && (
          <div className="filters-inline card">
            <div><b>OS Types</b>{['windows', 'linux', 'other'].map((o) => <label className="cb" key={o}><input type="checkbox" checked={fos.has(o)} onChange={() => tg(fos, setFos, o)} /> {o[0].toUpperCase() + o.slice(1)}</label>)}</div>
            <div><b>Power States</b>{['On', 'Off'].map((o) => <label className="cb" key={o}><input type="checkbox" checked={fpw.has(o)} onChange={() => tg(fpw, setFpw, o)} /> {o}</label>)}</div>
            <div><b>Availability</b>{['FT', 'HA'].map((o) => <label className="cb" key={o}><input type="checkbox" checked={fav.has(o)} onChange={() => tg(fav, setFav, o)} /> {o}</label>)}</div>
            <button className="linkbtn" onClick={() => { setFos(new Set()); setFpw(new Set()); setFav(new Set()); }}>Clear</button>
          </div>
        )}
        <div className="table-wrap">
          <table className="grid vms-src">
            <thead><tr><th className="chk"><button className="add-btn" title="Add all VMs" aria-label="Add all VMs" onClick={() => add(rows.map((r) => r.id))}>+</button></th>
              <th className="sortable" onClick={() => setAsc(!asc)}>Name {asc ? '▲' : '▼'}</th><th>OS</th><th>Availability</th><th>Active Node</th><th>Power State</th><th>vCPU(s)</th><th>Memory</th><th>Storage</th><th>Source Network</th></tr></thead>
            <tbody>
              {wlLoading && <tr><td colSpan={10}><Spinner /></td></tr>}
              {pager.slice(rows).map((w) => {
                const on = added.has(w.id);
                return (
                  <tr key={w.id} data-testid="src-vm">
                    <td className="chk"><button className={'add-btn' + (on ? ' on' : '')} aria-label={`Vm ${w.name} ${on ? 'remove' : 'add'}`} onClick={() => (on ? rem([w.id]) : add([w.id]))}>{on ? '✓' : '+'}</button></td>
                    <td>{w.name}{vmWarnings(w).length > 0 && <Tip wide text={<>{vmWarnings(w).map((x, i) => <div key={i}>{x}</div>)}</>}><span className="warn-ic">&#9888;</span></Tip>}</td>
                    <td>{w.os_name}</td><td>{w.availability || '-'}</td><td>{w.active_node || '-'}</td><td>{w.power_state}</td><td>{w.vcpus}</td><td>{fmtMem(w.memory_mib)}</td>
                    <td>{fmtBytes(w.total_bytes)}</td><td>{Array.from(new Set(w.nics.map((n) => n.network))).join(', ')}</td>
                  </tr>
                );
              })}
              {!wlLoading && rows.length === 0 && <tr><td colSpan={10} className="empty">No VMs match.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <aside className="added-panel">
        <div className="ap-head"><b>Added VMs</b><span className="pill">{addedWl.length}</span><div className="spacer" />
          <button className="linkbtn" disabled={!addedWl.length} onClick={() => set({ vmIds: [] })}>Remove all</button></div>
        <div className="tabs"><button className={tab === 'all' ? 'active' : ''} onClick={() => setTab('all')}>All</button>
          <button className={tab === 'warn' ? 'active' : ''} onClick={() => setTab('warn')}>Warnings{warned.length ? ` (${warned.length})` : ''}</button></div>
        <ul className="added-list">
          {shown.map((w) => (
            <li key={w.id}>
              <button className="rm-btn" aria-label={`Vm ${w.name} remove`} onClick={() => rem([w.id])}>&minus;</button>
              <span className={vmWarnings(w).length ? 'warn-text' : ''}>{w.name}</span>
              {vmWarnings(w).length > 0 && <Tip wide text={<>{vmWarnings(w).map((x, i) => <div key={i}>{x}</div>)}</>}><span className="warn-ic">&#9888;</span></Tip>}
            </li>))}
          {shown.length === 0 && <li className="muted">{tab === 'warn' ? 'No warnings.' : 'No VMs added yet.'}</li>}
        </ul>
      </aside>
    </>
  );
}

function Step3({ ws, set, networks, target }: SP & { networks: string[]; target?: Provider }) {
  const subnets = target?.subnets || [];
  const allMapped = networks.every((n) => ws.netMap[n]);
  return (
    <div className="wiz-col wide">
      <h4 className="section">Network</h4>
      <p>Map the source network details to respective target network details</p>
      <table className="grid net"><thead><tr><th>Source Network</th><th>Target Type</th><th>Target Subnet</th>{ws.testNet && <th>Test Subnet</th>}</tr></thead>
        <tbody>{networks.map((n) => (
          <tr key={n}><td>{n}</td><td>VLAN</td>
            <td><select aria-label={`Target subnet for ${n}`} value={ws.netMap[n] || ''} onChange={(e) => set({ netMap: { ...ws.netMap, [n]: e.target.value } })}>
              <option value="">Select Subnet</option>{subnets.map((s) => <option key={s.id}>{s.name}</option>)}</select></td>
            {ws.testNet && <td><select aria-label={`Test subnet for ${n}`} value={ws.testMap[n] || ''} onChange={(e) => set({ testMap: { ...ws.testMap, [n]: e.target.value } })}>
              <option value="">Select Subnet</option>{subnets.map((s) => <option key={s.id}>{s.name}</option>)}</select></td>}
          </tr>))}</tbody></table>
      <hr />
      <h4 className="section">Test Network (Optional)</h4>
      <label className={'cb' + (allMapped ? '' : ' disabled')}><input id="test-net" type="checkbox" disabled={!allMapped} checked={ws.testNet} onChange={(e) => set({ testNet: e.target.checked })} />
        Add a test network for crash consistent testing of your VMs prior to migration</label>
      <p className="info-note">&#9432; This network should be non-routable and isolated from the rest of your network to avoid IP and or MAC address conflicts.</p>
    </div>
  );
}

function Step4({ ws, set, hasWin, hasLin, editing, planId, selected }: SP & { hasWin: boolean; hasLin: boolean; editing: boolean; planId?: string; selected: Workload[] }) {
  const p = ws.prep;
  const up = (x: Partial<Prep>) => set({ prep: { ...p, ...x } });
  const manual = p.mode === 'manual';
  const auto = !manual && !p.bypass_guest_ops;
  return (
    <div className="wiz-col wide">
      <h4 className="section">VM Preparation</h4>
      <p>Guest preparation installs the drivers the VM needs on AHV and keeps its network identity.</p>
      <div className="radio-cards">
        <label className={'rc' + (!manual ? ' on' : '')}><input type="radio" name="mode" checked={!manual} onChange={() => up({ mode: 'automatic' })} />
          <b>Automatic</b><span>ev2ahv connects to the guest (WinRM for Windows, SSH for Linux) with the credentials below and prepares it.</span></label>
        <label className={'rc' + (manual ? ' on' : '')}><input type="radio" name="mode" checked={manual} onChange={() => up({ mode: 'manual' })} />
          <b>Manual</b><span>You run a preparation script in each guest yourself.</span></label>
      </div>
      {manual && (
        <div className="card">
          <p>Download the script, run it as Administrator (Windows) or root (Linux) inside every VM, then start the plan.</p>
          {editing && planId ? (
            <p>{hasWin && <a className="dl" href={api.prepScriptUrl(planId, 'windows')}>Download Windows script (.ps1)</a>}{' '}
              {hasLin && <a className="dl" href={api.prepScriptUrl(planId, 'linux')}>Download Linux script (.sh)</a>}</p>
          ) : <p className="muted">The download links appear after the plan is saved (plan summary page) or when you edit the plan. Selected: {selected.length} VM(s).</p>}
        </div>
      )}
      {auto && (
        <>
          {hasWin && <div className="cred-box"><h5>Windows credentials</h5>
            <Field label="Windows user"><input value={p.windows_user} onChange={(e) => up({ windows_user: e.target.value })} /></Field>
            <Field label="Windows password" hint={editing ? 'Passwords are write-only. Enter it again to change it.' : undefined}><PasswordInput value={p.windows_password || ''} onChange={(v) => up({ windows_password: v })} /></Field></div>}
          {hasLin && <div className="cred-box"><h5>Linux credentials</h5>
            <Field label="Linux user"><input value={p.linux_user} onChange={(e) => up({ linux_user: e.target.value })} /></Field>
            <Field label="Linux password" hint="Leave empty to use the SSH key."><PasswordInput value={p.linux_password || ''} onChange={(v) => up({ linux_password: v })} /></Field></div>}
          {!hasWin && !hasLin && <p className="muted">The selected VMs need no guest credentials.</p>}
        </>
      )}
      <h4 className="section">Options</h4>
      <label className="cb"><input type="checkbox" checked={p.retain_ip} onChange={(e) => up({ retain_ip: e.target.checked })} /> Retain static IP addresses</label>
      <label className="cb"><input type="checkbox" checked={p.retain_mac} onChange={(e) => up({ retain_mac: e.target.checked })} /> Retain MAC addresses</label>
      <label className="cb"><input type="checkbox" checked={p.uninstall_guest_tools} onChange={(e) => up({ uninstall_guest_tools: e.target.checked })} /> Uninstall source guest tools (Stratus agent)</label>
      <label className="cb"><input type="checkbox" checked={p.install_ngt} onChange={(e) => up({ install_ngt: e.target.checked })} /> Install Nutanix Guest Tools (NGT)</label>
      <label className="cb"><input id="bypass" type="checkbox" checked={p.bypass_guest_ops} onChange={(e) => up({ bypass_guest_ops: e.target.checked })} /> Bypass guest operations</label>
      {p.bypass_guest_ops && <div className="alert warn">Drivers, IP and MAC retention become your responsibility. The prep-check will show a warning.</div>}
      <Field label="VirtIO driver source" hint="Optional. ISO path on the server, or a share the guest can reach.">
        <input value={p.virtio_source || ''} placeholder="e.g. /srv/iso/virtio-win.iso" onChange={(e) => up({ virtio_source: e.target.value })} /></Field>
    </div>
  );
}

function Step5({ ws, set, selected, setting }: SP & { selected: Workload[]; setting: (w: Workload) => VmSettings }) {
  const tzs = useMemo(timezones, []);
  const up = (w: Workload, x: Partial<VmSettings>) => set({ vmSet: { ...ws.vmSet, [w.id]: { ...setting(w), ...x } } });
  return (
    <div className="wiz-col full">
      <h4 className="section">VM Settings</h4>
      <p>Review and override the settings of each VM on AHV.</p>
      <div className="table-wrap"><table className="grid vmset">
        <thead><tr><th>Source VM</th><th>Target VM name</th><th>vCPU(s)</th><th>Memory (GiB)</th><th>Disk bus</th><th>Firmware</th><th>Timezone</th><th>Guest IP (for automatic preparation)</th></tr></thead>
        <tbody>{selected.map((w) => { const s = setting(w); return (
          <tr key={w.id} data-testid="vmset-row">
            <td>{w.name}<div className="sub">{w.os_name}</div></td>
            <td><input aria-label={`Target name ${w.name}`} value={s.target_name || ''} onChange={(e) => up(w, { target_name: e.target.value })} /></td>
            <td><input aria-label="vCPUs" className="num" type="number" min={1} value={s.vcpus ?? ''} onChange={(e) => up(w, { vcpus: Number(e.target.value) })} /></td>
            <td><input aria-label="Memory GiB" className="num" type="number" min={0.25} step={0.5} value={s.memory_mib ? s.memory_mib / 1024 : ''} onChange={(e) => up(w, { memory_mib: Math.round(Number(e.target.value) * 1024) })} /></td>
            <td><select aria-label="Disk bus" value={s.disk_bus} onChange={(e) => up(w, { disk_bus: e.target.value as 'scsi' | 'pci' })}><option value="scsi">SCSI</option><option value="pci">PCI</option></select>
              {s.disk_bus === 'pci' && <label className="cb small"><input type="checkbox" checked={s.convert_to_scsi} onChange={(e) => up(w, { convert_to_scsi: e.target.checked })} /> Convert PCI to SCSI after cutover</label>}</td>
            <td><select aria-label="Firmware" value={s.firmware || ''} onChange={(e) => up(w, { firmware: (e.target.value || null) as 'bios' | 'uefi' | null })}>
              <option value="">Same as source</option><option value="bios">BIOS</option><option value="uefi">UEFI</option></select></td>
            <td><select aria-label="Timezone" value={s.timezone || ''} onChange={(e) => up(w, { timezone: e.target.value || null })}>
              <option value="">Same as source</option>{tzs.map((z) => <option key={z}>{z}</option>)}</select></td>
            <td><input aria-label="Guest IP" value={s.guest_address || ''} placeholder="e.g. 10.0.0.20" onChange={(e) => up(w, { guest_address: e.target.value || null })} /></td>
          </tr>); })}</tbody></table></div>
      {selected.some((w) => w.os_type === 'windows' && setting(w).disk_bus === 'scsi' && w.warnings.some((x) => x.includes('virtio-blk only'))) &&
        <div className="alert warn">A Windows VM with virtio-blk only cannot boot from SCSI. Choose PCI with "Convert PCI to SCSI after cutover", or the prep-check will fail.</div>}
    </div>
  );
}

function Row({ label, children, step, goto }: { label: string; children: ReactNode; step: number; goto: (n: number) => void }) {
  return <><dt>{label} <button className="linkbtn small" onClick={() => goto(step)}>Edit</button></dt><dd>{children}</dd></>;
}

function Step6({ ws, set, selected, source, target, networks, setting, goto }: SP & { selected: Workload[]; source?: Provider; target?: Provider; networks: string[]; setting: (w: Workload) => VmSettings; goto: (n: number) => void }) {
  const tzs = useMemo(timezones, []);
  const p = ws.prep;
  const past = ws.schedMode === 'later' && ws.schedAt && zonedToEpoch(ws.schedAt, ws.schedTz) <= Date.now() / 1000;
  return (
    <div className="wiz-col wide">
      <h4 className="section">Summary</h4>
      <div className="card">
        <dl className="kv wide-k">
          <Row label="Source" step={0} goto={goto}>{source?.name} ({source ? typeLabel(source.type) : ''})</Row>
          <Row label="Target" step={0} goto={goto}>{target?.name}: {ws.cluster} / {ws.container}{ws.project !== 'None' ? ` / project ${ws.project}` : ''}</Row>
          <Row label="VMs" step={1} goto={goto}>{selected.length}: {selected.map((w) => setting(w).target_name || w.name).join(', ')}</Row>
          <Row label="Network" step={2} goto={goto}>{networks.map((n) => `${n} → ${ws.netMap[n]}`).join('; ')}{ws.testNet ? `; test: ${networks.map((n) => ws.testMap[n]).join(', ')}` : ''}</Row>
          <Row label="Preparation" step={3} goto={goto}>{p.mode === 'manual' ? 'Manual' : 'Automatic'}{p.bypass_guest_ops ? ', guest operations bypassed' : ''}; {p.retain_ip ? 'retain IP' : 'no IP retention'}, {p.retain_mac ? 'retain MAC' : 'no MAC retention'}{p.uninstall_guest_tools ? ', uninstall Stratus tools' : ''}{p.install_ngt ? ', install NGT' : ''}</Row>
          <Row label="VM settings" step={4} goto={goto}>{selected.map((w) => { const s = setting(w); return `${s.target_name}: ${s.vcpus} vCPU, ${fmtMem(s.memory_mib)}, ${s.disk_bus.toUpperCase()}${s.disk_bus === 'pci' && s.convert_to_scsi ? ' → SCSI' : ''}`; }).join(' | ')}</Row>
        </dl>
      </div>
      <h4 className="section">Schedule</h4>
      <div className="card">
        <label className="cb"><input type="radio" name="sched" checked={ws.schedMode === 'now'} onChange={() => set({ schedMode: 'now' })} /> Start data seeding now (when you click Save and Start)</label>
        <label className="cb"><input type="radio" name="sched" checked={ws.schedMode === 'later'} onChange={() => set({ schedMode: 'later' })} /> Schedule data seeding for a date and time</label>
        {ws.schedMode === 'later' && (
          <div className="sched-row">
            <input type="datetime-local" aria-label="Seeding date and time" value={ws.schedAt} onChange={(e) => set({ schedAt: e.target.value })} />
            <select aria-label="Timezone" value={ws.schedTz} onChange={(e) => set({ schedTz: e.target.value })}>{tzs.map((z) => <option key={z}>{z}</option>)}</select>
            <span className="muted">{ws.schedTz === 'Asia/Jakarta' ? 'WIB' : tzLabel(ws.schedTz)}</span>
          </div>)}
        {past && <div className="field-error">Pick a time in the future.</div>}
        <Field label="Resync interval" hint="While a VM waits for cutover it is resynced at this interval.">
          <select value={ws.resync} onChange={(e) => set({ resync: Number(e.target.value) })}>
            {(RESYNC.includes(ws.resync) ? RESYNC : [...RESYNC, ws.resync].sort((a, b) => a - b)).map((m) => <option key={m} value={m}>{m >= 60 ? `${m / 60} hour${m === 60 ? '' : 's'}` : `${m} minutes`}</option>)}
          </select></Field>
      </div>
      <p className="muted">Save keeps the plan as Not Started. Save and Start begins data seeding{ws.schedMode === 'later' ? ' at the scheduled time' : ' now'}.</p>
    </div>
  );
}
