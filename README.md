# ev2ahv

Migrates VMs from Stratus everRun (KVM, 2-node HA/FT) to Nutanix AHV. Seeds while the VM runs,
resyncs only changed chunks, and cuts over with a verified final sync. The source VM is only ever
read, stopped and started. Rollback is powering it back on.

## Status

| Area | State |
|---|---|
| Hot-add, clone from attached disk, hot-remove on AHV | Verified on NTNX-DC |
| Sync engine, resume, zero-skip, corruption repair | Verified, 38 unit tests |
| LocalReader, DirectBlockReader (ssh + loop device), ExportReader (qcow2) | Verified |
| End-to-end: live seed, resync, cutover, boot, checksums, rollback | Verified with a fake source |
| Web console | Verified in Chrome |
| Prism API key auth | Verified |
| Prism username/password auth | Built, never used with valid credentials |
| Linux prep script | Run on Rocky 9 |
| Windows prep script | Run on Windows Server 2022 over WinRM |
| Real everRun Rocky 9 FT guest | Migrated, verified and rolled back. See docs/results.md |
| Real everRun Windows HA guest | Migrated on virtio-blk, verified and rolled back. virtio-scsi boot did not work, so pci is required |
| Guest prep, auto and manual (Move-like) | Tested on both guests. See docs/results.md |
| Raw file to AHV image upload fallback (`target.transfer: image`) | Verified end-to-end, including rollback and a second cutover |

## Quick start

    cd ~/ev2ahv
    python3 -m ev2ahv creds test
    python3 -m ev2ahv plan new plans/my-vm.yaml      # edit it
    python3 -m ev2ahv plan validate plans/my-vm.yaml
    python3 -m ev2ahv prep-check plans/my-vm.yaml
    python3 -m ev2ahv seed plans/my-vm.yaml
    python3 -m ev2ahv resync plans/my-vm.yaml        # repeat as needed
    python3 -m ev2ahv cutover plans/my-vm.yaml
    python3 -m ev2ahv cleanup plans/my-vm.yaml

Plan steps accept `--dry-run`. Run the tests with `python3 -m unittest discover -s tests`.

## Using the UI

A Move-style web UI (environments, migration plans, a 6-step plan wizard, progress, cutover and
rollback) on HTTPS port 8443, backed by a REST API at `/api/v2` (docs at `/api/v2/docs`).

    cd ~/ev2ahv/web && npm ci && npm run build        # once, or after UI changes
    cd ~/ev2ahv/server
    ../.venv/bin/python -m ev2ahv_server --live       # real engine, https://<host>:8443
    ../.venv/bin/python -m ev2ahv_server --mock       # demo data, no lab systems touched

As a service: `cp deploy/ev2ahv.service ~/.config/systemd/user/ && systemctl --user enable --now ev2ahv`.
The certificate is self-signed (`~/.config/ev2ahv/tls/`); replace `server.crt`/`server.key` with your own.
Sign in as `nutanix` (same account file as the old console), then change the password from the user menu.

1. **Add environments.** Source: *Stratus everRun* (system IP, admin user and password, node IPs; disks
   are read over ssh as root with `~/.config/ev2ahv/id_ed25519`), or *Plan files* (a directory of CLI
   plan YAMLs). Target: *Nutanix AOS* (Prism Central with an API key or user/password).
2. **Create a migration plan**: source and target, VMs, network mapping, VM preparation (automatic over
   WinRM/SSH, or manual with a downloadable script), per-VM settings (target name, guest IP for
   automatic prep, disk bus, PCI to SCSI conversion), then *Save and Start* or schedule it.
3. The server runs guest prep, prep-check and the seed, then resyncs every *resync interval* while the
   VM waits in **Ready to Cutover**. Prep-check results show on each VM; a FAIL blocks cutover unless you
   choose *Cutover anyway*.
4. **Cutover** stops the source, runs the final verified sync, creates and starts the AHV VM and checks it.
   **Rollback** powers the AHV VM off and the source back on; a later cutover discards the old copy first.

Each VM's engine log is under *View Log* and in `~/.local/share/ev2ahv/ui/<plan id>/`. Passwords are
write-only in the API, stored apart from plans in the server database (`~/.local/share/ev2ahv/server.db`,
mode 0600) and handed to the engine through a 0600 file or environment variables. Test migration (an
isolated test VM) appears in mock mode only; the engine does not do it yet. The CLI keeps working and
uses the same engine; the UI's engine plans are ordinary plan files you can also run with the CLI.

Lab guard: live mode only migrates `ev2ahv-*` VMs unless `EV2AHV_ALLOW_ANY_SOURCE=1` is set.

The older stdlib console (`python3 -m ev2ahv ui`, http://127.0.0.1:8460) still works but is superseded.

## Prism credentials

Lookup order: `~/.config/ev2ahv/credentials.json`, then `NTNX_PC` + `NTNX_API_KEY`, then the
existing `~/nutanix/.env`. To change them, use Settings in the console, or:

    python3 -m ev2ahv creds set --method api-key
    python3 -m ev2ahv creds set --method password --username admin

New credentials are tested against Prism before they are saved (file mode 0600).

See [docs/runbook.md](docs/runbook.md) and [docs/design.md](docs/design.md).
