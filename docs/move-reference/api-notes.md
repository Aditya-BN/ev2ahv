# Nutanix Move (v6.2.0) - observed API notes

Source: `har/move.har` (redacted; 1245 entries across 18 browser sessions, read-only exploration).

Caveats
- The HAR was recorded with `content: 'omit'`. Request bodies and response bodies are NOT in the file, so resource shapes below are inferred from the UI text the endpoints fed, not from raw JSON. Treat field names as unconfirmed until a body capture is made.
- A safety-net route blocked every non-GET request except login and read-style `/list` / `/get` POSTs. Blocked calls appear in the HAR with status 599 and are not server responses.
- Timing values are measured from Playwright timestamps and include browser overhead. They describe UI behavior, not a server contract.

## Auth

| Method | Path | Notes |
|---|---|---|
| POST | `/move/v2/token` | Login from the `/` login page (username + password fields). Response sets session cookies (values redacted). Called once per login. |

## Endpoints observed (API, `/move/v2/...`)

| Method + path | Calls | Status seen | Used by | Notes |
|---|---|---|---|---|
| GET `/move/v2/appinfo` | 193 | 200 | Every page (global poll) | Appliance info; source of the version shown in About. Polled roughly every 3-5 s. |
| GET `/move/v2/checkUpgrade` | 18 | -1 (no response recorded) | Global | Upgrade check; the "New Update Available" badge appears in later sessions. Polled about every 20-45 s. Status -1 means no response was captured, so the upgrade check itself is unconfirmed. |
| GET `/move/v2/supportbundle/status` | 66 | 200 | Global | Support-bundle job state. Polled about every 5-6 s. Used by the settings/support area. |
| GET `/move/v2/iamproviders/view` | 18 | 200 | Global (once per page load) | Identity/auth provider view for the header. |
| POST `/move/v2/providers/list` | 71 | 200 | Dashboard, Environments | Lists environments (source vCenter, target Prism Central). Polled about every 6 s. This is a read, so it is allowed through the safety net. |
| POST `/move/v2/plans/list` | 219 | 200 (1 aborted) | Dashboard, plan lists | Lists migration plans. Query `?IncludeVMDetails=...`. Polled about every 2-3 s while the dashboard is open. |
| GET `/move/v2/plans/{id}` | 5 | 200 | Plan Summary / Edit | Single plan. |
| POST `/move/v2/plans/{id}/workloads/list` | 2 | 200 | Plan details (VM table) | VMs in one plan. |
| POST `/move/v2/providers/{id}/workloads/list` | 3 | 202 | Source VM picker (Edit wizard step 2) | VM inventory from the source environment. 202 means accepted/async. Re-requested about every 50 s. |
| POST `/move/v2/plans/bwcap/policies/list` | 4 | 200 | Bandwidth Throttling page | Bandwidth policies. The page shows "You have no bandwidth throttling policies" (empty). |
| POST `/move/v2/events` | 1 | 599 (blocked by safety net) | Events page | The Events list is loaded through this POST. It was blocked in the final session, so the Events screen in the HAR comes from earlier sessions. |
| GET `/move/v2/events/download/status` | 1 | 200 | Events | Status of an events CSV export. The export button itself was NOT clicked. |

Non-API routes that appear in the HAR (SPA routing, not data): `/dashboard`, `/events`, `/appliance-settings[/...]`, `/bandwidth-configuration`, `/cluster-conversion/dashboard`, `/security-policy/dashboard`, `/files-migration-plan/select-source-target`, `/migrationplan/SelectTarget[/{planId}]`, `/plan-details/{planId}`, `/migrationprogress/{planId}`, `/migration-progress/{planId}`, `/all-plan-vms/{completed|failed}`, `/file-migration-plan/{id}`.

Static assets (JS/CSS/fonts/SVG) were loaded but are not recorded in this file, per the IP rule.

## Polling summary (median repeat interval, UI-side)

| Endpoint | Median repeat | Note |
|---|---|---|
| `/move/v2/plans/list` | ~2-3 s | Fastest; drives the plan table and dashboard counts. |
| `/move/v2/appinfo` | ~3-5 s | Global. |
| `/move/v2/supportbundle/status` | ~5-6 s | Global. |
| `/move/v2/providers/list` | ~6 s | Environment cards. |
| `/move/v2/checkUpgrade` | ~20-45 s | Upgrade badge. |
| `/move/v2/providers/{id}/workloads/list` | ~50 s (one sample) | Only on the wizard VM picker; too few samples to be firm. |

These are observed intervals from a near-idle appliance with 4 plans. They are not a documented contract.

## Status and state enums (from the UI text, not the API payload)

Plan status (plan list "Plan Status" column and the Plan filter): `Not Started`, `In Progress`, `Completed`, `Failed` (VMs-level), `Paused`, `Cancelled`, `Scheduled`.

