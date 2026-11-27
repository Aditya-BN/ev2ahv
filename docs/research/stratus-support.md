# Stratus everRun: support stance on ev2ahv's host-side approach

Researched 2026-11-28 from public documentation only. Quotes come from WebFetch/WebSearch
extraction of everrundoc.stratus.com pages (a summarizing model, quotes capped near 125 characters).
Re-check wording against the live pages before quoting externally.
Not reachable or not found: the Stratus customer portal (login required), Veeam/Commvault
everRun compatibility pages, any Stratus statement on "voiding support", any everRun-to-Nutanix guide.
Docs vendor: everRun 8.x docs now say "Penguin Solutions" (host OS is Ubuntu there; 7.x is CentOS).

## 1. Summary verdict

| Item | Short answer | Confidence |
|---|---|---|
| (a) Root SSH + third-party script/tools + `virsh -r` | Root SSH to the host is part of Stratus's own documented procedures (e.g. `ftxmnt` for export). Running third-party tools is "allowed" only for management/monitoring tools, with a non-root account recommended and an explicit destabilization warning. Read-only `virsh -r` is not addressed. A `/tmp` helper doing O_DIRECT reads is not covered, so it is neither endorsed nor forbidden. Gray area. | Medium |
| (b) `virsh qemu-monitor-command` (bitmaps, NBD, block-export) | Not supported. Stratus says "everRun users should make no changes to the QEMU virtualization engine", and third-party tools that manage/monitor KVM virtualization are the named exception, with "use the integrated everRun management tools" as the instruction. Changing block state on managed guests is the highest-risk item. | High that it is unsupported in spirit; Medium on exact support consequence (no explicit "voids support" text found) |
| (c) Reading disks during FT mirroring; supported export path | No documented support for reading live disks. Supported paths: console/avcli snapshot -> `vm-snapshot-export` (OVF + VHD/VHDX, to a CIFS/NFS share mounted on the host), VM export (VM must be shut down), and a REST API (HTTPS 8888, OpenAPI served by the system). | High for existence of the paths; Medium for REST coverage of snapshot/export (not verified) |
| (d) Third-party packages/agents on host; migration/backup recommendations | Not forbidden outright: "You can install third-party management tools on everRun systems", but with "potential of destabilizing" and "Follow the recommendation of your authorized Stratus service representative". No "voids support" wording found. No documented migration-off path, no Veeam/Commvault partner list found. Guest-side tools are the clear safe zone. | Medium (absence of evidence on voiding and partners) |
| (e) Host OS, root, SSH policy, customer modifications | Documented: host OS CentOS (7.x) / Ubuntu (8.x); default root credentials documented and must be changed; SSH to the PM management IP is the expected access; do not update the OS from non-Stratus sources; do not change QEMU; sshd hardening guidance exists. No general "customer modifications" support-policy text found publicly. | Medium |

## 2. Evidence

### 2.1 Host OS access, root, SSH
- "Accessing the Host Operating System" (everRun 7.5.1.1):
  https://everrundoc.stratus.com/7.5.1.1/en-us/Content/Help/P01_Users/C01_EES/S05_SysUseRestr/R_UseHostLayer.htm
  - Host OS reachable at the PM console or over SSH using the management IP (not the system IP:
    "Do not connect to the system IP address, as it can move from PM to PM").
  - Default root password is documented; "For security reasons, change the username and password as soon as possible."
  - Warns against updating CentOS "from any source other than Stratus".
- Exporting a VM (7.4.3.2) procedure itself says "Use SSH to log on to the primary node's host OS as root" and run `ftxmnt`:
  https://everrundoc.stratus.com/7.4.3.2/en-us/Content/Help/P01_Users/C08_MngVMs/S02_CreateMigrateVM/T_ExportVMfromEES.htm
  -> root SSH on the host is an expected admin action. It does not imply arbitrary tooling is supported.
- Security Hardening (7.8.0.0): sshd AllowUsers/AllowGroups/DenyUsers, MaxAuthTries, IgnoreRhosts,
  HostbasedAuthentication; change root password on both nodes; "Recovering a lost root password requires
  replacing or reinstalling the nodes" (paraphrase); no PermitRootLogin guidance found:
  https://everrundoc.stratus.com/7.8.0.0/en-us/content/help/P02_Support/C05_Security/T_SecHarden.htm
  -> Practical risk: customer sites may legitimately have restricted or disabled root SSH.

