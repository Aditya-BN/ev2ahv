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

**Working hypothesis, to confirm once guests exist:** each guest disk is an LV under its own
`/dev/drbdN`. The reader should use the DRBD device on the Primary node, not the backing LV, because
DRBD 8.4 refuses opens on a Secondary and internal metadata may sit at the end of the LV.

## Still unknown

These need a guest VM:

- Which block device or LV backs each guest disk, and on which node it can be read while running.
- Whether reading the DRBD device on the active node gives a consistent view of the mirror.
- How firmware, disk bus and NIC MAC appear in `virsh` for HA and FT guests.
- Whether AHV passes traffic from nested guest MACs. If it does not, guests cannot reach the bastion.

If direct block access proves impossible, the plan switches to `ExportReader`.

## Bridge note

The lab session and this session share the working directory `~/migration-tool`. The `claude -c`
fallback resumes the most recent session in that directory, which could be the wrong one. Use
`claude --resume <session ID from HANDOFF>` only.
