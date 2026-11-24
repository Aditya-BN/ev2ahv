# Nutanix Move - UX specification (Stage C1)

## Coverage

- Appliance: `https://10.12.135.181/` (test/dev, owner-approved read-only exploration). Login page title `Login - Move`; post-login title `VM Migration Dashboard - Move`. Brand confirmed as Nutanix Move.
- Version (About Move dialog, screenshot 24): **Move 6.2.0**. Copyright 2026. The Help link points to Move 6.2 docs. A "New Update Available" badge appears in later sessions.
- Screenshots: 74 files in `screenshots/` (numbered in visiting order; a few numbers, e.g. 49, 56, 60, 61, 66-68, are gaps left by failed clicks and have no file).
- Text: 74 matching `text/*.txt` files (visible text, URL, title).
- HAR: `har/move.har`, redacted (see `api-notes.md`).

Screens visited (summary):
- Dashboard (VM): 01, 26-31, 32-38 (Add Environment types), 39-46 (plan summary/details, VM configurations, dashboard filter lists, target VM link), 79-81 (data-size and environment popups).
- Files: 02, 52-54, 57-58 (dashboard, shares/file servers tabs, new plan wizard gate, plan summary, plan details).
- Cluster Conversion: 03, 55, 62, 64 (dashboard, Add Cluster dialog, empty state).
- Security Policy: 04, 63, 65, 69, 70 (dashboard, Add Environment dialog, Migration Plans tab, Policies tab).
- Events: 05.
- Settings and account: 06 (user menu), 07 (settings menu), 08-23 (Appliance Settings and each tab), 09-10 (bandwidth throttling and experience improvement), 12-15 (realtime logs, upgrade dialog, VDDK library, view metrics), 24 (About), 25 (change password dialog).
- Create Migration Plan wizard: 47-51 (name modal gate and step 1 behind it).
- Edit Migration Plan wizard (existing plan): 71-75 (steps 1-3).
- Environment dialogs: 76-78 (view/edit details, vCenter view).
- Bandwidth: 59 (create policy dialog).

Screens NOT reached, and why:
- Create Migration Plan wizard steps 2-6 (Select VMs, Network and Policy, VM Preparation, VM Settings, Summary). The "New Migration Plan" name modal gates the wizard. Its only forward action, **Proceed**, creates the plan record on the appliance, which is a state change. Cancel and Escape close the whole wizard. Step 1 was read from the DOM behind the modal, and the source dropdown was opened read-only. Steps 2-6 are documented from the Edit wizard and the wizard header, not from the Create path.
- Edit Migration Plan wizard steps 4-6. Step 3 uses **Save and Continue**, which is save-equivalent, so it was not pressed.
- Files migration wizard steps 2-4 (Select Shares, Settings, Review Summary). Same Proceed gate as the VM wizard (text 54).
- Cluster Conversion "Create Plan for Cluster Conversion": disabled until a cluster is added.
- Security Policy "Create Policy Migration Plan": disabled until an NSX environment is added.
- Plan action buttons (Retry, Discard, Cutover, Edit Cutover Settings, Cancel, Resume, Pause, Complete): all disabled on the sample plans, and clicking any of them is out of scope.
- Export as CSV (Events), Download Support Bundle, Generate Report (Files), Register Prism Central, Replace Certificate, Update/Remove VDDK, Upgrade Move (Next step), Remove/Validate environment: labels recorded only, not clicked.
- Events list after the final filter session: the list POST was blocked by the safety net, so the Events screen uses the earlier capture (05).

State change check: nothing was saved, created, started, edited, deleted, or cancelled. Dialogs opened for review were closed with Cancel, Close, or Escape. Non-GET requests were blocked by a safety net except login. The only non-GET calls allowed through were read-style `/list` queries.

---

## Global layout (every page)

