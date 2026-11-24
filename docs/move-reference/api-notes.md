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
