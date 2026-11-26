# Changed-block tracking (D5)

Status 2026-11-26: engine pass and QEMU bitmap tracker built and measured on a fake source. **Not wired into
plans or the UI, and not run against everRun.** Resync and final sync still use the full pass.

## Why

Hashing already runs on the source host (the helper streams digests, not data) and skips holes in sparse files
(`SEEK_DATA`). So the cost of a resync is reading every allocated chunk on the everRun node, about 110 MiB/s in the
lab. For example `ev2ahv-src-win22` has 14.6 of 60 GiB allocated on its boot disk and 1.05 of 10 GiB on its data
disk (XFS, read with `stat` on node0, 2026-11-26). Downtime grows with the allocated size, not with what changed.

## Options on everRun (8.1.1: libvirt 10.0.0, QEMU 8.2.2, kernel 6.8, raw disk files on XFS)

| Option | How | Verdict |
| --- | --- | --- |
| QEMU dirty bitmaps | `block-dirty-bitmap-add` on the guest's block node; at each pass rotate in one QMP transaction (add N+1, disable N); read N's dirty extents over an NBD export (`qemu:dirty-bitmap:N`); remove N | **Chosen.** Exact, cheap (one QMP call + one block-status query), supported since QEMU 4.x. Needs read-write QMP on the running guest (`virsh qemu-monitor-command`), and the bitmap is lost when QEMU restarts or FT fails over, which forces a full pass |
| Persistent bitmaps / libvirt checkpoints | `virsh checkpoint-create` | Needs qcow2; everRun disks are raw files. Not available |
| dm-era | a device-mapper target under the disk that tracks written blocks per era | The guest disk would have to sit on a dm-era device; everRun owns its storage stack. Not available |
| Hash-only, smarter | hash on the node (done), skip holes (done), larger reads, parallel disks | Already in place; still reads all allocated data |
| In-guest tracking | an agent or filter driver in the guest | Invasive, per-OS. Rejected |

Open risk on everRun: Stratus runs its own QEMU pairing for FT. Adding a bitmap to the active QEMU changes the running
production guest's state (small, but real), and a bitmap on one side of the pair does not follow a failover. Ask
Stratus support before turning this on for customer FT guests; test on `ev2ahv-src-*` first.

## Design

- `ev2ahv/cbt.py`, `QemuBitmap(qmp, node, nbd_socket)`: `start()` before the full pass; `rotate()` returns the frozen
  bitmap; `dirty(frozen)` exports it over NBD and reads extents with `qemu-img map` (QEMU reports dirty extents as
  `"data": false`, 64 KiB granularity); `drop(frozen)` after the pass. Missing bitmap → `BitmapLost`.
- QMP transports: `UnixQmp` (qemu-storage-daemon, tests) and `VirshQmp` (ssh + `virsh qemu-monitor-command`, for
  everRun; not tested).
- `engine.sync_dirty(...)`: reads only the listed chunks (rounded to 4 MiB), compares them with the recorded digests,
  sends what differs, and records the new digests. It refuses to run without a completed full pass.
- Safety: a dirty pass is only valid if tracking ran without a gap since the last full pass started. On `BitmapLost`,
  or any doubt, run `start()` and a full pass. Final sync keeps its verified full pass (source stopped, target hashed)
  until this has lab numbers; replacing only its first source pass already removes one full read from downtime.

## Measured on a fake source (2026-11-26, builder container, QEMU 10.1, `tools/cbt_bench.py`)

Fake guest disk: a fully written raw file served by qemu-storage-daemon; guest writes (64 KiB into N % of the 4 MiB
chunks) go through its NBD export; the engine reads the file with O_DIRECT, as on a node. Same write volume in both
rounds.

| Disk | Dirty | Full resync | Dirty-bitmap resync | Speed-up | Target == source |
| --- | --- | --- | --- | --- | --- |
| 2 GiB | 2 % (10 chunks) | 10.07 s, 512 chunks read | 0.86 s, 10 chunks read (bitmap query 0.025 s) | 11.7x | yes, both |
| 8 GiB | 1 % (20 chunks) | 38.33 s, 2048 chunks read | 1.57 s, 20 chunks read (bitmap query 0.039 s) | 24.4x | yes, both |

The frozen bitmap matched the chunks written exactly in both runs, and did not include a write made after the rotation
(probe run).

## Next steps

1. Lab: on `ev2ahv-src-rocky9` (claim it), check that read-write `virsh qemu-monitor-command` works on everRun and
   that the guest's block node can be found (`query-named-block-nodes`), then run `start`/`rotate`/`dirty` once by hand.
2. Wire it in behind `sync.cbt: qemu-bitmap` (default off): `start()` in seed, `rotate`/`dirty`/`sync_dirty`/`drop` in
   resync, fall back to the full pass on `BitmapLost`.
3. The NBD export must be reachable from where `qemu-img map` runs: on everRun, run it on the node (unix socket via
   `nbd-server-start`), which needs `qemu-img` there (it is present per docs/discovery.md).
