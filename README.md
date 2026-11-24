# ev2ahv

Migrates VMs from Stratus everRun (KVM, 2-node HA/FT) to Nutanix AHV. Seeds while the VM runs,
resyncs only changed chunks, and cuts over with a verified final sync. The source VM is only ever
read, stopped and started. Rollback is powering it back on.

## Status

| Area | State |
|---|---|
| Hot-add, clone from attached disk, hot-remove on AHV | Verified on NTNX-DC |
| Sync engine, resume, zero-skip, corruption repair | Verified, 22 unit tests |
| LocalReader, DirectBlockReader (ssh + loop device), ExportReader (qcow2) | Verified |
| End-to-end: live seed, resync, cutover, boot, checksums, rollback | Verified with a fake source |
| Web console | Verified in Chrome |
| Prism API key auth | Verified |
| Prism username/password auth | Built, never used with valid credentials |
| Linux prep script | Run on Rocky 9 |
| Windows prep script | Not run or parsed. No PowerShell was available |
| Real everRun Rocky 9 FT guest | Migrated, verified and rolled back. See docs/results.md |
| Real everRun Windows HA guest | Pending, still installing |
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

## Web console

    python3 -m ev2ahv ui                             # http://127.0.0.1:8460
    ssh -L 8460:127.0.0.1:8460 <bastion>             # from your workstation

Sign in with the default `nutanix` account, then change the password under Settings. The console
binds to localhost and serves plain HTTP, so reach it through the ssh tunnel.

## Prism credentials

Lookup order: `~/.config/ev2ahv/credentials.json`, then `NTNX_PC` + `NTNX_API_KEY`, then the
existing `~/nutanix/.env`. To change them, use Settings in the console, or:

    python3 -m ev2ahv creds set --method api-key
    python3 -m ev2ahv creds set --method password --username admin

New credentials are tested against Prism before they are saved (file mode 0600).

See [docs/runbook.md](docs/runbook.md) and [docs/design.md](docs/design.md).