- **Top bar (dark)**, left to right: logo + product name `Move`; primary nav links `VM`, `Files`, `Cluster Conversion`, `Security Policy` (the active one is underlined). Right side: `Help` (?, external Nutanix portal link), `Settings` (gear, opens a menu), `Events` (link), `nutanix` user menu (`Nutanix user account options`). Later sessions also show `New Update Available` in the top bar.
- **Left panel** (VM dashboard only, 300 px wide): `Environments` header with `+ Add Environment` link, then one card per environment: small tag (`AOS` or `ESXi`), name, type line, address, VM count pill, and a kebab (`⋮`) menu.
- **Main area** (light gray background): status cards row, action row, table.
- Sub-pages (Appliance Settings, Bandwidth Throttling) use a `Back to Dashboard` link at the top left and a `Settings` left sub-nav.

### Settings menu (gear)
Items in order: Appliance Settings, Bandwidth Throttling, Download Support Bundle, Experience Improvement, Prism Central Registration, Realtime Logs, Upgrade Software, Upload VDDK Library, View Metrics.

### User menu (`nutanix`)
Items in order: Events, Change Password, REST API Docs (link), About Move, Logout.

---

## Page 1 - VM dashboard (`/dashboard`, title "VM Migration Dashboard - Move") [01]

Purpose: overview of VM migrations and the list of migration plans.

Layout:
- Left panel: Environments (see global).
- Status cards row (5 cards, each with label, count link, and an icon): `In Progress` (0 VMs), `Ready to Cutover` (0 VMs, with a help icon), `Failed` (1 VMs), `Paused` (0 VMs), `Completed` (1 VMs). Each count is a link to a filtered VM list.
- Action row: `+ New Migration Plan` (primary blue), `Actions` (dropdown, disabled when nothing is selected).
- Plan table header line: `Viewing 4 plans`, search box (`e.g. My Migration Plan`), status filter dropdown (`All Plans`), pager (`1 - 4 of 4`, prev/next arrows).

Table columns, in order:
1. checkbox (select all)
2. Plan name (sortable; link to Plan Summary)
3. Created By
4. Source (link button, `ESXi` - opens source environment popup)
5. Target (link button, `AOS` - opens target environment popup)
6. Data Migrated (link, `Not Available` or size like `24.37 GiB`; opens data-size details)
7. Elapsed Time
8. Plan Status (icon + text: `Not Started`, `In Progress`, `Completed`)
9. VMs Status (three icon counters)
10. Total VMs
11. Actions (per row: `Actions` dropdown and/or `View Details` link)

Sample rows (values from the appliance, for reference only): windows03-32 (Not Started), testingingg (In Progress, with 1 failed VM), Test-win03-32bit (Not Started), Test (Completed).

Row actions menu:
- Not Started plan: `Start`, `Edit`, `Delete`.
- Completed plan: `Delete`.
- In Progress plan: `Actions` disabled, `View Details` shown instead.

Environment card menu (kebab), same items on both cards: Edit Environment Details, View Environment Details, Refresh, Remove, Validate.

Empty state: not seen (4 plans present). The list header still renders `Viewing 0 plans` form.

Transitions:
- Plan name / Plan Summary -> Page 3.
- View Details -> Page 4 (migration progress).
- Source/target buttons -> popups (Page 9).
- Status cards -> Page 6 (VM lists).
- New Migration Plan -> Page 7 (wizard gate).
- Add Environment -> Page 8.

## Page 2 - Files dashboard (`/dashboard`, after clicking `Files`) [02]
Title `Files Migration Dashboard - Move`. Cards: In Progress Shares, Failed Shares, Paused Shares, Ready to Cutover Shares, Completed Shares (6). Actions: `New Migration Plan`, `Generate Report`, `Actions`. Sub-nav: `Migration Plans`, `Shares`, `File Servers` (tabs). Table columns: Plan Name (sortable), Created By (sortable), Source Server (sortable), Target Server (sortable), Total Shares, Data Migrated (sortable), Elapsed Time (sortable), Plan Status (sortable), Shares Status (three counters), Actions (`View Details`, `Actions`). Sample rows: full migration (In Progress), test, test-mirror, test-sync (Completed). Shares and File Servers tabs keep the same route (52, 53).