Filter list on the plan dropdown ("All Plans"): All Plans, Cancelled, Completed, Failed, In Progress, Not Started, Paused, Scheduled.

VM migration status (plan details filter and status column): All VMs, In Progress, Ready to Cutover, Scheduled for Cutover, Tested VMs, Failed, Paused, Completed. A VM row shows `Failed` with a red icon, or `Completed`.

Dashboard counters: In Progress, Ready to Cutover, Failed, Paused, Completed (each shows a VM count and links to a filtered list).

VMs Status counters on each plan row: three numbers with icons (in the same order as the dashboard: in-progress/ready, warnings/failed (red triangle), paused/other (orange diamond)). Exact mapping is unconfirmed.

Plan detail action buttons (all were disabled on the sample plans): Retry, Discard, Cutover, Edit Cutover Settings, Cancel, Resume, Pause, Complete, plus "VM Configurations" (per VM) and "View Target VM" (opens the target Prism Element console in a new tab, `https://10.12.135.195:9440/console/...`).

Plan-row action menus: `Start`, `Edit`, `Delete` (Not Started plan); `Delete` only (Completed plan). The in-progress row's Actions menu is disabled.

Environment card menu (Nutanix and vCenter cards): Edit Environment Details, View Environment Details, Refresh, Remove, Validate.

Environment types (Add Environment dropdown): VMware ESXi, Nutanix AOS, Microsoft Hyper-V, Microsoft Azure, Amazon Web Services.

Cluster Conversion: "Create Plan for Cluster Conversion" is disabled until a cluster is added.

Security Policy: "Create Policy Migration Plan" is aria-disabled until an NSX environment is added.

## Wizard data points (from the Edit wizard for an existing Not Started plan)

- Step 1 "Source & Target" fields: Select a Source (readonly dropdown), Select a Target (readonly dropdown), Target Project (text, placeholder `None`), Target Cluster (text, placeholder `NTNX-DC`), Target Container (text, placeholder `VM_Container`), Security Policy (Optional) panel. Button: Next.
- Step 2 "Select VM's": source inventory table (columns: Name, Datacenter, Cluster, Host, OS, Power State, Source Network), 145 VMs on the vCenter source, paged 10 per page. Controls: Filter, Add all VMs, Remove all, All / Warnings tabs, Back, Cancel, Next.
- Step 3 "Network and Policy": network map (Source Network -> Target Type / Target VPC / Target Subnet), optional Test Network checkbox, optional Security Policies panel. Buttons: Back, Cancel, **Save and Continue** (this is a save-equivalent; not pressed). Steps 4-6 are behind this save and were not reached.

## Create-wizard pass (har/move-wizard.har, bodies embedded and redacted)

Source: `har/move-wizard.har` (178 entries, 12 browser sessions; login body, Authorization/Cookie/Set-Cookie/CSRF headers and any password/token/secret JSON values redacted; static assets and the global polls appinfo/checkUpgrade/supportbundle/iamproviders removed). A Playwright route net aborted any path containing /start, /cutover, /test, /pause, /resume, /retry, /abort, /discard, /complete, /settings, /appliance, /users, /password, /upgrade and any non-list POST under /providers/. Nothing was aborted.

Result: the plan was NEVER created. `POST /move/v2/plans` was sent from step 3 `Next` and answered HTTP 500 (below). The plan-create payload is therefore observed, the plan-update flow and steps 4-6 are not.

### Plan create request (`POST /move/v2/plans`, from wizard step 3 Next)
```json
{"Spec":{"Name":"ev2ahv-uxref",
 "SourceInfo":{"ProviderUUID":"<vCenter provider uuid>"},
 "TargetInfo":{"ProviderUUID":"<Prism Central provider uuid>",
   "AOSProviderAttrs":{"ClusterUUID":"<cluster uuid>","ContainerUUID":"<container uuid>"}},
 "NetworkMappings":[{"SourceNetworkID":"VLAN 135","TargetNetworkID":"<subnet uuid>","TestNetworkID":"","TargetSubnetID":"","TestSubnetID":"","TargetSecurityGroupIDs":[]}],
 "Workload":{"Type":"VM","VMs":[{
   "VMReference":{"UUID":"<vm uuid>","VMID":"vm-31023"},
   "VMCustomizedNicsInfo":{"MetaInfo":{"ConfigMode":"retain","IsConfigured":false}},
   "GuestPrepMode":"auto","RetainMacAddress":true,"SkipIPRetention":false,"UninstallGuestTools":true,
   "SkipCdrom":false,"EnableMemoryOvercommit":false,"VMPriority":"Medium","CategoriesMapping":{},
   "RetainUserData":false,"CreatePublicIpAddress":false,"VMCustomizeType":"static",
   "PowerOffForpRDMtovRDMConversion":true}]},
 "SecurityPolicySettings":{"AddToSecurityPolicy":false},
 "Settings":{"IsConfigPending":false}},
 "IsUpdatePlanFlow":false}
```
Defaults visible in the payload: VM preparation mode `auto`, MAC retained, IP retention on (`SkipIPRetention` false, `VMCustomizeType` static), guest tools uninstall on, CD-ROM kept, priority `Medium`. Test network IDs are empty strings when the Test Network box is off. `IsUpdatePlanFlow` is false for create (true presumably for the Edit flow; not observed).

