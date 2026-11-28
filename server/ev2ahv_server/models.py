"""API contract. These models are the single source of truth; /api/v2/openapi.json is generated from them.

Names follow Nutanix Move 6.2 (see docs/move-reference): providers = environments, plans, workloads.
Secrets are write-only: request models accept them, response models never contain them.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

ProviderType = Literal["everrun", "aos", "plans"]  # plans: a directory of engine plan YAML files (CLI plans, test rigs)
PlanStatus = Literal["Not Started", "In Progress", "Paused", "Completed", "Failed", "Cancelled", "Scheduled"]
VmStatus = Literal["Queued", "Preparing", "Seeding", "Ready to Cutover", "Scheduled for Cutover", "Cutover in Progress",
                   "Testing", "Tested", "Rolling Back", "Completed", "Failed", "Paused", "Cancelled"]
PlanAction = Literal["start", "pause", "resume", "cutover", "test", "cancel", "retry", "discard", "complete", "rollback",
                     "resync"]
CheckStatus = Literal["PASS", "WARN", "FAIL"]


class Login(BaseModel):
    username: str
    password: str


class AppInfo(BaseModel):
    name: str = "ev2ahv"
    version: str
    build: str = ""
    mode: Literal["mock", "live"]
    user: Optional[str] = None
    role: Optional[Literal["admin", "viewer"]] = None
    must_change: bool = False
    appliance: bool = False


class Named(BaseModel):
    id: str
    name: str


class Subnet(Named):
    """A Prism subnet (D8c): VLAN or OVERLAY (a VPC subnet); vpc is the VPC name for overlay subnets."""
    type: Literal["VLAN", "OVERLAY", ""] = ""
    vpc: Optional[str] = None


class ProviderIn(BaseModel):
    name: str
    type: ProviderType
    address: str = Field(description="everRun system (mgmt) IP, or Prism Central IP/FQDN")
    username: str
    password: Optional[str] = Field(None, description="write-only")
    api_key: Optional[str] = Field(None, description="write-only, Prism Central only")
    node_addresses: List[str] = Field(default_factory=list, description="everRun node IPs for read-only disk access")
    ssh_user: str = Field("root", description="everRun host account for SSH. Anything but root is used with sudo -n and an exact command allowlist (docs/least-privilege.md)")
    storage_path: str = Field("/mnt/everrun", description="everRun volume path on the nodes; the sudoers allowlist is limited to it")


class Provider(BaseModel):
    id: str
    name: str
    type: ProviderType
    role: Literal["source", "target"]
    address: str
    username: str
    auth: Literal["password", "api_key"] = "password"
    status: Literal["Connected", "Refreshing", "Error"] = "Connected"
    error: Optional[str] = None
    vm_count: int = 0
    last_refreshed: Optional[float] = None
    version: Optional[str] = None
    node_addresses: List[str] = Field(default_factory=list)
    ssh_user: str = "root"
    storage_path: str = "/mnt/everrun"
    clusters: List[Named] = Field(default_factory=list)
    containers: List[Named] = Field(default_factory=list)
    subnets: List[Subnet] = Field(default_factory=list)
    projects: List[Named] = Field(default_factory=list, description="Prism projects (target environments)")


class Nic(BaseModel):
    network: str
    mac: str
    ip: Optional[str] = None


class Workload(BaseModel):
    """A source VM in an environment's inventory."""
    id: str
    name: str
    os_type: Literal["windows", "linux", "other"]
    os_name: str = ""
    power_state: Literal["On", "Off"]
    availability: Literal["FT", "HA", ""] = ""
    active_node: str = ""
    vcpus: int = 1
    memory_mib: int = 1024
    firmware: Literal["bios", "uefi"] = "bios"
    disks: int = 1
    total_bytes: int = 0
    nics: List[Nic] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class NetworkMap(BaseModel):
    source_network: str
    target_subnet: str
    test_subnet: Optional[str] = None


class Prep(BaseModel):
    mode: Literal["automatic", "manual"] = "automatic"
    windows_user: str = "Administrator"
    windows_password: Optional[str] = Field(None, description="write-only")
    linux_user: str = "root"
    linux_password: Optional[str] = Field(None, description="write-only; ssh key is used when empty")
    retain_ip: bool = True
    retain_mac: bool = True
    uninstall_guest_tools: bool = True
    install_ngt: bool = False
    bypass_guest_ops: bool = False
    virtio_source: Optional[str] = Field(None, description="ISO path on the server, or a share the guest can reach")


CBT_WARNING = ("Uses QEMU monitor commands on everRun nodes, which Stratus documentation advises against. "
               "Keep off unless Stratus has agreed.")


class VmSettings(BaseModel):
    target_name: Optional[str] = None
    vcpus: Optional[int] = None
    memory_mib: Optional[int] = None
    disk_bus: Literal["scsi", "pci"] = "scsi"
    convert_to_scsi: bool = True
    firmware: Optional[Literal["bios", "uefi"]] = None
    timezone: Optional[str] = None
    guest_address: Optional[str] = Field(None, description="IP the server uses to reach the guest for automatic prep")
    cbt: Literal["off", "auto"] = Field("off", description="changed-block tracking for resyncs (experimental, everRun direct sources only; docs/cbt.md). " + CBT_WARNING)
    cbt_confirmed: bool = Field(False, description="must be true to switch cbt to auto: the operator has read the Stratus warning")
    final_verify: Literal["full", "sample", "none"] = Field("full", description="how the final sync checks the target after its source pass: full (hash the target too, default), sample (compare a random 1 percent), none (trust the recorded digests); docs/cbt.md")