## Page 3 - Plan Summary (`/plan-details/{id}`) [39, 43]
Title `<name> Plan Details - Move`. Heading = plan name, subtitle "Summary of migration plan. Click on the edit below to make a change."
Sections:
- Source Environment Details: Environment Type (`VMware vCenter`), Name, Source IP, No. of VMs to Migrate.
- Target Environment Details: Cluster, Container.
- Network Mapping: table with Source Network and Target Network (`VLAN 130` -> `VLAN130-IPAM`).
Action: `Edit` (bottom). Clicking it opens the Edit wizard (Page 7b).
Files Plan Summary (57) uses Source File Server Details (name, address), Target File Server Details (name, address), Migration Details (No. of Shares).

## Page 4 - Migration progress (`/migrationprogress/{id}`, full-screen overlay) [40, 41]
Title `VM Migration Progress - Move`. Header: plan name, `?` help, `X` close (back to dashboard).
Action toolbar, in order (state-dependent enable): Retry, Discard, Cutover, Edit Cutover Settings, Cancel, Resume, Pause, Complete. All disabled on the sample plans. Only Cancel/Discard are destructive; do not click on a live plan.
Table header line: `Viewing N VMs`, pager (`1 - N of N`), `Show: 10 rows`.
Columns, in order: checkbox, VM Name (with a `VM Configurations` link underneath), Migrated Data Size (help icon), Migration Status (icon + text: `Failed`, `Completed`), Details (`View Target VM` link on completed VMs; opens the target Prism Element VM page in a new tab).
Right filter panel: `Filters` / `Clear`; a VM-name search (`e.g. VM Apache`); Migration Status checkboxes: All VMs (checked), In Progress, Ready to Cutover, Scheduled for Cutover, Tested VMs, Failed, Paused, Completed.
VM Configurations: opens a modal for that VM [42].

## Page 5 - Dashboard filtered VM lists (`/all-plan-vms/{status}`) [45, 46]
Reached from the status card counts. Shows the same VM table filtered to the chosen status (completed or failed in the samples).

## Page 6 - Events (`/events`) [05]
Title `Events - Move`. Header line: `viewing all N events`, `Export as CSV` (button; not used), pager. Left "Back to Dashboard" link. Filter/clear controls: `Clear`, `Dismiss` (alert). Table columns, in order: Event Name (sortable), User Name, Migration Name (sortable), Share Paths, Status, Start Time (sortable, default sort descending), End Time (sortable), Note(s). Sample event names: `User login`, `Upgrade Move` (Failed, Move appliance upgrade). Dates shown as `Mon D, YYYY, h:mm am/pm`.

## Page 7 - Create Migration Plan wizard (`/migrationplan/SelectTarget`) [47-51]
Full-screen modal wizard (close `X` top right, `?` help, pencil icon for rename on the step line).
Step line (6 steps, numbered): 1 Source & Target, 2 Select VM's, 3 Network and Policy, 4 VM Preparation, 5 VM Settings, 6 Summary.

Gate modal (shown first, over step 1): title `New Migration Plan`; field `Plan Name` (placeholder `Enter Migration Plan Name`, hint `Provide a unique name for your plan`); buttons `Cancel` and `Proceed` (primary; **Proceed creates the plan; not pressed**). Cancel or Escape close the whole wizard and return to the dashboard.

Step 1 - Source & Target (read from the DOM behind the gate; dropdowns opened read-only):
- `Select a Source` (dropdown). Options: `Nutanix` (Prism Central), `vCenter` (VMware vCenter).
- `Select a Target` (dropdown, disabled until a source is chosen). Options: `Nutanix` (Prism Central), `vCenter` (VMware vCenter), as listed by the appliance.
- `Refresh` link (disabled until a target is selected; tooltip "Refresh is disabled until you select a Target Environment"). Confirmation text `Target refreshed`.
- Buttons: `Cancel`, `Next` (disabled until required fields).

Step 2 - Select VM's (documented from the Edit wizard [73]):
- Header: `Source: vCenter`; `Refresh`; `Filter` (opens filter); `Add all VMs` / `Remove all`; tabs `All` and `Warnings`.
- Table, columns in order: Name (sortable, default ascending), Datacenter, Cluster, Host, OS, Power State (On/Off), Source Network. Paged `1 - 10 of 145`, `Show: 10 rows`.
- Buttons: Back, Cancel, Next.

