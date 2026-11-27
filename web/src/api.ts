// Typed client for the ev2ahv API (/api/v2). Types mirror the pydantic models in openapi.json.

export type ProviderType = 'everrun' | 'aos' | 'plans';
export type PlanStatus = 'Not Started' | 'In Progress' | 'Paused' | 'Completed' | 'Failed' | 'Cancelled' | 'Scheduled';
export type VmStatus =
  | 'Queued' | 'Preparing' | 'Seeding' | 'Ready to Cutover' | 'Scheduled for Cutover' | 'Cutover in Progress'
  | 'Testing' | 'Tested' | 'Rolling Back' | 'Completed' | 'Failed' | 'Paused' | 'Cancelled';
export type PlanAction = 'start' | 'pause' | 'resume' | 'cutover' | 'test' | 'cancel' | 'retry' | 'discard' | 'complete' | 'rollback' | 'resync';
export type CheckStatus = 'PASS' | 'WARN' | 'FAIL';

export interface AppInfo { name: string; version: string; build: string; mode: 'mock' | 'live'; user: string | null; role?: 'admin' | 'viewer' | null; must_change?: boolean; appliance?: boolean }
export interface UiUser { username: string; role: 'admin' | 'viewer'; must_change: boolean; source: string; created_at?: number | null }
export interface Subnet { id: string; name: string; type?: 'VLAN' | 'OVERLAY' | ''; vpc?: string | null }
export interface Named { id: string; name: string }
export interface ProviderIn {
  name: string; type: ProviderType; address: string; username: string;
  password?: string | null; api_key?: string | null; node_addresses: string[];
}
export interface Provider {
  id: string; name: string; type: ProviderType; role: 'source' | 'target'; address: string; username: string;
  auth: 'password' | 'api_key'; status: 'Connected' | 'Refreshing' | 'Error'; error?: string | null;
  vm_count: number; last_refreshed?: number | null; version?: string | null; node_addresses: string[];
  clusters: Named[]; containers: Named[]; subnets: Subnet[]; projects?: Named[];
}
export interface Nic { network: string; mac: string; ip?: string | null }
export interface Workload {
  id: string; name: string; os_type: 'windows' | 'linux' | 'other'; os_name: string; power_state: 'On' | 'Off';
  availability: 'FT' | 'HA' | ''; active_node: string; vcpus: number; memory_mib: number; firmware: 'bios' | 'uefi';
  disks: number; total_bytes: number; nics: Nic[]; warnings: string[];
}
export interface NetworkMap { source_network: string; target_subnet: string; test_subnet?: string | null }
export interface Prep {
  mode: 'automatic' | 'manual'; windows_user: string; windows_password?: string | null; linux_user: string;
  linux_password?: string | null; retain_ip: boolean; retain_mac: boolean; uninstall_guest_tools: boolean;
  install_ngt: boolean; bypass_guest_ops: boolean; virtio_source?: string | null;
}
export interface VmSettings {
  target_name?: string | null; vcpus?: number | null; memory_mib?: number | null; disk_bus: 'scsi' | 'pci';
  convert_to_scsi: boolean; firmware?: 'bios' | 'uefi' | null; timezone?: string | null; guest_address?: string | null;
  cbt?: 'off' | 'auto'; final_verify?: 'full' | 'sample' | 'none';
}
export interface Schedule { seed_at?: number | null; timezone: string; resync_minutes: number }
export interface VmCounts { total: number; in_progress: number; ready_to_cutover: number; failed: number; paused: number; completed: number }
export interface Check { name: string; status: CheckStatus; detail: string }
export interface PlanVm {
  id: string; name: string; os_type: string; status: VmStatus; seed_percent: number; data_transferred_bytes: number;
  total_bytes: number; eta_seconds?: number | null; last_sync_at?: number | null; target_vm_id?: string | null;
  target_vm_url?: string | null; test_vm_id?: string | null; checks: Check[]; error?: string | null; actions: PlanAction[];
  phase?: string | null; stage?: string | null; sync_progress?: number | null; cutover_result?: CutoverResult | null;
}
export interface CutoverResult {
  completed_at?: number | null; target_vm_id?: string | null; downtime_seconds?: number | null; final_sync_seconds?: number | null;
  image_upload_seconds?: number | null; conversion_seconds?: number | null; last_resync_seconds?: number | null;
  verify: Record<string, unknown>;
}
export interface PlanVmX extends PlanVm { plan_id: string; plan_name: string }
export interface Plan {
  id: string; name: string; created_by: string; created_at: number; source?: Named | null; target?: Named | null;
  cluster?: string | null; container?: string | null; project?: string | null; network_map: NetworkMap[];
  test_network: boolean; prep: Prep; vm_settings: Record<string, VmSettings>; schedule: Schedule; status: PlanStatus;
  started_at?: number | null; data_migrated_bytes: number; counts: VmCounts; vms: PlanVm[]; actions: string[];
}
export interface PlanIn {
  name: string; source_id?: string | null; target_id?: string | null; cluster?: string | null; container?: string | null;
  project?: string | null; vm_ids: string[]; network_map: NetworkMap[]; test_network: boolean; prep: Prep;
  vm_settings: Record<string, VmSettings>; schedule: Schedule; start: boolean;
}
export interface ActionIn { action: PlanAction; vm_ids: string[]; force: boolean }
export interface EventRow { time: number; severity: 'info' | 'warning' | 'error'; plan?: string | null; vm?: string | null; message: string }
export interface BandwidthPolicy { id: string; name: string; source_id: string; source_name: string; frequency: string; limit_mbps: number }
export interface Writer { vm?: string | null; host?: string | null; user?: string | null }
export interface Settings { id?: string; resync_minutes: number; bandwidth_policies: BandwidthPolicy[]; timezone: string; writer?: Writer | null; source_name_prefix?: string; ldap?: LdapSettings }
export interface LdapSettings {
  enabled: boolean; url?: string; bind_dn?: string; bind_password?: string; user_base?: string; user_filter?: string;
  admin_group?: string; viewer_group?: string; start_tls?: boolean; verify_tls?: boolean; ca_file?: string; allow_plain?: boolean;
}
export interface SelfTestStep { time: number | string; message: string }
export interface SelfTest { status: 'never run' | 'running' | 'PASS' | 'FAIL'; steps?: SelfTestStep[]; error?: string | null; started?: number | null; ended?: number | null }
export interface Health {
  vm_uuid: string; ahv_vm: { name: string; power: string; disks: number } | null; prism: string | null; selftest: SelfTest;
  tls: { subject: string; issuer: string; not_after: string | number; self_signed: boolean; sha256: string } | null; ssh_public_key: string | null;
}
export interface ListResult<T> { entities: T[]; total: number }

