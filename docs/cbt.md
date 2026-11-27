# Changed-block tracking (D5)

Status 2026-11-27: works on real everRun, on the HA guest (`ev2ahv-src-win22`, "On everRun" below) and on the FT guest
(`ev2ahv-src-rocky9`, "On an FT guest" below). Wired into the resync as a plan option `sync.cbt: auto|off` (default **off**).
Since 0.3.5 it is a per-VM setting in the appliance too (wizard checkbox, `vm_settings.cbt`, and
`PATCH /api/v2/plans/{id}/vms/{vm}/settings` `{"cbt": "auto"}` for an existing plan). Since 0.3.6 the final sync can use the
bitmap too (section "Final sync" below); `sync.final_verify` chooses how it is checked, default `full`.

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

## On an FT guest (measured 2026-11-27, `ev2ahv-src-rocky9`, Rocky 9 FT, 4 disks, 40 GiB, from appliance-2 0.3.5)

Same picture as the HA guest. Node0 runs the guest (`qemu-unity` 6.2), node1 has the partner domain `paused`; on both,
`query-block-jobs` is empty, `query-colo-status` says mode `none`, `query-migrate` is `{}`, `query-block-exports` is empty
and there are no bitmaps, over plain raw nodes. Nothing at the QEMU level that a bitmap could interfere with, so tracking
was enabled and 8 scheduled resyncs ran with a write load in the guest (table in docs/results.md, "D5 on the FT guest").

| Pass | Wall time | Read | Sent |
| --- | --- | --- | --- |
| Full scan, idle | 72-73 s | 10,240 chunks | 0-348 MB |
| Dirty pass, idle | 26-27 s | 11-17 chunks | 29-54 MB |
| Dirty pass, moderate load (60 files) | 73 s | 354 chunks | 1,418 MB |
| Dirty pass, heavy load | 126 s (full scan under the same load: 140 s) | 697 chunks | 2,793 MB |
| Fallback after the bitmaps were removed by hand | 110 s (full scan, tracking restarted) | 10,240 chunks | 705 MB |

- The gain is the read and hash time: on this small guest 2.7x idle, because each disk still costs 5-6 s for the rotation, the NBD
  export and the extent query. Under heavy load the transfer (about 23 MB/s) dominates and CBT changes little.
- The fallback works on the FT guest: with no bitmap on a disk the engine logs "dirty-bitmap pass failed (... tracking was
  interrupted), full pass instead", completes the pass, restarts tracking, and the next resync is a dirty pass again.
- Cutover after the test: data identical (438 of 438 files in each of two directories), the guest never paused or lost
  connectivity while tracking was on; bitmaps removed afterwards (none, no exports, no socket left on either node).
- **Not tested:** a real FT failover or an everRun migration of the guest. The bitmap exists only in the active node's QEMU, so
  a failover loses it; the fallback above was run by removing the bitmaps over QMP, which is a simulation. The everRun API has
  no per-VM failover for an FT guest (it is a node action, which also moves the other lab guest), so it was left alone.
  Ask Stratus before using this on customer FT guests.

## Final sync (0.3.6)

Problem: of the 439 s downtime on the 40 GiB FT guest, 378 s was the final sync, which is always a full pass (one source-only
pass, then verified passes that hash the target too). The bitmap lives in the guest's QEMU, and everRun's QEMU exits the
moment the guest powers off, so after a normal stop there is nothing left to read.

What everRun does on a stop (observed 2026-11-27 with a 20 ms QMP status poll on both nodes during a cutover of
`ev2ahv-src-rocky9`): the partner QEMU goes paused to running, the guest shuts down, and about 5.5 s after the stop QEMU
exits and the monitor socket resets. There is no `shutdown` or `paused` state in between and so no window to read a bitmap
(libvirt's own `-no-shutdown` window would have shown as a status). A bitmap cannot be persisted (raw disks, see above).

Chosen approach: **halt, pause, read, then power off hard.** With `sync.cbt: auto` and a Linux guest that the appliance
reaches over ssh (`guest.address`), the cutover does not use everRun's stop:

1. Check the guest QEMU is `running` on this node and every disk's bitmap exists and was tracked without a gap since its
   last full pass (`cbt:<disk>` = tracking). Otherwise: normal stop, full final sync (today's behaviour).
2. `systemctl halt --no-block` over ssh. The OS stops its services and unmounts, then halts with the virtual CPU still
   powered, so QEMU stays alive.
3. Wait until the guest answers nothing on ssh and the disks saw no write or flush for 5 s (`query-blockstats`).
4. QMP `stop` (vCPUs paused, I/O drained). From here no write is possible, and the bitmap holds every write up to this
   point: the dirty set is exact and final.
5. In the same pause, rotate each disk's bitmap and read its dirty extents over NBD (about 1 s per disk).
6. everRun `poweroff` (hard; the guest is already halted and paused, so nothing is cut short). Wait for `stopped`.
7. Dirty final pass: read only the dirty chunks, send those that differ from the recorded digests. Then `sync.final_verify`:
   - `full` (default): the existing verified passes (hash source and target, repair, repeat until clean). Saves only the
     first full source pass.
   - `sample`: compare a random 2 percent of the disk (at least 256 chunks) plus every chunk the dirty pass wrote, source
     against target. Any difference runs the full verified passes.
   - `none`: trust the dirty pass. The floor of the downtime, but nothing checks the bitmap.

Failure handling, all ending in the current full final pass: no bitmap, status not `running`, flag not tracking, halt
command refused (the guest is untouched, normal stop), guest not silent within `stop_timeout`, QMP error or rotate error
after the halt (the guest is powered off hard, then the full pass). A halted guest is never left paused with no
plan: the power-off runs in every branch. The dirty list is stored in the plan state before the source stops, so a crashed
cutover resumes the same dirty pass. `sync.final_cbt: off` keeps the old stop path even with `cbt: auto`.

What is and is not guaranteed: the target equals the source's disks at the moment of the pause (QMP `stop` plus an exact
bitmap), and `full` verifies that independently of the bitmap. How clean the guest's filesystems are depends on step 3: a
guest that halted normally unmounted cleanly; one that was still stopping a service when the disks went quiet is left like
a power cut (journal replay on the first boot). Windows guests and sources without ssh keep the old path (there is no
halt without power-off on Windows).

## Plan option

    source: {type: direct, host: 10.12.136.152, qemu_domain: ev2ahv-src-win22, ...}
    sync:   {cbt: auto}        # auto | off (default off)

`everrun.discover` now writes `qemu_domain`. With `auto`, seed starts tracking (`start()`) before its full pass; each
resync tries rotate, dirty extents, `sync_dirty`, drop. Any error, a missing bitmap, a resumed (interrupted) pass, a verify
pass or an unreachable QMP means a full pass that restarts tracking, so the bitmap is only an accelerator. The per-disk
flag `cbt:<disk>` in the plan state records whether tracking has been gap-free since the last full pass began; it is set to
off before a dirty pass and back to tracking only when it finishes. Final sync is unchanged.

## Next steps

1. Ask Stratus about bitmaps on FT guests (the lab FT test is done; a customer FT guest is a different matter).
2. Force a real failover or guest restart (node maintenance in a window where both lab guests may move) and watch the fallback.
3. Done 2026-11-27: seed and resyncs through the plan with `sync.cbt: auto` against the real target (FT guest above); the UI option exists.
4. Apply a changed `cbt` setting while the engine plan is reused (after a rollback, the source is not in discovery, so today the
   environment must be refreshed once first); consider replacing the first pass of the final sync.