### 2.2 Third-party tools
- "Third-party Management Tools" (7.5.1.1):
  https://everrundoc.stratus.com/7.5.1.1/en-us/Content/Help/P01_Users/C01_EES/S01_eesOver/O_3rdPartyMngTools.htm
  - "You can install third-party management tools on everRun systems."
  - "In general, management tools that run on the host operating system (CentOS) should run on everRun systems."
  - "Possible exceptions are tools that manage/monitor the KVM-based virtualization."
  - "To manage/monitor everRun virtualization, use the integrated everRun management tools."
  - "Stratus recommends that you set up a non-root account for third-party management tools."
  - "Third-party management tools have the potential of destabilizing the environment of the host operating system and system software."
  - "You may need to remove management tools that consume excessive RAM or disk space..."
  - "Follow the recommendation of your authorized Stratus service representative."
  - Tools must be reinstalled when a PM is replaced.
- Same page, 8.0.0.0 (host OS Ubuntu, vendor "Penguin Solutions"), same wording:
  https://everrundoc.stratus.com/8.0.0.0/en-us/Content/Help/P01_Users/C01_EES/S01_eesOver/O_3rdPartyMngTools.htm
- 7.9.1.1 variant exists: https://everrundoc.stratus.com/7.9.1.1/en-us/Content/Help/P01_Users/C01_EES/S01_eesOver/O_3rdPartyMngTools.htm

### 2.3 QEMU
- "QEMU" (7.5.1.1): "everRun users should make no changes to the QEMU virtualization engine"
  https://everrundoc.stratus.com/7.5.1.1/en-us/Content/Help/P01_Users/C01_EES/S05_SysUseRestr/O_QEMU.htm
  (page footer: help created 2026-02-25.) Note the search-tool summary of the first page said this text is on the
  QEMU page, not on the host-layer page.

### 2.4 Snapshots, export, APIs
- Managing Snapshots (7.5.1.1): https://everrundoc.stratus.com/7.5.1.1/en-us/Content/Help/P01_Users/C08_MngVMs/S08_MngSnapshot/O_MngSnapshot.htm
  - "Creating a snapshot results in the conversion of any RAW format volume to QCOW2 format"; "You cannot convert the volumes back to RAW format."
  - "You cannot revert the state of a VM to a snapshot"; snapshots can be exported or turned into a new VM.
  - Snapshots enabled by default, work on running or stopped VM; each snapshot contains only changed data, stored in each volume container.
  - Application-consistent needs the QEMU guest agent (VSS on Windows):
    https://everrundoc.stratus.com/7.6.0.1/en-us/Content/Help/P01_Users/C08_MngVMs/S03_ConfigWindowsVM/T_InstallQEMUWindowsVM.htm
  - Roles: Administrator, Platform Manager, VM Manager. The page does not mention backups.
- `avcli vm-snapshot-export` (7.7.0.0): https://everrundoc.stratus.com/7.7.0.0/en-us/Content/Help/P02_Support/C02_CLI/S02_Commands/S_vm-snapshot-export.htm
  - Syntax: `avcli vm-snapshot-export [--wait][--silent][--volumes volumes] --path pathname [--format format] snapshot`; format `vhd` or `vhdx` (--format added 7.6).
  - "When the OVF file appears in pathname, the export is complete." Target must be a CIFS/NFS share mounted on the host OS.
- `avcli vm-snapshot-create` (7.9.1.1): https://everrundoc.stratus.com/7.9.1.1/en-us/Content/Help/P02_Support/C02_CLI/S02_Commands/S_vm-snapshot-create.htm
  (crash vs application consistency via --desire/--require; `vm-snapshot-create-enable` shows `-H/-u/-p` remote invocation; admin role needed. Snapshot not possible for UEFI-boot VMs per GUI docs.)
- Export VM (7.4.3.2, URL in 2.1): VM must be shut down; "Both PMs of the everRun system must be online";
  "If the everRun system fails over from the primary PM to the secondary PM during export, the process fails"; Windows VMs need Sysprep.
  The 7.6.1.0 equivalent: https://everrundoc.stratus.com/7.6.1.0/en-us/content/help/p01_users/c08_mngvms/S02_CreateMigrateVM/T_ExportVMfromEES.htm
