# Nutanix Move: cutover settings reference

Research notes on what Nutanix Move's migration plan "cutover" and related settings contain, and how ev2ahv maps to them. Written 2026-11-27.

**Source quality, read this first.** The official Nutanix Move user guide on portal.nutanix.com could not be read. One portal fetch failed with a connection reset (Move 5.1 guide), and the Move 6.0 guide fetch returned only the page title. No official page with an "Edit Cutover Settings" screen or its field list was found. The strongest official material is the Move REST API reference on nutanix.dev, which lists plan and workload fields. Blog, community, and partner material is secondary. Anything that rests only on search snippets is marked as such. Items not confirmed by a source are marked "unconfirmed".

## Sources

| Ref | URL | Version it documents | Read |
|---|---|---|---|
| S1 | https://www.nutanix.dev/api_reference/apis/move.html | Page title "Nutanix Move REST APIs (2.8.0)"; intro says it covers APIs in Move 6.4.0 | Fetched (partial, first 100k chars and the page's later part) |
| S2 | https://next.nutanix.com/product-updates/nutanix-move-6-3-3-45958 | Move 6.3.3 | Fetched |
| S3 | https://next.nutanix.com/product-updates/nutanix-move-6-3-45782 | Move 6.3 | Fetched |
| S4 | https://next.nutanix.com/conversations/42882 ("Basics of MOVE") | Deployment guide cited as Move 4.1 | Fetched (community post, old) |
| S5 | https://www.nutanix.com/blog/migration-plan-testing-with-nutanix-move | none stated | Fetched, nothing relevant on cutover (page only links to Move) |
| S6 | Nutanix Move 5.1 User Guide (portal.nutanix.com, Nutanix-Move-v5_1) | Move 5.1 | Not readable (connection reset) |
| S7 | Nutanix Move 6.0 User Guide (portal.nutanix.com, Nutanix-Move-v6_0) | Move 6.0 | Not readable (title only) |
| S8 | Search snippets: nutanix.com "Supercharge Your VM Migrations", NC2 solution brief, AWS migration guidance, Nutanix community posts | Various, 6.1 and later noted | Snippets only, not fetched |

## Settings table

Field names are from S1 where cited. "Default" comes from S1 sample payloads, not from a stated default, and is labelled as such.

