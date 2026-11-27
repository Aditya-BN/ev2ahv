# Changed-block tracking (D5)

Status 2026-11-27: works on real everRun, on the HA guest (`ev2ahv-src-win22`, "On everRun" below) and on the FT guest
(`ev2ahv-src-rocky9`, "On an FT guest" below). Wired into the resync as a plan option `sync.cbt: auto|off` (default **off**).
Since 0.3.5 it is a per-VM setting in the appliance too (wizard checkbox, `vm_settings.cbt`, and
`PATCH /api/v2/plans/{id}/vms/{vm}/settings` `{"cbt": "auto"}` for an existing plan). The final sync does not use the bitmap
(tried in 0.3.6 and rejected, see "Final sync" below); `sync.final_verify` (0.3.6) shortens it instead.

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

## Final sync (0.3.6): what was tried, and what shipped

Problem: of the 439 s downtime on the 40 GiB FT guest, 378 s was the final sync. It is a source-only pass (about 75 s) followed by
verified passes that hash the target too (about 300 s: 145 s for the 20 GiB boot disk alone, one round when nothing differs).
The bitmap lives in the guest's QEMU. Could the final sync read only the dirty chunks?

**Answer for everRun: not safely, so the bitmap is not used for the final sync.** What was measured (2026-11-27, FT guest
`ev2ahv-src-rocky9`, appliance-2 0.3.6 builds; the code is in commit 3ae3c57 and was reverted in the next commit):

1. everRun's QEMU does not outlive the guest. A 20 ms QMP status poll on both nodes during a stock stop showed: partner paused
   to running, then about 5.5 s later the monitor socket resets. There is no `shutdown` or `paused` state to catch, so after a
   normal stop there is nothing left to read (and a bitmap cannot be persisted on raw disks).
2. Halting the guest from inside (`systemctl halt --no-block` over ssh, the CPU stays powered so QEMU stays up) does keep QEMU
   alive. Pausing it over QMP `stop` does not stick: everRun resumes it within a second (in the second run `query-status`
   straight after `stop` already said `running`).
3. "The disks were silent for 5 s" is not "the guest has finished". A file-mtime trace on node0 showed the guest's last writes
   to the boot disk came 1.5 to 4.6 s after a 4.6 s silence in the middle of the shutdown (the capture had run in the gap).
   Run 1 shows the effect: the dirty pass missed 2 chunks of `vda`, which only the verified pass found.
4. The disk files' mtime cannot prove "nothing was written after the capture": everRun touches all four files every 3 s on its own.

Results of the two runs that used the bitmap (guest idle, `final_verify: full`):

| Run | Path | Downtime | Final sync | Note |
| --- | --- | --- | --- | --- |
| Stock 0.3.5, cbt off (earlier runs) | stop, source pass, verified passes | 422.1 s and 439.1 s | 364 s and 377.9 s | |
| Stock 0.3.5 with the QMP observers | same | 474.4 s | 403.0 s | stop to QEMU exit 5.5 s |
| 1: halt, pause, bitmap, dirty pass (30+3+3+3 chunks), verified passes | dirty final | 534.5 s | 446.6 s | the dirty set missed 2 `vda` chunks, so the verified pass ran twice (2 x 145 s); data correct |
| 2: same, but the pause did not hold: fell back (hard power-off, full final pass) | fallback | 489.3 s | 399.9 s | the fallback worked; halt plus hard power-off cost 89 s before the final sync against 71 s for a stock stop |

Both ended with the data identical on AHV (438 of 438 files in each directory, same IP and MAC). The bitmap final was slower than
today's final sync in both runs, and a dirty set that is not exact would corrupt the copy under `sample` or `none`. So it was removed
(the design below is what would be needed to bring it back).

What would make it exact: a guarantee that the guest cannot write after the capture. On everRun that would need either a
guest-side hook that signals "halted, all file systems unmounted" (a `system-shutdown` script), or making the disk nodes read-only
in QEMU (`blockdev-reopen`) before the capture; neither is safe to run against everRun's own storage handling without Stratus.

**What shipped instead: `sync.final_verify: full|sample|none`** (per-VM setting `final_verify`, API and wizard column "Final sync check",
default `full`). The final sync is still the exact source-only pass first, which sends everything that differs from the recorded
digests; the setting controls the pass after it, which hashes the target:

- `full`: verified passes (today's behaviour).
- `sample`: compare a random 1 percent of each disk (at least 64 chunks), source against target. Any difference runs the verified
  passes.
- `none`: trust the recorded digests. A digest is recorded only after the target fsynced the chunk, so it is the exact source
  content for every chunk the source pass saw; what it cannot see is a target that changed behind the tool's back.

This is independent of `sync.cbt`. Measured on the FT guest (idle, 40 GiB, appliance-2 0.3.6): `full` 422-474 s downtime (final
sync 364-403 s), `sample` 208.0 s (final sync 152.9 s), `none` 161.0 s (final sync 98.8 s). Details in docs/results.md.

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

## Real FT failover with cbt on (2026-11-27, root)

Rocky FT guest (`ev2ahv-src-rocky9`, plan `app-rocky-stageB`, appliance-2 0.3.8), journaled write load running. Only the two
`ev2ahv-src-*` guests exist on this everRun, so node maintenance moved nothing else.

1. cbt on; first resync = full scan (tracking starts); next resync dirty-only (vda 22 of 5120 chunks, vdd 184 of 1280).
2. `avcli node-workon node0` (issued on node0; on node1 it failed with "Cannot determine the system version"): 14 s later the
   guest ran on node1 and node0 held the paused partner. Ping never dropped.
3. Next resync: for every disk "dirty-bitmap pass failed (no ev2ahv-cbt-* bitmap ...: tracking was interrupted), full pass
   instead"; the full passes sent what the load wrote (vdd 488 chunks).
4. Following resync: dirty-only again on node1 (vda 14, vdd 466 chunks): tracking restarted by itself.
5. `avcli node-workoff node0` (on node1; on node0 it failed with a Java NullPointerException, node1 had become primary).
6. Cutover with `final_verify: full` and the load running: 0 repaired chunks in the verified passes; on AHV 438/438 x2,
   journals 100/100 and 99/100 (the one differing file is the one being written at shutdown).