Error response (HTTP 500): `{"ApiVersion":"2.3.0","Code":20543,"Message":"Connection to source failed: Post \"https://<vcenter fqdn>/sdk\": dial tcp: lookup <fqdn> on 127.0.0.11:53: no such host","State":"Error"}`. The create call therefore validates the source connection (and a UI-side "Checking capacity on the target cluster" spinner precedes it). The name gate `Proceed` makes no API call.

### Source workloads list (`POST /move/v2/providers/{vcenterId}/workloads/list`)
Request: `{"Filter":{"Datacenter":[],"TargetType":"AOS_PC"},"SortBy":"VMName","SortOrderDesc":false}`. Response top-level keys `Entities`, `MetaData`, `Shares`. Entity: `VMName`, `VMUuid`, `VmID` (e.g. `vm-31023`), `InternalUuid`, `SourceUuid`, `RecID`, `DatacenterName`, `ClusterName`, `HostName`, `OSType`, `PowerState` (`PoweredOn`/`PoweredOff`), `NumCPU`, `NumCoresPerSocket`, `MemoryMB`, `NumCDROMs`, `NumEthernetCards`, `StorageAllocatedInBytes`, `StorageUsedInBytes`, `Networks[{ID,Name,Devices[],MacIDs[],IpAddresses}]`, `Nics.NicsInfo[{Mac,NetworkName,IpsConf}]`, `AssociatedPolicies[]`, `AssociatedCategories`, `ErrorMessages`, `WarningMessages[]` (strings shown in the step 2 warning tooltip), `MigrationSupportWarningStatus` (int bitmask, e.g. 2050), and `AdditionalProperties` (strings: `bootfirmware`, `secureboot`, `vtpmenabled`, `vbsenabled`, `guestid`, `targetguestid`, `osfamily`, `esxversion`, `esxbuild`, `annotation`, JSON-in-string `esxvmconfig` and `videocard`).

### Provider list (`POST /move/v2/providers/list`, body `{"EntityType":"VM"}`)
Entity `{MetaData:{UUID,CreationTime,LastUpdateTime,LastInventoryRefreshTime,NumDiscoveredWorkloads}, Spec:{AOSAccessInfo:{IPorFQDN,Username,Password,...}, AOSProperties:{Clusters[{Name,UUID,TargetUUID,Type,Version,IPorFQDN,Containers[],Networks[],Vpcs[],HypervisorTypes[],SupportedCpuModels[],CompatibleTargetTypes[]}], Projects[{Name,UUID,ClusterList,SubnetList,...}], CategoriesMapping{...}, FeaturesStatus[], Users[], Runbooks[], Version}, CompatibleTargetTypes[]}}`. `Password` is returned by the appliance in this response; it is redacted in the HAR. Cluster, container, subnet and project dropdowns of wizard steps 1 and 3 are fed from this object.

### Plan list entity (`POST /move/v2/plans/list`, body `{"EntityType":"VM"}`)
`MetaData`: `UUID`, `Name`, `Owner`, `IamName`, `CreatedTime` (epoch ns), `State`/`StateString`, `Status`/`StatusString`, `Actions[]` (allowed now, e.g. `DELETE`), `AllActions[]` (`START`,`RESUME`,`ABORT`,`DISCARD`,`EDIT`,`DELETE`,`PAUSE`), `NumVMs`, `NumEntities`, `VMStateCounts{Completed:n,...}`, `VMStatus`, `DataInBytes`, `MigratedDataInBytes`, `ElapsedTime{Hours,Minutes}`, `Schedule{ScheduleAtEpochSec,RWStartTimeAtEpochSec,RWEndTimeAtEpochSec}`, `SourceInfo{Name,Type,UUID}`, `TargetInfo{Name,Type,UUID,Cluster}`, `ErrorReasons`, `ValidationWarnings`, `FilesStateCounts`, `FilesStatus`, `SpStateCounts`, `SpStatus`, `AssociatedPlans`. The row Actions menu reads `Actions[]`. Not-started plans: `Actions` includes START/EDIT/DELETE; a Completed plan only `DELETE`.

### Not observed
Plan update payload (`IsUpdatePlanFlow:true`), plan-details/start/delete requests, prep-mode `manual` values, credential fields and the schedule fields of step 5 stayed behind the failed create and the route net; do not infer them from this section.
