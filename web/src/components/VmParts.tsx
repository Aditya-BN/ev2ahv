import { ReactNode, useEffect, useState } from 'react';
import { api, ApiError, PlanAction, PlanVm, VmStatus } from '../api';
import { Modal, Popover, useDialogs } from './ui';
import { vmTone, worstCheck } from '../util';

export const ACTION_ORDER: PlanAction[] = ['retry', 'discard', 'cutover', 'resync', 'test', 'cancel', 'resume', 'pause', 'complete', 'rollback'];
export const ACTION_LABEL: Record<string, string> = {
  start: 'Start', retry: 'Retry', discard: 'Discard', cutover: 'Cutover', test: 'Test', cancel: 'Cancel', resume: 'Resume',
  pause: 'Pause', complete: 'Complete', rollback: 'Rollback', resync: 'Resync now',
};

export function VmStatusBadge({ s }: { s: VmStatus }) {
  const icon = s === 'Completed' ? '✓' : s === 'Failed' ? '▲' : s === 'Paused' ? '❚❚' : s === 'Cancelled' ? '✕' : '●';
  return <span className={'vm-status ' + vmTone(s)}><span className="si">{icon}</span> {s}</span>;
}

// Prep-script downloads. Always offered: live discovery often leaves the guest OS unknown, so
// both scripts show unless every VM is known to be one OS. `planId` is optional (unsaved wizard plan).
export function PrepScripts({ planId, os, mode }: { planId?: string; os: string[]; mode: 'manual' | 'automatic' }) {
  const win = os.includes('windows'), lin = os.includes('linux');
  const unknown = os.some((o) => o !== 'windows' && o !== 'linux') || (!win && !lin);
  const showWin = win || unknown, showLin = lin || unknown;
  const auto = mode !== 'manual';
  return (
    <div className="prep-dl" data-testid="prep-scripts">
      <p className="lead">{auto
        ? 'Automatic mode runs the preparation for you. Download the scripts only if you want to review what it does.'
        : 'Run the script as Administrator (Windows) or root (Linux) inside every VM, then start the plan.'}</p>
      {showWin && <a className={'dl' + (auto ? ' review' : '')} href={api.prepScriptUrl(planId, 'windows')}>{auto ? 'Download for review: Windows (.ps1)' : 'Download Windows script (.ps1)'}</a>}
      {showLin && <a className={'dl' + (auto ? ' review' : '')} href={api.prepScriptUrl(planId, 'linux')}>{auto ? 'Download for review: Linux (.sh)' : 'Download Linux script (.sh)'}</a>}
      {unknown && <p className="muted">The guest OS of some VMs is not known yet, so both scripts are offered.</p>}
    </div>
  );
}

export function ChecksBadge({ vm }: { vm: PlanVm }) {
  if (vm.phase === 'prep-check' || vm.phase === 'guest-prep') return <span className="chk-badge running" title="Prep-check is running">Checking…</span>;
  const w = worstCheck(vm);
  if (!w) return <span className="muted">-</span>;
  return (
    <Popover title={`Prep-check: ${vm.name}`} trigger={<span className={'chk-badge ' + w.toLowerCase()}>{w}</span>}>
      <ul className="checks">
        {vm.checks.map((c, i) => (
          <li key={i}><span className={'chk-badge sm ' + c.status.toLowerCase()}>{c.status}</span>
            <span><b>{c.name}</b>{c.detail ? <><br /><span className="muted">{c.detail}</span></> : null}</span></li>
        ))}
      </ul>
    </Popover>
  );
}

export function ProgressBar({ pct, tone }: { pct: number; tone?: string }) {
  return (
    <div className="pbar-wrap"><div className="pbar"><div className={'pbar-fill ' + (tone || '')} style={{ width: `${Math.min(100, pct)}%` }} /></div>
      <span className="pbar-pct">{Math.round(pct)}%</span></div>
  );
}

type Target = { id: string; name: string };
interface Pending { planId: string; action: PlanAction; vms: Target[]; testSubnet?: string | null; blocked?: { message: string; failing: string[] } }

