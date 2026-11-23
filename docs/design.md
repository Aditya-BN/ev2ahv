# ev2ahv design

## Flow

    everRun node                 bastion (ev2ahv)                ev2ahv-writer (AHV VM)
    read-only helper in /tmp --> diff, state, control ---------> helper writes staging vdisks
                                       |                                  |
                                  Prism v4 API  --> clone staging disks into the target VM

## Decisions that differ from the original brief

1. **A helper VM is the writer, not the bastion.** A wrong device choice on the bastion could
   destroy the machine running the tool. The owner agreed to this.
2. **Clone before removing the staging disk.** Removing a vdisk from an AHV VM deletes it, so
   "detach, then clone" would lose the data. The order is flush, offline in guest, clone, and
   remove at cleanup.
3. **Hash is negotiated.** xxhash is not in the Python stdlib, and the source helper must be stdlib
   only. Preference is xxh64 if both sides have it, else blake2b, with sha256 as an option. The
   end-to-end test used blake2b.
4. **Staging disks stay until `cleanup`**, so a rollback can retry with a small resync.

## Safety

- `Prism` refuses to modify any VM not named `ev2ahv-*` or the target named in the loaded plan.
- VM disks are removed only through `/vmm/.../vms/{vm}/disks/{disk}`. In the Nutanix MCP tool,
  `deleteDiskById` resolves to `/clustermgmt/.../disks/{id}`, which is **physical disk removal**.
- AHV disk IDs do not appear in the guest serial. A staging device is accepted only if it is new
  since a baseline, exactly the right size, and unmounted. Later it is matched by recorded serial
  and size.
- The helper opens sources `O_RDONLY`, and its `write` command refuses mounted or held devices.
- A plan whose target VM already exists refuses to cut over before the source is stopped.
- The Windows startup task is written to exit unless the machine is a Nutanix VM (not yet run).

## Sync

- 4 MiB chunks. Each pass hashes the source and sends chunks that differ from the recorded digest.
- All-zero chunks are never sent to a fresh disk, and are written only over non-zero data.
- The digest recorded is of the bytes actually read, so a write racing the copy shows up next pass.
- A chunk is committed to SQLite only after the writer fsyncs it. Resume continues from `scan_pos`.
- Final sync hashes the real target too, which also repairs corruption, and repeats until a pass
  sends nothing.
- Source reads use `O_DIRECT` where possible so a running guest's writes are not hidden by page cache.

## Stage A results (2026-11-24 WIB, fake source, 10 GiB image)

| Measure | Result |
|---|---|
| Hot-add / clone / hot-remove | 4.3 s / 2.6 s / 2.6 s |
| Seed, with live writes | 114 s, 1.65 GiB sent, 2137 of 2560 chunks skipped |
| Resyncs | 108 chunks, then 2 chunks |
| Final sync | 189 s (4 chunks sent, second pass clean) |
| Downtime, source stop to SSH on AHV | 307.5 s |
| Data check | 120 of 120 files match, manifest hash identical |
| Rollback | AHV VM off, source running, data intact |

## Known limits

- No changed-block tracking: every resync and final sync reads the entire disk (about 110 MiB/s
  here). Downtime grows with disk size, not with the amount changed.
- The fake source was a mounted filesystem on a loop device, not a guest on everRun. Stage B must
  confirm how everRun exposes its mirrored disks.
- A seed of a running guest is not consistent on its own. Consistency comes from the final sync
  with the source off.
- The raw file to AHV image upload fallback is not built.