- REST API (8.0.0.0): https://everrundoc.stratus.com/8.0.0.0/en-us/Content/Help/P02_Support/C03_SysRef/S06_RestAPI/T_RestAPI.htm
  - HTTPS port 8888, OpenAPI/Swagger docs served from the system, `/v2/login` returns a token. Whether 7.x has it and whether it
    covers snapshot export: not verified. (The task text mentions "DOH"; I found no everRun doc by that name.)
- Creating and Migrating VMs (7.7.0.0): https://everrundoc.stratus.com/7.7.0.0/en-us/Content/Help/P01_Users/C08_MngVMs/S02_CreateMigrateVM/O_CreateMigrateVM.htm
  covers import of OVF/VHD (incl. from vSphere and Avance) INTO everRun; nothing about exporting to other hypervisors.

### 2.5 Lifecycle / policy
- Stratus Lifecycle Policy: https://www.stratus.com/?p=6684 - Full Support 4 years from GA, then Limited Support, optional Extended Limited Support.
  The page "does not address unsupported configurations, modifications, or everRun" (extraction result).
- 8.0.0.0 release notes: 7.9.x upgrades directly to 8.0.0.0; 7.x->8.x upgrade does not preserve the CentOS system logs:
  https://everrundoc.stratus.com/8.0.0.0/en-us/Content/Help/P02_Support/C01_RelNotes/evr/R_evr_8.0.0.0_RelNotes.htm