Step 3 - Network and Policy (Edit wizard [74]):
- `Network` section: "Map the source network details to respective target network details". Table: Source Network (e.g. `VLAN 130`) | Target Type (text, placeholder `VLAN`) | Target VPC (dropdown, placeholder `Select VPC`) | Target Subnet (text, placeholder `VLAN130-IPAM`).
- `Test Network (Optional)`: checkbox "Add a test network for crash consistent testing of your VMs prior to migration" with explanation (non-routable, isolated). Not enabled in the sample.
- `Security Policies (Optional)`: text "Nutanix Move supports migrating VM security policies from the NSX environment..." plus "No NSX environment found for the selected source." and "No FNS environment found for the selected target."
- Buttons: Back, Cancel, **Save and Continue** (save-equivalent; not pressed).

Steps 4-6 (not reached; from the wizard header only):
- 4 VM Preparation: guest preparation step (not observed in UI).
- 5 VM Settings: per-VM settings (not observed).
- 6 Summary: review and final create/save (not observed).

Validation messages observed: `Next` disabled until the required field is set (step 1).

Wizard transitions: Cancel -> dashboard. Proceed -> wizard steps (not performed). Next/Back between steps; Save and Continue advances step 3 and persists (not performed).

## Page 7b - Edit Migration Plan (existing plan) [71-75]
Same six-step wizard, opened from the Plan Summary `Edit` button. Step 1 pre-filled: Select a Source `vCenter`, Select a Target `Nutanix`, Target Project (placeholder `None`), Target Cluster (placeholder `NTNX-DC`), Target Container (placeholder `VM_Container`), Security Policy (Optional) panel. Step 2 and step 3 as above. Steps 4-6 not reached (behind Save and Continue).

## Page 8 - Add Environment dialog [28, 32-38]
Opened from `+ Add Environment` (left panel) or the Security Policy `Add Environment` button. Common header `Add Environment`; field `Select Environment Type` (dropdown). Options in order: VMware ESXi, Nutanix AOS, Microsoft Hyper-V, Microsoft Azure, Amazon Web Services. Buttons: `Cancel`, `Add` (not pressed). Fields per type (placeholders in brackets):
- VMware ESXi: vCenter Server, standalone ESXi host, VMC on AWS, or AVS on Azure (`Enter IP Address or FQDN`); Environment Name (`Enter a friendly display name`); User Name (`Enter user name`); Password (`Enter password`). Buttons: Show, Cancel, Add.
- Nutanix AOS: Nutanix Environment (`Enter IP Address or FQDN`); Environment Name; User Name; Password; checkbox `I also want to use this Prism Central for Policy migration`.
- Microsoft Hyper-V: Hyper-V Server/Cluster (`Enter a IP Address:Port or FQDN:Port`); Environment Name; User Name; Password.
- Microsoft Azure: Environment Name; Subscription ID; Tenant ID; Client ID; Client Secret (`Enter Client Secret Key`). Buttons: Create Azure Client ID/Secret, Show, Cancel, Add.
- Amazon Web Services: radio `AWS Public Cloud` / `AWS GovCloud (US)`; Environment Name; AWS Access Key ID (`Enter the AWS credential ID`); AWS Secret Access Key. Buttons: AWS Permission Policy, Show, AWS Context Entries (Optional), Cancel, Add.

Security Policy variant (Add NSX / Prism Central): dialog with `Select Environment Type` only (65).

Edit Environment Details (Nutanix card) [77]: same fields, pre-filled from the saved environment (type `Nutanix AOS`, Nutanix Environment, Environment Name `Nutanix`, User Name, Password blank). Buttons: Show, Cancel, Save (not pressed).

View Environment Details [76, 78]: opens a read-only environment view with a `Filters` panel and a `Show: 10 rows` control (the VM list for that environment). Visual content is unconfirmed beyond the controls listed.

## Page 9 - Popups from the plan table [79-81]
- Data size details (on `Data Migrated`): table `Data Migrated`, `Total Data Size` (and per-VM rows).
- Source environment popup (on `ESXi`): `Source Environment Details` - Environment Type (`VMware vCenter`), Source Name (`vCenter`), IP/FQDN.
- Target environment popup (on `AOS`): `Target Environment Details` - Environment Type (`Prism Central`), Target Name (`Nutanix`), Cluster.
Closed with Escape.

