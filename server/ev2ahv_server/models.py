"""API contract. These models are the single source of truth; /api/v2/openapi.json is generated from them.

Names follow Nutanix Move 6.2 (see docs/move-reference): providers = environments, plans, workloads.
Secrets are write-only: request models accept them, response models never contain them.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

ProviderType = Literal["everrun", "aos"]
PlanStatus = Literal["Not Started", "In Progress", "Paused", "Completed", "Failed", "Cancelled", "Scheduled"]
VmStatus = Literal["Queued", "Preparing", "Seeding", "Ready to Cutover", "Scheduled for Cutover", "Cutover in Progress",
                   "Testing", "Tested", "Completed", "Failed", "Paused", "Cancelled"]
PlanAction = Literal["start", "pause", "resume", "cutover", "test", "cancel", "retry", "discard", "complete", "rollback"]
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


class Named(BaseModel):
    id: str
    name: str


class ProviderIn(BaseModel):
    name: str
    type: ProviderType
    address: str = Field(description="everRun system (mgmt) IP, or Prism Central IP/FQDN")
    username: str
    password: Optional[str] = Field(None, description="write-only")
    api_key: Optional[str] = Field(None, description="write-only, Prism Central only")
    node_addresses: List[str] = Field(default_factory=list, description="everRun node IPs for read-only disk access")


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
    clusters: List[Named] = Field(default_factory=list)
    containers: List[Named] = Field(default_factory=list)
    subnets: List[Named] = Field(default_factory=list)


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


class VmSettings(BaseModel):
    target_name: Optional[str] = None
    vcpus: Optional[int] = None
    memory_mib: Optional[int] = None
    disk_bus: Literal["scsi", "pci"] = "scsi"
    convert_to_scsi: bool = True
    firmware: Optional[Literal["bios", "uefi"]] = None
    timezone: Optional[str] = None


class Schedule(BaseModel):
    seed_at: Optional[float] = Field(None, description="epoch seconds; None = start now")
    timezone: str = "Asia/Jakarta"
    resync_minutes: int = Field(60, description="automatic resync interval while waiting for cutover")


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
    actions: List[PlanAction] = Field(default_factory=list)


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