// Runs plan actions with Move-style confirmation dialogs. Only call with actions the server listed.
export function useVmActions(onDone: () => void) {
  const { showError, toast } = useDialogs();
  const [p, setP] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);

  const exec = async (pp: Pending, force = false) => {
    setBusy(true);
    try {
      await api.planAction(pp.planId, { action: pp.action, vm_ids: pp.vms.map((v) => v.id), force });
      toast(`${ACTION_LABEL[pp.action]} requested for ${pp.vms.length} VM${pp.vms.length === 1 ? '' : 's'}`);
      setP(null); onDone();
    } catch (e) {
      const a = e as ApiError;
      if (pp.action === 'cutover' && a.status === 409 && a.failing.length) setP({ ...pp, blocked: { message: a.message, failing: a.failing } });
      else { setP(null); showError(e, `${ACTION_LABEL[pp.action]} failed`); }
    } finally { setBusy(false); }
  };

  const run = (planId: string, action: PlanAction, vms: Target[], testSubnet?: string | null) => {
    const pp: Pending = { planId, action, vms, testSubnet };
    if (['pause', 'resume', 'retry', 'complete', 'resync'].includes(action)) exec(pp); else setP(pp);
  };

  let dialog: ReactNode = null;
  if (p) {
    const names = <ul className="plain vmlist">{p.vms.map((v) => <li key={v.id}>{v.name}</li>)}</ul>;
    const close = () => setP(null);
    const cancelBtn = <button className="btn" onClick={close} disabled={busy}>Cancel</button>;
    if (p.action === 'cutover') {
      dialog = p.blocked ? (
        <Modal title="Cutover blocked by prep-check" width={560} tone="danger" onClose={close}
          footer={<>{cancelBtn}<button className="btn danger" id="cutover-force" disabled={busy} onClick={() => exec(p, true)}>Cutover anyway (force)</button></>}>
          <p>{p.blocked.message}</p>
          <p><b>Failing checks</b></p>
          <ul className="plain fails">{p.blocked.failing.map((f) => <li key={f}><span className="chk-badge sm fail">FAIL</span> {f}</li>)}</ul>
          <p className="muted">Forcing the cutover ignores these failures. The migration may end with a VM that does not boot on AHV. Rollback stays available.</p>
        </Modal>
      ) : (
        <Modal title="Cutover" width={540} onClose={close}
          footer={<>{cancelBtn}<button className="btn primary" id="cutover-confirm" disabled={busy} onClick={() => exec(p)}>Cutover</button></>}>
          <p>Cutover causes <b>downtime</b> for the selected VM{p.vms.length === 1 ? '' : 's'}:</p>
          <ol className="steps"><li>The source VM is shut down.</li><li>A final sync copies the remaining changes to AHV.</li><li>The AHV VM is created and powered on.</li></ol>
          <p>Applications on the source stop being available until the AHV VM is up. After cutover you can roll back.</p>
          {names}
        </Modal>
      );
    } else if (p.action === 'test') {
      dialog = (
        <Modal title="Test Migration" width={520} onClose={close}
          footer={<>{cancelBtn}<button className="btn primary" id="test-confirm" disabled={busy} onClick={() => exec(p)}>Test</button></>}>
          <p>A test copy of the VM is booted on AHV{p.testSubnet ? <> on the isolated test subnet <b>{p.testSubnet}</b></> : ' on the isolated test network'} so you can check that it starts. The source VM keeps running and is not changed. Use <b>Discard</b> afterwards to remove the test copy.</p>
          {names}
        </Modal>
      );
    } else if (p.action === 'rollback') {
      dialog = (
        <Modal title="Rollback" width={560} tone="danger" onClose={close}
          footer={<>{cancelBtn}<button className="btn danger" id="rollback-confirm" disabled={busy} onClick={() => exec(p)}>Rollback</button></>}>
          <p>Rollback powers the <b>AHV VM off</b> and powers the <b>source VM back on</b>.</p>
          <p className="warn-text">Data written on AHV after cutover is NOT copied back to the source.</p>
          {names}
        </Modal>
      );
    } else if (p.action === 'cancel' || p.action === 'discard') {
      const cancelling = p.action === 'cancel';
      dialog = (
        <Modal title={cancelling ? 'Cancel Migration' : 'Discard Test'} width={500} tone={cancelling ? 'danger' : undefined} onClose={close}
          footer={<><button className="btn" onClick={close} disabled={busy}>{cancelling ? 'Keep Migrating' : 'Cancel'}</button>
            <button className={'btn ' + (cancelling ? 'danger' : 'primary')} id="confirm-ok" disabled={busy} onClick={() => exec(p)}>{cancelling ? 'Cancel Migration' : 'Discard'}</button></>}>
          <p>{cancelling ? 'Stop the migration of the selected VMs? Seeded data on AHV is not used afterwards. The source VMs are not changed.' : 'Discard the test copy of the selected VMs? The VMs return to Ready to Cutover.'}</p>
          {names}
        </Modal>
      );
    }
  }
  return { run, dialog };
}

export function PhaseInfo({ vm }: { vm: PlanVm }) {
  const showBar = (vm.phase === 'resync' || (vm.phase === 'cutover' && (!vm.stage || vm.stage === 'final sync'))) && vm.sync_progress != null;
  return (
    <>
      {vm.phase && <div className="phase">{vm.phase}{vm.stage ? `: ${vm.stage}` : ''}</div>}
      {showBar && <div className="sync-bar" title={`Current sync pass: ${Math.round(vm.sync_progress || 0)}%`}><div style={{ width: `${Math.min(100, vm.sync_progress || 0)}%` }} /></div>}
    </>
  );
}

export function VmLogDialog({ planId, vm, onClose }: { planId: string; vm: Target; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let alive = true;
    const load = () => api.vmLog(planId, vm.id).then((t) => { if (alive) { setText(t); setErr(''); } }).catch((e) => { if (alive) setErr(e.message); });
    load();
    const t = window.setInterval(load, 5000);
    return () => { alive = false; window.clearInterval(t); };
  }, [planId, vm.id]);
  return (
    <Modal title={`Log: ${vm.name}`} width={820} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>Close</button>}>
      {err && <div className="alert bad">{err}</div>}
      <pre className="logview short">{text === null ? 'Loading...' : text || '(empty)'}</pre>
      <p className="muted small">Refreshes every 5 s.</p>
    </Modal>
  );
}