## Page 10 - Cluster Conversion (`/cluster-conversion/dashboard`) [03, 62, 64]
Title `Cluster Conversion Dashboard - Move`. Tabs/sub-nav: `Clusters`. Buttons: `Add Cluster`, `Create Plan for Cluster Conversion` (disabled until a cluster exists). Explanatory panel: "How to Convert Clusters from ESXi to AHV" with steps (add ESXi on Nutanix cluster; provide Prism Element and vCenter access; arrange hosts and VMs by priority; start conversion). Link `Read more about Cluster Conversion`.
Add Cluster dialog fields, in order: User Friendly Cluster Name; Prism Element Virtual IP; Username; Password; vCenter Server IP; Username; Password (Show toggle). Buttons: Show, Add Cluster (not pressed).

## Page 11 - Security Policy (`/security-policy/dashboard`) [04, 63, 69, 70]
Title `Security Policy Dashboard - Move`. Sub-nav tabs: `Migration Plans`, `Policies`. Right/left panel: `Environments` with `Add Environment` (NSX + Prism Central). Button: `Create Policy Migration Plan` (aria-disabled until NSX is added). Explanatory steps: Add NSX server and Prism Central; Add security policies; Migrate seamlessly; `How does it Work?` link.
Policies tab: header `Policies`, actions `Actions`, `Modify filters`, `Viewing N Policies`; columns: NSX Policy (sortable), FNS Policy (sortable), Migration Plan, Secured Entities, FNS Policy Mode (sortable), Status (sortable), Actions. Empty state: `No policies found`.

## Page 12 - Bandwidth Throttling (`/bandwidth-configuration`) [09, 59]
Title `Bandwidth Throttling - Move`. Empty state: "You have no bandwidth throttling policies" with copy "Bandwidth throttling policies help you to define the allowed bandwidth cap at the source level." Button `Create Policy`.
Create Policy dialog fields, in order: Policy Name (`Enter Policy Name`); Select Source (dropdown); Frequency (dropdown, placeholder `Select Schedule`); Bandwidth Limit (number, placeholder `0`). Buttons: Cancel, Next (not pressed).

## Page 13 - Appliance Settings (`/appliance-settings`) [08, 16-23]
Title `Appliance Settings - Move`. Left sub-nav: `General`; `Docker Bridge IP` (`/docker-ip`); `NTP Servers` (`/ntp-servers`); `Snapshot Configuration` (`/snapshot-configuration`); `VDDK Upload` (`/vddk-library-upload`); `Hostname` (`/hostname`); section `Security & Authentication` with `SSL Certificate` (`/ssl-certificate`) and `Prism Central Registration` (`/prism-central-registration`).
- NTP Servers: field `NTP Server Address` (`Enter Hostname/IP Address`), `Add NTP Servers` button, table column `Hostname or IP Address` (sortable).
- SSL Certificate: `Replace Certificate` button.
- VDDK Upload: `Update` and `Remove` buttons (VMware disk library).
- Prism Central Registration: `Register Prism Central` button (not pressed).
Other tabs (General, Docker Bridge IP, Snapshot Configuration, Hostname) show their text content; their form contents are in the text files, not as form fields.

## Page 14 - Settings dialogs (gear menu) [10, 12-15]
- Experience Improvement: checkbox `Participate in Nutanix Customer Experience Program` (id `TelemetryOn`); buttons Cancel, OK.
- Realtime Logs: log viewer (no form fields recorded).
- Upgrade Software: `Upgrade Move Appliance` dialog, status text `Fetching upgrade details...`; buttons Cancel, Next (Next not pressed).
- Upload VDDK Library: redirects to the VDDK upload page (`/appliance-settings/vddk-library-upload`).
- View Metrics: metrics view listing plans (`Viewing N plans`, same table as the dashboard).
- Download Support Bundle: menu item only, not used.

## Page 15 - About Move and account dialogs [24, 25]
- About Move: `Move`, `Version 6.2.0`, "Move is a product of Nutanix, inc.", "Copyright 2026. All rights reserved", OK.
- Change Password: fields `User` (disabled, shows `nutanix`), `Enter Current Password`, `Enter new Password`, `Re-enter new Password` (each with a Show toggle); button `Change Password` (not pressed).
- Logout: menu item only, not used.
- REST API Docs: menu link (not followed).