class VmSettingsPatch(BaseModel):
    cbt: Optional[Literal["off", "auto"]] = None
    cbt_confirmed: Optional[bool] = Field(None, description="required (true) together with cbt=auto. " + CBT_WARNING)
    final_verify: Optional[Literal["full", "sample", "none"]] = None


class Schedule(BaseModel):
    seed_at: Optional[float] = Field(None, description="epoch seconds; None = start now")
    timezone: str = "Asia/Jakarta"
    resync_minutes: int = Field(60, ge=5, description="automatic resync interval while waiting for cutover (5 min or more)")


class PlanIn(BaseModel):
    name: str
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    cluster: Optional[str] = None
    container: Optional[str] = None
    project: Optional[str] = None
    vm_ids: List[str] = Field(default_factory=list)
    network_map: List[NetworkMap] = Field(default_factory=list)
    test_network: bool = False
    prep: Prep = Field(default_factory=Prep)
    vm_settings: Dict[str, VmSettings] = Field(default_factory=dict)
    schedule: Schedule = Field(default_factory=Schedule)
    start: bool = Field(False, description="Save and Start")


class VmCounts(BaseModel):
    total: int = 0
    in_progress: int = 0
    ready_to_cutover: int = 0
    failed: int = 0
    paused: int = 0
    completed: int = 0


class Check(BaseModel):
    name: str
    status: CheckStatus
    detail: str = ""


class CutoverResult(BaseModel):
    """What the last successful cutover measured, kept on the plan VM (engine timings, in seconds)."""
    completed_at: Optional[float] = Field(None, description="epoch seconds; null when unknown (plans stored before 0.3.x)")
    target_vm_id: Optional[str] = None
    downtime_seconds: Optional[float] = Field(None, description="source shutdown requested to target verified")
    final_sync_seconds: Optional[float] = None
    image_upload_seconds: Optional[float] = None
    conversion_seconds: Optional[float] = None
    last_resync_seconds: Optional[float] = None
    verify: Dict[str, object] = Field(default_factory=dict, description="power state and reachability seen by verify")


class PlanVm(BaseModel):
    id: str
    name: str
    os_type: str = ""
    status: VmStatus = "Queued"
    seed_percent: float = 0
    data_transferred_bytes: int = 0
    total_bytes: int = 0
    eta_seconds: Optional[int] = None
    last_sync_at: Optional[float] = None
    target_vm_id: Optional[str] = None
    target_vm_url: Optional[str] = None
    test_vm_id: Optional[str] = None
    checks: List[Check] = Field(default_factory=list)
    error: Optional[str] = None
    phase: Optional[str] = Field(None, description="engine step running now, e.g. guest-prep, seed, resync, cutover")
    stage: Optional[str] = Field(None, description="part of the cutover running now, e.g. final sync, image upload")
    sync_progress: Optional[float] = Field(None, description="percent of the running sync pass (resync, cutover)")
    cutover_result: Optional[CutoverResult] = Field(None, description="set when a cutover completes, cleared on rollback")
    actions: List[PlanAction] = Field(default_factory=list)

    @field_validator("cutover_result", mode="before")
    @classmethod
    def _legacy_cutover_result(cls, v):
        """Plans stored before 0.3.x kept the engine's raw result {"vm", "timings", "verify", "conversion"}; read it
        in the current shape instead of failing the whole plan list (found on a real 0.2.0 -> 0.3.1 upgrade)."""
        if isinstance(v, dict) and "completed_at" not in v:
            t = v.get("timings") or {}
            ver = {k: x for k, x in (v.get("verify") or {}).items() if isinstance(x, (str, int, float, bool))}
            keep = {k: t[k] for k in ("downtime_seconds", "final_sync_seconds", "image_upload_seconds",
                                      "conversion_seconds", "last_resync_seconds") if t.get(k) is not None}
            return {"completed_at": None, "target_vm_id": v.get("vm"), "verify": ver, **keep}
        if isinstance(v, dict) and not v.get("completed_at"):
            return {**v, "completed_at": None}   # 0.3.2 stored 0 for unknown; never render 1970 (D17)
        return v


class Plan(BaseModel):
    id: str
    name: str
    created_by: str
    created_at: float
    source: Optional[Named] = None
    target: Optional[Named] = None
    cluster: Optional[str] = None
    container: Optional[str] = None
    project: Optional[str] = None
    network_map: List[NetworkMap] = Field(default_factory=list)
    test_network: bool = False
    prep: Prep = Field(default_factory=Prep)
    vm_settings: Dict[str, VmSettings] = Field(default_factory=dict)
    schedule: Schedule = Field(default_factory=Schedule)
    status: PlanStatus = "Not Started"
    started_at: Optional[float] = None
    data_migrated_bytes: int = 0
    counts: VmCounts = Field(default_factory=VmCounts)
    vms: List[PlanVm] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list, description="plan-row Actions menu: Start, Edit, Delete")


class ActionIn(BaseModel):
    action: PlanAction
    vm_ids: List[str] = Field(default_factory=list, description="empty = every VM the action applies to")
    force: bool = False


class Event(BaseModel):
    time: float
    severity: Literal["info", "warning", "error"]
    plan: Optional[str] = None
    vm: Optional[str] = None
    message: str


class ListResult(BaseModel):
    entities: list
    total: int
