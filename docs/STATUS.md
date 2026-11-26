# ev2ahv resume notes (updated 2026-11-27 WIB)

Tag v0.2.0. Stages A, B, C done; Stage D (appliance) first iteration done. See docs/results.md, docs/appliance.md,
docs/security.md, docs/side-by-side.md.

## Running now (lab), as of the 2026-11-27 overnight run
- `ev2ahv-appliance-3` (a0090f8b-...), https://10.12.135.160/, ON, version 0.3.2, fresh deploy from Prism image `ev2ahv-appliance-0.3.2`;
  UI password ~/.config/ev2ahv/appliance3-ui.pw, admin ~/.config/ev2ahv/appliance3-admin.pw (key as below). Only the AOS environment.
- `ev2ahv-appliance-2` now runs 0.3.2 (upgraded by bundle; backups under /var/lib/ev2ahv/backups). Plan `app-rocky-stageB` is Completed again after T2.
- AHV `ev2ahv-rocky9-app2` (006cf2a0-..., new extId after the T2 re-cutover) is ON at 10.12.136.162; everRun `ev2ahv-src-rocky9` is STOPPED.
- Images: `ev2ahv-appliance-0.3.2` added. `ev2ahv-appliance-test`, `ev2ahv-appl-fake`, `ev2ahv-win22-ahv` were deleted on 2026-11-26.
- Older entries below are from 2026-11-26 and partly outdated (appliance-2 version, rocky9-app2 id).
- Bastion dev servers: mock UI https://<bastion>:8443, live UI :8444 (`server/run-dev.sh mock live`).
- Appliance test VM `ev2ahv-appliance-test` (b10a98c6-...), https://10.12.135.88/ (VLAN135-IPAM, DHCP).
  UI password: ~/.config/ev2ahv/appliance-ui.pw; ssh admin (key ~/.config/ev2ahv/id_ed25519, sudo password
  ~/.config/ev2ahv/appliance-admin.pw). It runs image 0.2.0 build 1 with the 4 validation fixes hot-patched.
- Appliance `ev2ahv-appliance-2` (e26347c2-...), https://10.12.135.154/ (static, VLAN135-IPAM), py312 build from Prism image
  `ev2ahv-appliance-0.2.0-py312`, helper hot-patched (BrokenPipe fix, not in the qcow2 yet). UI password ~/.config/ev2ahv/appliance2-ui.pw,
  admin password ~/.config/ev2ahv/appliance2-admin.pw (ssh key as above). Holds the everRun + Prism environments and plan `app-rocky-stageB`
  (Completed; 4 staging disks still attached for rollback). It has the bastion keypair as its app key (lab shortcut).
- Stage B from the appliance DONE: AHV VM `ev2ahv-rocky9-app2` (8e75066d-..., ON, 10.12.136.162, MAC 00:e0:09:a4:d7:40).
  everRun source `ev2ahv-src-rocky9` is STOPPED (do not start it while the AHV VM runs). The old AHV copy `ev2ahv-rocky9-ui` was deleted.
- dist/ev2ahv-0.2.0.qcow2 (py312, sha256 a13c5b01...b995) is booted and validated on AHV; Prism images: `ev2ahv-appliance-0.2.0` (old build) and
  `ev2ahv-appliance-0.2.0-py312`.
- Build: `bash appliance/build.sh` (no make on the bastion). Docker-based, no sudo needed.

## Open
1. DONE: venv on Python 3.12, pins bumped. pip-audit 0 (was 19); trivy OS 0, venv 1 HIGH (setuptools 70.3.0 CVE-2025-47273, probable
   false positive, venv has 84.2.1); CIS L1 187/69/34 (83.7). Image 293 MiB. Smoke boot (TCG) passed, 0 AVC. New image NOT yet booted on AHV.
2. Deferred by owner: upgrade bundle (UI/CLI, DB migrations w/ backup+rollback), second fresh-deploy test.
3. Not tested: console static-IP/hostname/NTP on AHV, UEFI, OVA, ConfigDrive network, UI clicked through for Stage B, rollback from the appliance on a real guest.
3b. Rebuild the image to include the ev2ahv_helper BrokenPipe fix (and drop the lab key shortcut); IPAM pool 10.12.135.94-95 overlaps other devices.
4. Final cleanup (owner said later): AHV VMs ev2ahv-appliance-test, ev2ahv-appl-fake, ev2ahv-rocky9-app2 (Rocky's
   AHV VM, ON; everRun source is stopped), ev2ahv-appliance-2, image ev2ahv-appliance-0.2.0-py312, ev2ahv-win22-ahv, ev2ahv-writer + staging disks; image
   ev2ahv-appliance-0.2.0; writer: /var/tmp/ev2ahv-uifake*, appliance key in nutanix authorized_keys;
   everRun nodes: ev2ahv ssh key (ask lab session session_012MCA4oeMjsZRVhbBhzuie4).
5. UI gaps: no Prism project list, no Edit Cutover Settings, VLAN-only network step; test migration mock-only.

## Key facts
- everRun power via DOH web API (ev2ahv/everrun_api.py), password "/" escaped as "\/"; no avcli/Java.
- AHV vdisk by-id is scsi-1NUTANIX_<serial>; VM extId == SMBIOS UUID.
- Appliance: nginx 443 -> uvicorn unix socket; app user ev2ahv; root helper ev2ahv-blockd (socket-activated,
  allowlist: AHV vdisk, claimed by ev2ahv, never a disk in use at boot, not mounted/held).
- Secrets Fernet-encrypted with per-instance key (EV2AHV_ETC/instance.key).
