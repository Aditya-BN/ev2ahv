# ev2ahv resume notes (updated 2026-11-26 WIB)

Tag v0.2.0. Stages A, B, C done; Stage D (appliance) first iteration done. See docs/results.md, docs/appliance.md,
docs/security.md, docs/side-by-side.md.

## Running now (lab)
- Bastion dev servers: mock UI https://<bastion>:8443, live UI :8444 (`server/run-dev.sh mock live`).
- Appliance test VM `ev2ahv-appliance-test` (b10a98c6-...), https://10.12.135.88/ (VLAN135-IPAM, DHCP).
  UI password: ~/.config/ev2ahv/appliance-ui.pw; ssh admin (key ~/.config/ev2ahv/id_ed25519, sudo password
  ~/.config/ev2ahv/appliance-admin.pw). It runs image 0.2.0 build 1 with the 4 validation fixes hot-patched.
- dist/ev2ahv-0.2.0.qcow2 = rebuild with the Python 3.12 venv (sha256 a13c5b01...b995), NOT yet booted on AHV.
  Prism image `ev2ahv-appliance-0.2.0` is the OLD build.
- Build: `bash appliance/build.sh` (no make on the bastion). Docker-based, no sudo needed.

## Open
1. DONE: venv on Python 3.12, pins bumped. pip-audit 0 (was 19); trivy OS 0, venv 1 HIGH (setuptools 70.3.0 CVE-2025-47273, probable
   false positive, venv has 84.2.1); CIS L1 187/69/34 (83.7). Image 293 MiB. Smoke boot (TCG) passed, 0 AVC. New image NOT yet booted on AHV.
2. Deferred by owner: upgrade bundle (UI/CLI, DB migrations w/ backup+rollback), second fresh-deploy test.
3. Not tested: console static-IP/hostname/NTP on AHV, UEFI, Stage B from the appliance, OVA, ConfigDrive network.
4. Final cleanup (owner said later): AHV VMs ev2ahv-appliance-test, ev2ahv-appl-fake, ev2ahv-src-rocky9 (Rocky's
   AHV copy, ON; everRun source is stopped), ev2ahv-win22-ahv, ev2ahv-writer + staging disks; image
   ev2ahv-appliance-0.2.0; writer: /var/tmp/ev2ahv-uifake*, appliance key in nutanix authorized_keys;
   everRun nodes: ev2ahv ssh key (ask lab session session_012MCA4oeMjsZRVhbBhzuie4).
5. UI gaps: no Prism project list, no Edit Cutover Settings, VLAN-only network step; test migration mock-only.

## Key facts
- everRun power via DOH web API (ev2ahv/everrun_api.py), password "/" escaped as "\/"; no avcli/Java.
- AHV vdisk by-id is scsi-1NUTANIX_<serial>; VM extId == SMBIOS UUID.
- Appliance: nginx 443 -> uvicorn unix socket; app user ev2ahv; root helper ev2ahv-blockd (socket-activated,
  allowlist: AHV vdisk, claimed by ev2ahv, never a disk in use at boot, not mounted/held).
- Secrets Fernet-encrypted with per-instance key (EV2AHV_ETC/instance.key).
