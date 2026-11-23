# ev2ahv runbook

Times in logs are UTC. Schedule people in WIB (UTC+7).

## 1. Before the window

1. Confirm the writer is up: `ev2ahv-writer`, 10.12.136.211, with free capacity in the target container.
2. In the guest, run the prep script as admin/root:
   - Linux: `prep/prep-linux.sh` (read-only). Exit 0 ready, 1 review warnings, 2 blocked.
   - Windows: `prep-windows.ps1 -Mode Check`, then `-Mode Apply`. This script has not been run yet,
     so try it on a test guest first.
3. Put the results in the plan: firmware, disk bus, MACs, subnets, vCPU and RAM.
   If the guest has no boot-time virtio-scsi driver, use `disk_bus: pci`.
4. `python3 -m ev2ahv prep-check <plan>` must show all PASS.

## 2. Seed and resync (VM keeps running)

    python3 -m ev2ahv seed <plan>
    python3 -m ev2ahv resync <plan>

An interrupted step continues from its last committed chunk when rerun. Resync until the amount
sent is small. Set `source.max_mbps` to protect the source node.

## 3. Cutover (downtime starts)

    python3 -m ev2ahv cutover <plan>

Order: stop source, verified final sync until both sides hash the same, offline staging disks,
create the AHV VM from clones, power on, verify. If it fails, fix the cause and rerun: finished
steps are skipped. Without `source.power` commands, stop the VM yourself and pass
`--source-already-stopped`.

**Plan the window from disk size.** The final sync hashes every chunk on both sides, and repeats
the whole pass if it had to send anything. The 10 GiB test took 3.1 min of final sync and 5.1 min
of total downtime, so a large disk needs a much longer window at this rate.

## 4. After cutover

- Check the app, disks, IP and services.
- Windows: run `prep-windows.ps1 -Mode Post` on AHV to remove the Stratus agent. NGT is optional.
- If the guest uses `bus=pci`, plan the conversion to SCSI.

## 5. Rollback

    python3 -m ev2ahv rollback <plan>

Powers off the AHV VM, then starts the source. Nothing is deleted. **Data written on AHV after
cutover is not copied back.** To try again:

    python3 -m ev2ahv discard-target <plan>
    python3 -m ev2ahv resync <plan>
    python3 -m ev2ahv cutover <plan>

## 6. Cleanup

    python3 -m ev2ahv cleanup <plan>                  # staging disks only
    python3 -m ev2ahv cleanup --delete-target <plan>  # also the migrated VM

The source is never touched. Keep staging disks until you no longer need a quick retry.

## Troubleshooting

| Symptom | Cause and action |
|---|---|
| `cannot identify staging device safely` | Another disk changed on the writer during hot-add. Retry with nothing else attaching |
| `VM ... already exists and holds older data` | Earlier attempt. Run `discard-target` |
| `still differing after 5 verified passes` | The source is still writing. Confirm it is really off |
| `VMM-10000` on create | The real error is in the v3 subtask. ev2ahv includes it in the message |
| Windows `INACCESSIBLE_BOOT_DEVICE` | No boot-start driver for that bus. Roll back, install VirtIO, or use `disk_bus: pci` |
| Data disks offline on Windows | SAN policy. Run `-Mode Apply` before cutover |

**Do not reboot the writer while staging disks are attached** if they could share a filesystem
UUID with its own root disk, as a Rocky cloud image source does.