## Page 16 - Migration plan actions and filters
- Plan-row `Actions` for a Not Started plan [30]: Start, Edit, Delete (not clicked).
- Plan-row `Actions` for a Completed plan [31]: Delete only.
- Top `Actions` (dashboard) [29]: disabled with no selection.
- `All Plans` filter [33]: All Plans, Cancelled, Completed, Failed, In Progress, Not Started, Paused, Scheduled.

## Empty states and validation (observed)
- Bandwidth throttling: "You have no bandwidth throttling policies".
- Security Policy policies: "No policies found".
- Security Policy without NSX: "No NSX environment found for the selected source." / "No FNS environment found for the selected target." with link text "security policy section".
- Cluster Conversion without clusters: Create Plan button disabled.
- Wizard step 1: Next disabled until required fields set; Refresh disabled until a target is selected.

---

## Feature matrix

Context: ev2ahv migrates VMs from Stratus everRun (KVM-based, 2-node HA/FT) to Nutanix AHV. Engine today: environments (everRun mgmt IP + creds as source, Prism Central as target), seed + repeatable resync, cutover, rollback, guest prep (auto or manual), retain IP and MAC, uninstall Stratus agent, prep-check PASS/WARN/FAIL gate, PCI-to-SCSI conversion. Not yet: test migration to an isolated subnet, scheduling, pause/resume, multi-VM plans in the UI.

