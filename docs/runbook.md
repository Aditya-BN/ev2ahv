# ev2ahv runbook

Times in logs are UTC. Schedule people in WIB (UTC+7).

## 1. Before the window

1. Confirm the writer is up: `ev2ahv-writer`, 10.12.136.211, with free capacity in the target container.
2. For an everRun guest, `bin/ev2ahv discover <vm> --nodes <node1>,<node2> --target-name ev2ahv-<vm> --out plans/<vm>.yaml`
   writes the plan: disks, sizes, MACs, firmware, vCPU and RAM.
3. Prepare the guest (section 1b), then `bin/ev2ahv prep-check <plan>` must show no FAIL.

## 1b. Guest prep (Move-like)

Add a `guest` block to the plan. Run everything through `bin/ev2ahv`, which uses the project venv
(pywinrm is needed for Windows):

    guest:
      os: windows                 # or linux
      prep: auto                  # auto | manual | none
      address: 10.12.136.161      # the source guest, reachable from the bastion
      user: Administrator         # root for Linux (ssh key ~/.config/ev2ahv/id_ed25519)
      password_env: EV2AHV_WIN_PASS
      password_file: ~/.config/ev2ahv/win.env   # 0600, KEY=VALUE or a single line
      virtio_iso: ~/ev2ahv-media/fedora/virtio-win.iso   # Windows auto: drivers are copied in over WinRM
      ngt: false

- `bin/ev2ahv guest-prep <plan>`: **auto** uploads and runs the prep script over WinRM or ssh.
  **manual** writes the script and `INSTRUCTIONS.md` under `~/.local/share/ev2ahv/<plan>/guest-prep/`
  and runs nothing. Passwords never go into the plan, logs or state.
- `bin/ev2ahv prep-check <plan>` grades every item PASS, WARN or FAIL. In manual mode it only reads
  `prep.json` from the guest, or from `guest.prep_json_file` if the bastion cannot reach the guest.
- **Cutover refuses to start on any FAIL**, before touching the source. `--force` overrides it.
- After cutover: `bin/ev2ahv guest-cleanup <plan>` removes ghost NICs and Stratus software (Windows),
  or Stratus packages and rebuilds the initramfs (Linux).
- Windows guests that only use virtio-blk get a FAIL for `disk_bus: scsi`. Use `pci`.

## 2. Seed and resync (VM keeps running)

    python3 -m ev2ahv seed <plan>
    python3 -m ev2ahv resync <plan>

An interrupted step continues from its last committed chunk when rerun. Resync until the amount
sent is small. Set `source.max_mbps` to protect the source node.

## 3. Cutover (downtime starts)

    python3 -m ev2ahv cutover <plan>

Order: prep-check gate, stop source, verified final sync until both sides hash the same, offline staging
disks, create the AHV VM from clones, confirm the source is off, power on, verify. The source MAC is kept
by default, so the AHV VM is never powered on while the source might be running. If it fails, fix the
cause and rerun: finished steps are skipped. Without `source.power` commands, stop the VM yourself and pass
`--source-already-stopped`.

**Plan the window from disk size.** The final sync re-reads every chunk on both sides once, at about
110 MiB/s here. Measured: Rocky 30 GiB about 7.5 min downtime, Windows 70 GiB about 20 min. See
docs/results.md.

## 4. After cutover

- Check the app, disks, IP and services.
- Run `bin/ev2ahv guest-cleanup <plan>` (Windows: ghost NICs, Stratus software, optional NGT;
  Linux: Stratus packages, initramfs rebuild).
- If the guest uses `bus=pci`, plan the conversion to SCSI.

## 5. Rollback

    python3 -m ev2ahv rollback <plan>

Powers off the AHV VM, waits until Prism reports it OFF, then starts the source. Nothing is deleted.
**Data written on AHV after cutover is not copied back.** To try again:

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
| Data disks offline on Windows | SAN policy. Run `guest-prep` before cutover. The startup task also brings disks online |

**Do not reboot the writer while staging disks are attached** if they could share a filesystem
UUID with its own root disk, as a Rocky cloud image source does.
