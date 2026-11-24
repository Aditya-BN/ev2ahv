# REQ-002: two extra disks on the Rocky test guest

**Status: DRAFT. Not sent. Needs the owner's OK.**

## What

Add two new 5 GiB virtio disks to `ev2ahv-src-rocky9`, keeping its FT mode. If everRun cannot hot-add
them, a clean shutdown and power-on of the guest is fine. Do not touch the existing disks or any data
on them. Leave the new disks unformatted. The ev2ahv session will partition and format them inside the guest.

## Why

To cover more of the migration matrix. Today only XFS, a single-PV LVM volume and two disks have been
tested. Inside the guest, ev2ahv will build:

- disk 3: partition 1 as plain ext4 (mounted by UUID), partition 2 as an LVM PV;
- disk 4: a whole-disk LVM PV with no partition table;
- one volume group spanning both PVs, with an ext4 logical volume larger than either PV alone.

That covers ext4, a PV without a partition table, and an LV spread across two disks. The guest then
has four disks.

## Acceptance check

- `avcli vm-info ev2ahv-src-rocky9` shows four disk volumes, and the guest is running in FT.
- Inside the guest (`ssh root@10.12.136.162`), `lsblk` shows two new 5 GiB disks with no partitions.
- `/` and `/data` are unchanged, and `/usr/local/sbin/ev2ahv-load check` still reports OK.