| Move feature | Applies to Stratus everRun source? (yes / partly / no, with a one-line reason) | ev2ahv equivalent (or "none yet") | Priority (P1 must / P2 should / P3 nice) |
|---|---|---|---|
| Environment registration: source connector (ESXi / vCenter) | partly - the generic "source environment + credentials" model applies, but Move's connector is vCenter-specific | Environments: everRun mgmt IP + creds (source) | P1 |
| Environment registration: Nutanix AOS / Prism Central target | yes - AHV target via Prism Central is the same model | Environments: Prism Central target | P1 |
| Environment types: Hyper-V, Azure, AWS | no - other hypervisors and clouds; everRun is not one of them | none (not applicable) | P3 |
| Environment edit / view / refresh / validate / remove | yes - same lifecycle actions on a registered environment | none yet (environment CRUD in the engine; no UI) | P2 |
| Environment VM count badge | yes - count of VMs visible through the environment | none yet | P3 |
| Migration plan list with status, elapsed time, data migrated | yes - per-VM and per-plan progress is generic | none yet (engine has per-VM state; no list UI) | P1 |
| Multi-VM plan (one plan, many VMs) | yes - Move's core model | none yet (brief lists this as a gap) | P1 |
| Plan wizard: Source & Target selection | yes - choose everRun source and Prism Central target | none yet (engine inputs only) | P1 |
| Plan wizard: Select VMs (filter, add all, remove all, warnings tab) | yes - VM selection is generic; the Warnings tab maps to prep-check WARN | none yet (prep-check exists; selection UI does not) | P1 |
| Network mapping (source network to target subnet/VPC) | yes - AHV subnet mapping needed for every VM | retain IP and MAC (engine); mapping UI none yet | P1 |
| Test network (isolated subnet for test migration) | yes - applies to everRun VMs, but everRun networking must be mapped | none yet (brief lists this as a gap) | P2 |
| Security policies (NSX / FNS) | no - NSX and FNS are VMware and Nutanix Files constructs; everRun has none | none (not applicable) | P3 |
| Target project / cluster / container selection | partly - AHV cluster and container apply; "project" is a Nutanix-side concept that everRun has no equivalent for | none yet | P2 |
| Seed (initial full sync, block-level) | yes - seed is the same concept; method differs (block vs CBT) | seed + repeatable resync (block-level, VM running) | P1 |
| Resync / repeat sync before cutover | yes - Move's repeated sync maps directly | repeatable resync (hash-diff) | P1 |
| Test migration (boot a test copy, Tested VMs status) | partly - needs isolated subnet; ev2ahv does not yet test | none yet | P2 |
| Cutover, with Edit Cutover Settings and Scheduled for Cutover | yes - cutover is the same step | cutover = shutdown, final sync, create AHV VM, power on, verify | P1 |
| Scheduled cutover (Scheduled status and filter) | yes - timed cutover is generic | none yet (brief lists this as a gap) | P2 |
| Pause / Resume of a migration | yes - sync can be paused between passes | none yet (brief lists this as a gap) | P2 |
| Retry / Discard / Cancel / Complete actions | partly - Retry and Complete map to resync/finalize; Discard and Cancel need a clear engine state | none yet as a UI action set | P2 |
| Rollback after cutover | partly - Move does not show a rollback action in the UI; ev2ahv rollback (power AHV off, power source on) is ev2ahv's own addition | rollback (engine) | P1 (ev2ahv differentiator) |
| VM preparation (guest tools/drivers before migration) | yes - guest prep is needed for everRun guests too | guest prep auto (WinRM/SSH) or manual (script + instructions) | P1 |
| VM settings per VM (CPU, memory, disks, NICs) | partly - needs the everRun VM config model; disk-bus conversion applies | PCI-to-SCSI conversion (engine) | P1 |
| Prep-check gating (warnings before start) | yes - Move's Warnings tab is the same idea | prep-check PASS/WARN/FAIL gating cutover | P1 |
| Uninstall source agent after cutover | no - Move does not uninstall anything on the source; the Stratus agent step is ev2ahv-specific | uninstall Stratus agent (engine) | P2 (ev2ahv-specific, no Move equivalent) |
| Migration progress view: per-VM status, migrated data size, filters | yes - same progress model | per-VM state in engine; progress UI none yet | P1 |
| Events / audit log with CSV export | yes - an event log of plan actions is generic | none yet | P2 |
| Bandwidth throttling policy per source | partly - a cap on source-side transfer; everRun link capacity is the constraint | none yet | P3 |
| Support bundle download | yes - supportability is generic | none yet | P3 |
| Realtime logs viewer | yes - generic | none yet | P3 |
| Appliance settings: NTP servers | yes - the ev2ahv host needs time sync | not in ev2ahv scope (host settings) | P3 |
| Appliance settings: hostname, Docker bridge IP | partly - hostname yes; Docker bridge IP is a Move-internal detail | none (not applicable to ev2ahv) | P3 |
| Appliance settings: SSL certificate for the web UI | yes - the ev2ahv web UI needs TLS | none yet | P2 |
| Snapshot configuration | no - Move's snapshot settings relate to VMware snapshot and CBT behavior; everRun has no snapshot-based sync | none (not applicable) | P3 |
| VDDK library upload (VMware disk library) | no - VMware-specific virtual disk library | none (not applicable) | P3 |
| VM Configurations view (per-VM config details) | partly - showing source VM config is useful; the vSphere data model does not apply | none yet | P3 |
| View Target VM link (open target VM in Prism) | yes - link from a migrated VM to its AHV record | none yet | P3 |
| User account: change password | yes - generic login account management | none yet (ev2ahv login model) | P2 |
| User account: logout, REST API docs link | partly - logout yes; REST API docs depend on an ev2ahv API | none yet | P3 |
| About / version | yes - show engine version | none yet | P3 |
| Upgrade the appliance (Upgrade Software wizard, update badge) | partly - self-upgrade of ev2ahv tooling; no Move wizard applies | none yet | P3 |
| Experience Improvement (telemetry opt-in) | no - Nutanix Move telemetry; not part of ev2ahv | none (not applicable) | P3 |
| Cluster Conversion (ESXi-to-AHV cluster conversion) | no - ESXi host conversion; everRun is not ESXi | none (not applicable) | P3 |
| Nutanix Files migration (shares, Sync / Mirror replication) | no - file shares are not VM migration | none (not applicable) | P3 |
| Plan Edit before start | yes - editing a plan before it runs is generic | none yet | P2 |
| Plan Summary page (source/target/network summary before start) | yes - a pre-run summary is generic | none yet (prep-check report only) | P2 |
| Snapshot-based or CBT incremental sync | no - VMware snapshots and CBT are ESXi-specific; ev2ahv uses block-level hash-diff | seed + resync (block-level) | P3 (not needed) |
