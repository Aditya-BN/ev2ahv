# Changed-block tracking (D5)

Status 2026-11-27: works on real everRun (`ev2ahv-src-win22`, see "On everRun" below). Wired into the resync as a plan
option `sync.cbt: auto|off` (default **off**; not in the UI). Final sync still uses the verified full pass.

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

Open risk on everRun (checked on the lab, see below; still ask Stratus for customer FT guests): Stratus runs its own QEMU pairing for FT. Adding a bitmap to the active QEMU changes the running
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

## On everRun (measured 2026-11-27 on `ev2ahv-src-win22`, Windows Server 2022 HA, everRun 8.1.1)

What the node looks like (read-only queries first):
- The guest runs as `qemu-unity` (Stratus' QEMU fork, reports 6.2.0, not 8.2.2 as assumed above) on node1 (10.12.136.152); the
  partner domain on node0 is `paused`. Libvirt reaches it with `virsh qemu-monitor-command <domain>` as root, read-write.
- Block nodes: one plain `raw` format node over one `file` node per disk (`libvirt-4-format` is the 60 GiB boot disk,
  `libvirt-3-format` the 10 GiB data disk), cache `directsync`. The files sit on node-local LVM + XFS under `/mnt/everrun/`.
  No block job, no mirror, no COLO, no migration (`query-block-jobs` empty, `query-colo-status` mode none,
  `query-block-exports` empty) and no bitmaps before we started. So everRun's replication is not a QEMU block job that a
  bitmap could clash with.
- QMP offers `block-dirty-bitmap-add/remove/enable/disable/merge`, `x-debug-block-dirty-bitmap-sha256`, `nbd-server-start`,
  `block-export-add`, `blockdev-mirror`, `drive-mirror`. qemu-img on the node is present.

What worked, by hand and then through `ev2ahv.cbt` (all in memory, `persistent: false`, nothing on disk):
1. `block-dirty-bitmap-add {node: libvirt-4-format, name: ev2ahv-cbt-0, granularity: 65536}`: returns at once;
   `query-named-block-nodes` shows it `recording`, `persistent: false`.
2. Guest writes (WinRM, 32 MiB of files in C: and F:): the count grows (data disk 34.4 MB for 32 MiB of files).
3. Rotation in one `transaction` (add ev2ahv-cbt-1, disable ev2ahv-cbt-0): 1.0 s over ssh + virsh.
4. `nbd-server-start` on a **unix socket** (`/var/tmp/ev2ahv-cbt-<plan>.sock`), `block-export-add` (type nbd,
   `writable: false`, `bitmaps: [frozen]`), then on the node `qemu-img map --output=json --image-opts
   driver=nbd,...,x-dirty-bitmap=qemu:dirty-bitmap:<frozen>`: 0.05 s; dirty extents (`data: false`) summed to exactly the
   bitmap's `count` (34,406,400 B on the data disk). Then `block-export-del`, `nbd-server-stop` (the socket file goes away).
5. `block-dirty-bitmap-remove` for every ev2ahv bitmap. Afterwards `query-named-block-nodes` shows no bitmaps and
   `query-block-exports` is empty. The guest stayed running and reachable the whole time.
6. The code needed one fix: `block-export-add` does not start a server, so `QemuBitmap(start_server=True)` now runs
   `nbd-server-start` before the export and `nbd-server-stop` after it (always, in a `finally`).

Read cost on the boot disk (node1, `DirectBlockReader` over ssh, hash only, no writes anywhere), after 32 MiB of guest
files plus whatever Windows wrote in the meantime:

| Pass | Time | Chunks read |
| --- | --- | --- |
| Full hash of the 60 GiB boot disk (holes skipped by the helper) | 149.9 s | 15,360 chunks scanned |
| Bitmap: rotate 1.0 s + export and extent query 1.8 s + read 67 dirty chunks (39.4 MB dirty, 268 MiB read) | 7.6 s read + 2.8 s tracker = about 10.4 s | 67 |

About 14x faster end to end (19.6x for the read alone). With the source idle the dirty set is small; a busy guest dirties more.
Not measured: a dirty pass against a real target (the engine's `sync_dirty` is the same code the fake-source runs verified).

Not verified: FT guests (the Rocky FT guest was not touched); a failover or guest restart (the bitmap vanishes; the code
then falls back to a full pass, unit-tested but not forced on everRun); behaviour under a Stratus snapshot or backup;
Stratus support's view. Treat `sync.cbt: auto` as experimental, and keep it off for customer guests.

## Plan option

    source: {type: direct, host: 10.12.136.152, qemu_domain: ev2ahv-src-win22, ...}
    sync:   {cbt: auto}        # auto | off (default off)

`everrun.discover` now writes `qemu_domain`. With `auto`, seed starts tracking (`start()`) before its full pass; each
resync tries rotate, dirty extents, `sync_dirty`, drop. Any error, a missing bitmap, a resumed (interrupted) pass, a verify
pass or an unreachable QMP means a full pass that restarts tracking, so the bitmap is only an accelerator. The per-disk
flag `cbt:<disk>` in the plan state records whether tracking has been gap-free since the last full pass began; it is set to
off before a dirty pass and back to tracking only when it finishes. Final sync is unchanged.

## Next steps

1. Ask Stratus about bitmaps on FT guests, then test on `ev2ahv-src-rocky9` (FT).
2. Force a guest restart on win22 and watch the fallback on real everRun.
3. Run seed and resync through the plan with `sync.cbt: auto` against a real target (writer) and compare the cutover.
4. Expose the option in the UI; consider a replacement of the first pass of the final sync.