### 2.6 Not found
- No Veeam, Commvault, Acronis or other backup vendor compatibility statement for everRun. (Veeam's KVM support found is oVirt/OLVM/Proxmox only; not everRun.)
- No Stratus statement that host modification voids support, and no documented migration-off-everRun guide, P2V/V2V recommendation or partner list.
- No Reddit/community thread found that answers this.
- Stratus support portal articles are behind login; not checked.

## 3. Risks for ev2ahv's current design

Facts vs inference are marked.

1. QEMU monitor bitmaps/NBD/block-export (b). Documented: "make no changes to the QEMU virtualization engine" and use everRun's own
   tools for virtualization. Inferred: adding/removing dirty bitmaps, starting an NBD server or exporting block nodes alters live QEMU
   state on an everRun-managed guest. everRun runs its own block mirroring/sync between PMs (implementation not public); extra
   bitmaps or exports could affect sync, failover or snapshot/export. Failure mode inferred: stalled sync, failed failover, leaked
   bitmaps/exports surviving a migration, support refusing to investigate. Highest risk.
2. Bitmaps require qcow2. Documented: RAW volumes are converted to QCOW2 only when a snapshot is created. Inferred: RAW-backed
   guests cannot carry persistent dirty bitmaps, and everRun snapshot creation changes the disk format irreversibly - so a design
   that relies on bitmaps behaves differently per guest and per snapshot history.
3. Reading disk files directly with O_DIRECT (a, c). Not documented anywhere. Inferred: on a running FT/HA pair a read on one
   node's image is crash-consistent at best and may be torn; reading the secondary's copy during sync is not meaningful. Read-only
   O_DIRECT reads are unlikely to corrupt anything but add I/O load; Stratus warns tools may consume "excessive RAM or disk space".
4. Root SSH and /tmp helper (a, d). Documented: non-root account recommended for third-party tools; root passwords may be
   changed and sshd restricted per site hardening. Inferred: customers' security teams may block root SSH, and `/tmp` scripts are
   unmanaged software on the host (the page says it may be asked to remove them). `virsh -r` itself is read-only libvirt and lowest risk, but
   the page lists libvirt/KVM monitoring tools as the exception for third-party tools.
5. Support outcome (unknown). No public text says support is voided. The documented lever is "Follow the recommendation of your
   authorized Stratus service representative" and the tool is "potential destabilizing". Expect support to ask for removal of tools
   and reproduction without them, especially for QEMU-level changes. Also lifecycle: Limited Support phases reduce root-cause help.
6. Failover during migration. Documented for export/snapshot: failover from primary to secondary fails the operation. A host-side
   tool bound to one node must handle node failover mid-copy.
7. Host differences by version: 7.x CentOS vs 8.x Ubuntu (different libvirt/QEMU versions, tooling paths); the helper must not
   assume `virsh`/QEMU features across both.

## 4. Recommendations

1. Treat the QEMU-monitor/bitmap path as unsupported by default. Make it opt-in (explicit flag, loud warning, requires the operator to
   confirm they have Stratus's written OK), and never the default mode.
2. Add an in-guest agent mode (Veeam Agent / Acronis / rsync-style / a small guest-side CBT tool): fully supported, no host contact
   (everRun guests are ordinary Windows/Linux VMs). Application consistency via the QEMU guest agent/VSS is Stratus-documented.
3. Add a "supported export" mode: `avcli vm-snapshot-create` -> `vm-snapshot-export --format vhd|vhdx` to a share, then convert/import
   into AHV (Nutanix Image Service accepts VHD/VHDX/QCOW2). Works with VM running (snapshot) and gives per-snapshot data; incremental
   is not offered, so use it as full copy then a final shutdown-export or an in-guest delta sync. Verify UEFI VMs (snapshot not
   possible per GUI docs) and Windows Sysprep requirements.
4. Evaluate the REST API (8888, Swagger on the system) for automating snapshot/export where available; verify 7.x coverage on a
   lab system. Run avcli/REST from a management PC with the admin role rather than root-on-host where possible (the `-H/-u/-p` form).
5. If host-side access stays: use a dedicated non-root account, read-only operations only, no files left behind (clean up /tmp
   helper), no libvirt state changes, and a documented removal procedure. Test on a non-production pair first.
6. Ask Stratus/Penguin Solutions support the questions below and keep the written reply with the project.

### Ready-to-send question list
1. Is running a read-only script on an everRun host node over SSH as root (reads of guest volume files with O_DIRECT, plus `virsh -r`) supported under our service agreement?
2. Is `virsh qemu-monitor-command` (block-dirty-bitmap-add/remove, nbd-server-start, block-export-add) on everRun-managed guests supported, and could it impact FT sync, failover or snapshots? Does use of it affect support eligibility?
3. Is it safe to read a guest's disk image on the primary node while FT/HA mirroring is active? Are the images consistent when read while the VM runs?
4. Which export/backup methods are officially supported for a running VM: snapshot + `vm-snapshot-export`, VM export, REST API, partner backup tools? Is there a supported way to get incremental/changed-block data?
5. Does installing third-party packages or agents on the host void or limit support? Which categories are acceptable?
6. Does everRun snapshot creation (RAW -> QCOW2 conversion) have performance or compatibility implications we should plan for? Can it be reversed?
7. What does Stratus recommend for migrating off everRun to another hypervisor (P2V/V2V, export, backups)? Are Veeam, Commvault or others certified?
8. Does the REST API cover snapshot create and export in releases 7.x and 8.x?

## 5. Draft email

Subject: Written confirmation request - supported methods to export VMs from everRun for migration (Case/Contract: <ID>)

Hello Stratus / Penguin Solutions Support,

We are migrating everRun <version, e.g. 7.9.x> virtual machines (<N> VMs, Windows/Linux, <FT/HA>) to another platform, using
a tool that reads guest disk data from the everRun hosts. Before we run it on production systems we need your written position on
the following. All of this is read-only on the data side unless noted.

1. Is it supported to log in to a node's host OS as root over SSH and run a script that reads guest volume files read-only (O_DIRECT)
   and runs `virsh -r`?
2. Is it supported to use `virsh qemu-monitor-command` on everRun-managed guests (block-dirty-bitmap-add/remove, nbd-server-start,
   block-export-add) to track changed blocks? Could this affect FT/HA sync, failover or snapshots, and does it affect our support
   entitlement?
3. Is reading guest disk images while FT/HA mirroring is running safe and consistent?
4. Which methods do you officially support for getting data out of a running VM: snapshot with `avcli vm-snapshot-export`, VM export,
   the REST API, partner backup products? Is there any supported incremental method?
5. Does installing third-party tools or packages on the host affect support? Your documentation recommends a non-root account and
   says such tools may destabilize the host; please confirm the boundaries.
6. What migration path do you recommend off everRun, and which backup vendors do you support?

Please reply in writing so we can attach it to our change record. We can provide logs, `avcli` output and the exact commands.

Thank you,
<Name, company, contract number, phone>

## Appendix: real vs unconfirmed
- Verified from fetched pages: statements quoted in section 2 (via the fetch tool's extraction; 7.5.1.1 and 8.0.0.0 pages, 7.7 CLI page, 7.4.3.2 export page).
- Search-summary only (not fetched directly): snapshot create page details, REST API page, lifecycle statements beyond the policy page.
- Inferred: all of section 3 effects on everRun internals. Unknown: Stratus's actual stance on voiding support.