| Setting | What it does | Default | Editable when | Source (URL, Move version) |
|---|---|---|---|---|
| Scheduled cutover time (plan level) | Plan-level schedule held as `Spec.Settings.Schedule.ScheduleAtEpochSec` (epoch seconds). Meaning inferred from the field name, not described in S1. | `-1` in samples (appears to mean not scheduled; unconfirmed) | Unconfirmed. Plan update endpoint exists. | S1, Move 6.4 per intro. S2 says scheduled cutovers have known issues in 6.3.3. |
| Scheduled cutover (per workload) | API has "Schedule cutover actions for multiple workloads across different migration plans" and "Update scheduled cutover actions across multiple migration plans". The per-VM schedule fields are not visible in the fetched text. | Unconfirmed | Unconfirmed | S1 (listed operations). S2 mentions "scheduled cutovers" as a known issue in 6.3.3. |
| Cutover window | Not found in any source. | Unconfirmed | Unconfirmed | None found |
| Cutover run mode (manual vs scheduled) | Community material says cutover can be started manually or set for a scheduled time. A Move blog says starting a plan only starts data copy, and the operator still decides when to cut over. These two statements do not fully agree. | Manual (per S8 blog snippet) | Unconfirmed | S8 (Nutanix blog "Migration Plan Testing" snippet, plus community Ansible post). Version unconfirmed. |
| Source VM power-off at cutover | Move shuts down the source VM and disconnects its vNICs during cutover, then does a final sync. No API flag for source power-off was found; the closest field, `PowerOffForpRDMtovRDMConversion`, applies only to pRDM-to-vRDM conversion. | Source powered off at cutover (per S8 snippets) | Not configurable per any source found | S8 (AWS guidance, community posts). S1 for absence of flag. |
| Source VM kept for rollback | Source VM is kept after cutover so a rollback is possible. Per S8 it is left with a note and its vNIC disconnected. | Kept (per S8) | Unconfirmed | S8 (community post, Move snippet). Version unconfirmed. |
| Source VM renaming | Not found. | Unconfirmed | Unconfirmed | None found |
| Retain IP | Keep the static IP on the target. API: `SkipIpRetention` (per VM, `/prepare`) and query flag `SkipIPRetention`. S3 (6.3) notes IP retention improved for VLAN networks without IP pools. | Unconfirmed in Nutanix sources. Sample payload shows retain enabled (`SkipIpRetention` false is implied, not stated). | Unconfirmed, prepare step | S1 (`/prepare`), S3 (6.3). S8 NC2 brief lists "Retain source-side IP/MAC addresses". |
| Retain MAC | Keep the source MAC on the target. Listed as a configuration option in S8. No explicit MAC flag found in S1. | Unconfirmed | Unconfirmed | S8 (NC2 solution brief, AWS guidance). S1 has no MAC field. |
| Uninstall guest tools (VMware tools) | Removes source guest tools during prep. API: `Workload.VMs[].UninstallGuestTools`, `/prepare` query `UninstallGuestTools`. | `true` in sample payload | Unconfirmed | S1. S3 (6.3): "Remove VMware Tools more cleanly during Windows VM migrations". |
| Install NGT | Installs Nutanix Guest Tools on the migrated VM. API: `Workload.VMs[].InstallNGT`, `/prepare` query `InstallNGT`. | `true` in sample payload | Unconfirmed | S1. S8 (Nutanix blog) calls NGT installation a core Move feature that works with IP retention. |
| Bypass guest operations | Skip in-guest prep (driver install, IP/MAC handling). Not found in any Nutanix source. S1's `GuestPrepMode` ("auto" or "manual") is the nearest match, but the two are not confirmed as the same thing. | Unconfirmed | Unconfirmed | Not found. S1 `GuestPrepMode` (sample `"auto"`) is related, not confirmed equivalent. |
| Guest prep mode | `Workload.VMs[].GuestPrepMode`: `"auto"` in samples; `/prepare` body `Spec.GuestPrepMode`: `"manual"` in samples. | `"auto"` (sample) | Unconfirmed | S1 |
| Test network | Isolated network for test migrations. API: `NetworkMappings[].TestNetworkID` and `TestSecurityGroupIDs`. Plan actions include `test`, `retest`, `undotest`. | Off unless set (ev2ahv default; Move default unconfirmed) | Unconfirmed | S1. S5 blog covers test plans but gives no field detail. |
| Re-seed before cutover (sync) | Data re-sync before the cutover. API has a workload action `sync`. S4 says Move can "schedule data-seeding ... in advance". No separate "re-seed" flag was found. | Unconfirmed | Unconfirmed | S1 (`sync` action), S4 (Move 4.1 deployment guide). |
| VM timezone | Not found in any Nutanix source. | Unconfirmed | Unconfirmed | None found |
| Post-migration scripts | Not found in any Nutanix source. | Unconfirmed | Unconfirmed | None found |
| Cutover actions | Workload action set: `cutover`, `test`, `retest`, `undotest`, `retry`, `discard`, `abort`, `suspend`, `resume`, `sync`, `complete`. | n/a | Per action | S1 |
| Plan update after start | API has "Update migration plan". Whether a started plan can still change, and which fields, is not stated. | Unconfirmed | Unconfirmed | S1. S3 (6.3): plans can complete even when one VM fails during cutover. |
| Cutover retry after target VM left behind | Delete the previously created target VM in Prism before retrying. Resizing alone may not fix a resource-limit failure. | n/a | n/a | S2 (6.3.3) |

## How ev2ahv maps it

Field names are from `/home/adit@ntnx.hin/ev2ahv/server/ev2ahv_server/models.py` (models) and `/home/adit@ntnx.hin/ev2ahv/web/src/pages/Wizard.tsx` (wizard). Engine behaviour was not checked. Only the model and UI were read.

