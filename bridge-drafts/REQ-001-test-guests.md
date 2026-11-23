# REQ-001: test guests and read-only node access

**Status: DRAFT. Not sent. Needs the owner's OK.**

## What

Precondition: the owner has accepted the EULA and finished portal first-run. Do not accept it on
their behalf.

1. Add this public key to `root` on both everRun nodes, for read-only discovery. It will be removed
   at cleanup: the contents of `~/.config/ev2ahv/id_ed25519.pub`.
2. Create two guests on business network `network0` (VLAN136, gateway 10.12.136.1, DNS 10.12.135.135):

| Guest | Mode | vCPU | RAM | Disks | Static IP | User |
|---|---|---|---|---|---|---|
| `ev2ahv-src-win22` | HA | 2 | 6 GiB | 60 GiB boot + 10 GiB data | 10.12.136.161/24 | Administrator |
| `ev2ahv-src-rocky9` | FT | 2 | 4 GiB | 20 GiB LVM root + 10 GiB data | 10.12.136.162/24 | root |

- Windows: Windows Server 2022 from the media the owner approves, with VirtIO drivers.
- Rocky 9: root on LVM, data disk formatted and mounted by UUID, SSH enabled, the same public key
  added for `root`.
- Do not write any password into a file in `~/lab-bridge`.

## Why

ev2ahv Stage B needs real everRun guests to map disks to block devices, then migrate them to AHV.

## Acceptance check

- `avcli vm-info` shows both guests running, in the requested HA and FT modes.
- From the bastion (10.12.135.197): `10.12.136.161` answers on TCP 3389 and `10.12.136.162` on TCP 22.
  This also proves AHV passes nested guest MAC traffic. If it does not, report that and stop.
- `ssh -i ~/.config/ev2ahv/id_ed25519 root@10.12.136.151 true` works, and the same for `.152`.
- The response lists each guest's MACs, firmware and disk names.
