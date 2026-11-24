# everRun discovery (B1, partial)

Collected 2026-11-24 03:20 WIB with read-only AVCLI commands from the bastion. Nothing in everRun
was changed.

## System

| Item | Value |
|---|---|
| Version | everRun 8.1.1-285 |
| System IP | 10.12.136.153/24 |
| Nodes | node0 10.12.136.151 (primary), node1 10.12.136.152, both running and normal |
| Portal first-run | Not done (`configured: false`) |
| Capacity | 6 vCPU, 30 GiB RAM for guests |
| Storage | Initial Storage Group, 298.04 GiB, 31.55 GiB used, 512 B sectors |
| Business networks | `network0`, `net_1975` |
| A-Link | `priv0` |
| Guest VMs | None |
| ISO already in everRun | `virtio-win-sra-0.1.262_rev_1` |

## Node platform (read-only, 2026-11-24 07:45 WIB)

- Ubuntu 24.04.4, kernel 6.8, Python 3.12.3. The helper's stdlib needs are all present. xxhash is
  absent, so syncs will use blake2b.
- Storage is one LVM volume group, `everrun` (298 GiB), on each node, with DRBD 8.4 replicating
  between nodes. The system volume is `everrun/shared.fs` under `drbd5`, Primary on node0.
- `virsh -r` works on both nodes and `qemu-img` is present.

## Guest disks (confirmed on `ev2ahv-src-rocky9`, FT, 2026-11-24)

- **Each guest disk is a raw sparse file**, not a DRBD device: an LV per disk per node, formatted XFS and
  mounted under `/mnt/everrun/<volume>_nodeN/`, holding one file. My earlier DRBD guess was wrong.
- **The last 2,048 bytes of the file are everRun metadata** that the guest does not see. A 20 GiB file is
  21,474,834,432 bytes to the guest. ev2ahv copies only the guest-visible bytes (`size_bytes`).
- Bytes read from the file on the running node matched the guest's own view at four sampled 4 MiB offsets.
- For an FT guest, the domain is `running` on one node and `paused` on the other. The running node is the
  source. `ev2ahv discover` refuses unless exactly one node is running the guest.
- Both guests use `pc-i440fx-2.6`, BIOS, virtio disks (`directsync` cache) and a virtio NIC with a
  Stratus MAC (`00:e0:09:...`). AHV accepted that MAC.
- Nested guest traffic does pass through AHV: the bastion reaches the guest directly.

## Guest changes needed before cutover (Rocky 9)

- `/etc/lvm/devices/system.devices` pinned the PV to `/dev/vda2`, which becomes `/dev/sda2` on AHV SCSI.
- The NetworkManager profile was bound to `ens3f0`, and the NIC is named `ens3` on AHV.
- `prep-linux.sh --apply` handles both, reversibly, and both changes also work on everRun.

## Still unknown

- The Windows guest's layout, until its install finishes.

If direct block access proves impossible, the plan switches to `ExportReader`.

## Bridge note

The lab session and this session share the working directory `~/migration-tool`. The `claude -c`
fallback resumes the most recent session in that directory, which could be the wrong one. Use
`claude --resume <session ID from HANDOFF>` only.