| Setting | ev2ahv status | Where in ev2ahv | What it would take |
|---|---|---|---|
| Scheduled cutover time | Lacks. Only seeding is scheduled. | `Schedule.seed_at` (models.py), "Schedule data seeding for a date and time" (Wizard Step 6). `VmStatus` has "Scheduled for Cutover" but no field sets it. | Add `cutover_at` (and timezone) to `Schedule`, a cutover-time picker to Wizard Step 6, and an engine trigger at that time. Confirm first whether `VmStatus` "Scheduled for Cutover" is already used. |
| Cutover window | Lacks. | None | Needs a source-of-truth decision. Likely a `cutover_window` start/end on `Schedule`. |
| Cutover run mode (manual vs scheduled) | Partly. Cutover is a manual action (`PlanAction` "cutover"). Seeding can be scheduled. | `PlanAction`, `ActionIn`. | Follows from the scheduled cutover row. |
| Source VM power-off | Lacks as a setting. Engine behaviour not checked. | None in models or Wizard. | Decide whether it is always on (matching Move's default) or a per-VM flag in `VmSettings`. The engine must confirm it powers off the source. |
| Source VM kept for rollback | Partly. Plan has `rollback` action and `CutoverResult` is cleared on rollback. Source retention is not a setting. | `PlanAction` "rollback", `PlanVm.cutover_result`. | Add a note, not a setting, unless a source-side behaviour needs configuring. |
| Source VM renaming | Lacks. | None | Would be a per-VM `source_rename` in `VmSettings` if needed. |
| Retain IP | Has. | `Prep.retain_ip` (default `True`), Wizard "Retain static IP addresses". | None. |
| Retain MAC | Has. | `Prep.retain_mac` (default `True`), Wizard "Retain MAC addresses". | None. |
| Uninstall guest tools | Has. Label says "Stratus agent". | `Prep.uninstall_guest_tools` (default `True`), Wizard "Uninstall source guest tools (Stratus agent)". | Relabel if the Move term ("VMware Tools") is wanted. |
| Install NGT | Has. | `Prep.install_ngt` (default `False`), Wizard "Install Nutanix Guest Tools (NGT)". | Consider default. Move's sample payload uses `true`. Unconfirmed as Move's default. |
| Bypass guest operations | Has. | `Prep.bypass_guest_ops` (default `False`), Wizard "Bypass guest operations". | Equivalent to Move's behaviour is unconfirmed. |
| Test network | Has. | `PlanIn.test_network`, `NetworkMap.test_subnet`, Wizard Step 3. | None. ev2ahv requires a test subnet per network when enabled. |
| Re-seed before cutover | Partly. `Schedule.resync_minutes` is the automatic resync interval while waiting for cutover, and `VmSettings.final_verify` checks the final sync. | `Schedule.resync_minutes` (default 60, min 5), `VmSettings.final_verify`, `VmSettings.cbt`. | A manual "re-seed now" action would need a new `PlanAction`. Move's exact behaviour is unconfirmed. |
| VM timezone | Has. | `VmSettings.timezone` (default `None` = same as source), Wizard Step 5 "Timezone". | None. Not confirmed as a Move feature. |
| Post-migration scripts | Lacks. `PrepScripts` covers pre-migration guest prep only. | `PrepScripts` in Wizard Step 4. | Needs a new post-cutover script field on `VmSettings`, and engine support. |
| Plan editable after start | Partly. Edit mode loads the plan and saves via `editPlan`. Wizard has a placeholder for `started_at` that renders nothing. | Wizard `editing`, `editPlan?.started_at` (line 189). | Decide which fields lock after start, then gate them in Wizard and in the API. Move's rules are unconfirmed. |

### ev2ahv items with no Move counterpart found

These are present in ev2ahv but not found in the Move sources above. They are not errors, but they are not Move parity.

- `VmSettings.convert_to_scsi`, `disk_bus` (scsi or pci), `firmware`, `guest_address`, `vcpus`, `memory_mib`, `target_name`.
- `VmSettings.cbt` (experimental CBT resyncs) and `VmSettings.final_verify` (full, sample, none).
- `Prep.virtio_source`.

## Open items

- Get the official Move user guide section on migration plans and cutover from portal.nutanix.com, for the Move version ev2ahv targets (`api-notes.md` says 6.2). Portal access was not possible from this session.
- Check the nutanix.dev Move API schema (OpenAPI, "Download OpenAPI specification") for the per-VM schedule fields, the plan update rules, and the `cutover` action parameters. Only the first part of the page was readable.
- Confirm which "bypass guest operations" behaviour Move actually has. Nothing found.