export class ApiError extends Error {
  status: number;
  failing: string[];
  mustChange = false;
  constructor(status: number, message: string, failing: string[] = []) {
    super(message);
    this.status = status;
    this.failing = failing;
  }
}

const BASE = '/api/v2';
export interface UpgradeState {
  enabled: boolean; pending: boolean;
  status: { state?: 'running' | 'done' | 'failed'; message?: string; by?: string; version?: string; from_version?: string; time?: number };
}
export const authEvents = new EventTarget();

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      credentials: 'same-origin',
      headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'Cannot reach the ev2ahv server.');
  }
  if (res.status === 401 && path !== '/token') authEvents.dispatchEvent(new Event('unauthorized'));
  const text = await res.text();
  let data: unknown = undefined;
  try { data = text ? JSON.parse(text) : undefined; } catch { data = text; }
  if (!res.ok) {
    const d = data as { message?: string; detail?: unknown; failing?: string[] } | string | undefined;
    let msg = res.statusText || `HTTP ${res.status}`;
    let failing: string[] = [];
    if (typeof d === 'string' && d) msg = d;
    else if (d && typeof d === 'object') {
      if (typeof d.message === 'string') msg = d.message;
      else if (Array.isArray(d.detail)) msg = d.detail.map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`).join('; ');
      if (Array.isArray(d.failing)) failing = d.failing;
    }
    const err = new ApiError(res.status, msg, failing);
    if (res.status === 403 && d && typeof d === 'object' && (d as { must_change?: boolean }).must_change) {
      err.mustChange = true;
      authEvents.dispatchEvent(new Event('must_change'));
    }
    throw err;
  }
  return data as T;
}

export const api = {
  appinfo: () => req<AppInfo>('GET', '/appinfo'),
  login: (username: string, password: string) => req<{ user: string; must_change?: boolean }>('POST', '/token', { username, password }),
  logout: () => req<unknown>('POST', '/logout'),
  changePassword: (current: string, next: string) => req<unknown>('POST', '/password', { current, new: next }),
  users: () => req<{ entities: UiUser[] }>('GET', '/users'),
  addUser: (username: string, password: string, role: 'admin' | 'viewer') => req<UiUser>('POST', '/users', { username, password, role }),
  updateUser: (username: string, b: { role?: 'admin' | 'viewer'; password?: string }) => req<UiUser>('PUT', `/users/${encodeURIComponent(username)}`, b),
  deleteUser: (username: string) => req<unknown>('DELETE', `/users/${encodeURIComponent(username)}`),

  health: () => req<Health>('GET', '/health'),
  runSelftest: () => req<unknown>('POST', '/health/selftest', {}),
  putTls: (cert: string, key: string) => req<unknown>('PUT', '/tls', { cert, key }),

  providers: () => req<ListResult<Provider>>('GET', '/providers'),
  provider: (id: string) => req<Provider>('GET', `/providers/${id}`),
  validateProvider: (b: ProviderIn) => req<{ ok: boolean; version: string }>('POST', '/providers/validate', b),
  addProvider: (b: ProviderIn) => req<Provider>('POST', '/providers', b),
  editProvider: (id: string, b: ProviderIn) => req<Provider>('PUT', `/providers/${id}`, b),
  deleteProvider: (id: string) => req<unknown>('DELETE', `/providers/${id}`),
  refreshProvider: (id: string) => req<Provider>('POST', `/providers/${id}/refresh`),
  workloads: (id: string) => req<ListResult<Workload>>('GET', `/providers/${id}/workloads`),

  plans: (status?: string) => req<ListResult<Plan>>('GET', '/plans' + (status ? `?status=${encodeURIComponent(status)}` : '')),
  planVms: (status?: string) => req<ListResult<PlanVmX>>('GET', '/plans/vms' + (status ? `?status=${encodeURIComponent(status)}` : '')),
  plan: (id: string) => req<Plan>('GET', `/plans/${id}`),
  createPlan: (b: PlanIn) => req<Plan>('POST', '/plans', b),
  editPlan: (id: string, b: PlanIn) => req<Plan>('PUT', `/plans/${id}`, b),
  deletePlan: (id: string, cleanup = false) => req<{ cleanup_started?: string[] }>('DELETE', `/plans/${id}${cleanup ? '?cleanup=true' : ''}`),
  startPlan: (id: string) => req<Plan>('POST', `/plans/${id}/start`),
  planAction: (id: string, b: ActionIn) => req<Plan>('POST', `/plans/${id}/action`, b),
  precheck: (id: string) => req<Plan>('POST', `/plans/${id}/precheck`),
  prepScriptUrl: (id: string | undefined, os: 'windows' | 'linux') => `${BASE}${id ? `/plans/${id}` : ''}/prep-script?os_type=${os}`,
  vmLog: async (planId: string, vmId: string) => {
    const res = await fetch(`${BASE}/plans/${planId}/vms/${vmId}/log`, { credentials: 'same-origin' });
    if (res.status === 401) authEvents.dispatchEvent(new Event('unauthorized'));
    if (!res.ok) throw new ApiError(res.status, 'Could not load the VM log');
    return res.text();
  },
  planEvents: (id: string) => req<ListResult<EventRow>>('GET', `/plans/${id}/events`),

  events: (limit = 1000) => req<ListResult<EventRow>>('GET', `/events?limit=${limit}`),
  settings: () => req<Settings>('GET', '/settings'),
  putSettings: (b: Partial<Settings>) => req<Settings>('PUT', '/settings', b),
  supportBundleUrl: () => `${BASE}/supportbundle`,
  upgradeStatus: () => req<UpgradeState>('GET', '/upgrade'),
  uploadUpgrade: async (file: File, force = false): Promise<UpgradeState> => {
    const res = await fetch(`${BASE}/upgrade${force ? '?force=true' : ''}`, {
      method: 'PUT', credentials: 'same-origin', headers: { 'content-type': 'application/octet-stream' }, body: file,
    });
    const text = await res.text();
    let data: { detail?: unknown; message?: string } & Partial<UpgradeState> = {};
    try { data = text ? JSON.parse(text) : {}; } catch { /* not JSON */ }
    if (!res.ok) throw new ApiError(res.status, typeof data.detail === 'string' ? data.detail : data.message || res.statusText);
    return data as UpgradeState;
  },
  logs: async (lines = 300) => {
    const res = await fetch(`${BASE}/logs?lines=${lines}`, { credentials: 'same-origin' });
    if (res.status === 401) authEvents.dispatchEvent(new Event('unauthorized'));
    if (!res.ok) throw new ApiError(res.status, 'Could not load logs');
    return res.text();
  },
  docsUrl: '/api/v2/docs',
};

export const DEFAULT_PREP: Prep = {
  mode: 'automatic', windows_user: 'Administrator', windows_password: '', linux_user: 'root', linux_password: '',
  retain_ip: true, retain_mac: true, uninstall_guest_tools: true, install_ngt: false, bypass_guest_ops: false, virtio_source: '',
};
