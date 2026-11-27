# ev2ahv appliance image

A Rocky Linux 9 minimal qcow2 that boots on Nutanix AHV and serves the ev2ahv web app (same idea as the Nutanix Move appliance).
Output: `dist/ev2ahv-<VERSION>.qcow2` (0.3.2: 307,298,304 bytes, sha256 fd0d36b0...5619, build 6 min 16 s; 0.2.0 with the Python 3.12 venv: 307,232,768 bytes = 293 MiB compressed, sha256 a13c5b01...b995; was 271-284 MB) (+ `.sha256`, `MANIFEST.json`, `.sbom.spdx.json`).

## Build

```
make appliance          # appliance/build.sh; needs docker, no sudo, no KVM (~6 min with a warm dnf cache)
make appliance-smoke    # boots build/ev2ahv.raw in qemu TCG inside a container (about 12 min), prints a service/AVC report
make appliance-scan     # trivy, pip-audit, OpenSCAP CIS L1 on build/tree -> build/scan/
```

What `build.sh` does:

1. builds `web/dist` if missing or stale (`npm ci && npm run build`);
2. builds the `ev2ahv-builder` image (rockylinux:9 + python3.12 + dnf, xfsprogs, grub2, checkpolicy, qemu-img);
3. downloads cp312 wheels for `appliance/requirements.txt` with a current python3.12 pip (`--only-binary=:all:`, so no compiler is needed);
4. in a privileged container (`scripts/mkimage.sh`): partitions a sparse 20 GiB raw file (GPT: 1 MiB BIOS boot, 100 MiB ESP, root xfs),
   loop-mounts it, `dnf --installroot` the packages in `appliance/packages.txt` (weak deps off, `nodocs`, `%_install_langs en_US`),
   overlays `appliance/rootfs/`, creates users, builds the venv from the wheels, compiles and installs the SELinux module,
   builds the initramfs, writes the BLS entry/grub.cfg, installs grub for BIOS and shim+grub for UEFI, relabels, cleans caches,
   `fstrim`s and runs `qemu-img convert -c -O qcow2`;
5. writes the checksum, `MANIFEST.json` (version, git commit, UTC date, rpm list, pip list) and an SPDX SBOM (syft on the root tree).

### Why not kiwi-ng / virt-customize

I went straight to `dnf --installroot` + loop devices in a privileged container instead of kiwi-ng or the GenericCloud image + virt-customize.
kiwi needs udev-created partition nodes and its own bootloader handling inside the container, and virt-customize under TCG (no `/dev/kvm`)
would run every `dnf install` inside an emulated appliance VM. The installroot approach is about 120 lines of shell, runs at native speed,
and gives exact control over the package set. Trade-off: it is a custom script, not a declarative description. virt-sysprep/virt-sparsify
are replaced by the explicit cleanup in `mkimage.sh` (machine-id emptied, ssh host keys removed, logs, dnf cache, tmp, bash history) plus `fstrim` on the loop-mounted fs
so zeroed/free blocks are holes before `qemu-img convert -c`.

## What is inside and why

| Item | Why |
| --- | --- |
| kernel-core only (no kernel-modules-extra, no firmware) | all AHV needs (virtio_scsi/blk/net, ahci, xfs, nf_tables) is in kernel-modules-core |
| dracut, generic initramfs with an exclusive driver list | small initramfs (~16 MB), still boots virtio-scsi, SATA/IDE and virtio-blk |
| NetworkManager, firewalld, chrony, openssh, sudo, cloud-init, logrotate, audit, selinux-policy-targeted | standard OS services; cloud-init (datasources ConfigDrive, NoCloud, None) grows the root partition on first boot and accepts AHV custom scripts |
| python3.12 (AppStream) + venv `/opt/ev2ahv/venv` from pinned cp312 wheels (system python3.9 stays for dnf/cloud-init) | no compilers in the image; cryptography uses the abi3 manylinux wheel |
| nginx-core | see below |
| qemu-img, xorriso | used by the app (disk conversion, ISO building) |
| no compilers, -devel, docs/man/info, non-English locales, firmware, kdump, tuned, postfix, plymouth, cockpit, Node, Playwright | size and attack surface; dnf still works |

### Why nginx in front instead of uvicorn TLS

nginx serves the static UI directly, holds port 443 without giving the Python app any capability (the app runs as an unprivileged user with an empty
capability set and listens only on a unix socket), reloads certificates without restarting the app (`ev2ahv-tls.path`) and costs about 2 MB.
There is no port 80 listener.

## Deploy options

| Option | How | Status (lab, 2026-11-28) |
|---|---|---|
| qcow2 image | Prism image upload, then a VM cloned from it (below) | Verified: 0.3.9 on `ev2ahv-appliance-4`, 0.3.10 on the ConfigDrive test VM |
| OVA | `EV2AHV_OVA=1 bash appliance/build.sh` (or `python3 tools/make_ova.py`) writes `dist/ev2ahv-<ver>.ova`: OVF 1.0 (2 vCPU, 4 GiB, 1 NIC, 1 disk of 20 GiB, SCSI), manifest `.mf` and a stream-optimized VMDK, in a tar with the OVF first | Built and checked. The Prism v4 API has `createOva`, but only from a URL the Prism Central must fetch; the lab PC could not reach the bastion, so the OVA was **not imported through the API**. Import it in the PC UI (Compute, VM, OVAs, Upload) or from a web server the PC can reach. The VMDK disk inside the OVA was uploaded as an image and booted on AHV, self-test PASS |
| ConfigDrive / cloud-init (Prism guest customization, "custom script") | Provide user-data with an `ev2ahv:` key (below) | Verified on 0.3.10: static IP, hostname, DNS and NTP applied on first boot and kept after reboot. On 0.3.9 and older the same settings in plain cloud-config are ignored |
| Console menu | `ev2ahv-config` on tty1, or `sudo /usr/local/sbin/ev2ahv-config --console` over ssh | Verified over an ssh pty on 0.3.9 (same functions): static IP, hostname, NTP, DNS; kept after reboot, chrony synced, UI on the new IP. The real VM console (tty1) was not driven |

### Network and hostname from ConfigDrive user-data (0.3.10)

cloud-init network rendering stays disabled and AHV's ConfigDrive carries no `network_data.json`, so `network:` and `hostname:` in user-data have
no effect. Instead, `ev2ahv-cloudinit.service` runs `ev2ahv-config --from-cloud-init` once per cloud-init instance id (marker
`/etc/ev2ahv/.cloudinit-applied`). It reads an optional `ev2ahv:` key and uses the same code as the console menu. Without the key it does nothing.

    #cloud-config
    ev2ahv:
      hostname: ev2ahv-appliance-5
      network: {address: 10.1.2.3/24, gateway: 10.1.2.1, dns: [10.1.2.2]}   # or  dhcp: true
      ntp: [pool.ntp.org]
    users:
      - default
      - {name: admin, lock_passwd: false, ssh_authorized_keys: ["ssh-ed25519 AAAA..."]}

Do not list `admin` under `users:` without `lock_passwd: false`: cloud-init locks the password of a listed user and the forced first-login change then fails.
The same actions exist as flags: `ev2ahv-config --set-network ADDR/PREFIX GATEWAY [DNS...]`, `--set-dhcp`, `--set-hostname NAME`, `--set-ntp SERVER...` (root).
The static address must not be registered in the AHV IPAM of the NIC's subnet for another VM; the NIC itself can keep an IPAM address used only for the first boot.

## Deploy on AHV

1. Prism: Settings, Image Configuration, upload `ev2ahv-<VERSION>.qcow2` as a DISK image (or `acli image.create ev2ahv type=kDiskImage source_url=...`).
2. Create a VM: 2 vCPU / 4 GiB RAM recommended (minimum 2 vCPU / 2 GiB), one NIC, one disk cloned from the image (20 GiB; the root partition and xfs grow to the disk size on first boot if you make it bigger).
   Legacy BIOS and UEFI both boot; SCSI disk bus.
3. Power on. The VM console (tty1) shows the `ev2ahv-config` menu and the web URL once DHCP gives an address.
4. Open `https://<ip>/`, accept the self-signed certificate (compare the fingerprint from console menu item 7).

Do not clone the powered-on appliance after first boot and expect new keys: SSH host keys, the TLS certificate and `/etc/ev2ahv/instance.key` are generated once
(`ev2ahv-firstboot.service`, guarded by `/etc/ev2ahv/.initialized`). Deploy every instance from the pristine image.

## First boot and the console menu

`ev2ahv-config` runs as root on tty1 (`ev2ahv-console.service`; getty@tty1 is masked): status and URL, DHCP/static IPv4 on the primary NIC (nmcli),
hostname, NTP servers (chrony), toggle SSH password authentication, reset the UI admin password (writes `/var/lib/ev2ahv/ui.reset`), show the TLS
fingerprint, regenerate the self-signed certificate (also done automatically after network/hostname changes while the certificate is self-signed),
and "Login shell" (`/bin/login`). After applying network settings it prints `Open https://<ip>/`; `/etc/issue.d/50-ev2ahv.issue` shows the URL on login prompts.
The serial console and ssh get normal login prompts.

cloud-init network rendering is disabled so it cannot overwrite the profile (`ev2ahv-primary`) the menu manages.

## Authorize the appliance SSH key

Each appliance makes its own ed25519 key on first use (`/var/lib/ev2ahv/config/id_ed25519`, owner ev2ahv, 0600; comment
`ev2ahv-appliance`). Nothing else is shipped or shared. ev2ahv uses it to log in as **root** to:

- every everRun node of a source environment: `virsh -r` (read-only discovery), `stat` and reading the guest's raw disk files,
  and the plan's `avcli` power commands to stop the source at cutover and start it on rollback;
- Linux guests whose plan uses automatic prep over SSH (`guest.prep: auto`).

Steps, once per appliance:

1. Open **Health** and find **Appliance SSH key**. The page shows a one-line command with the key, restricted with
   `from="<appliance address>",no-agent-forwarding,no-port-forwarding,no-X11-forwarding`. Check that the address is the
   appliance IP (the page uses the address you opened it with).
2. Run that line as root on **each** everRun node (both nodes of the pair), and in each Linux guest that will use automatic
   prep. It appends to `/root/.ssh/authorized_keys`, so nothing already there is changed.
3. Add (or refresh) the everRun environment in the UI. Validation runs `virsh -r list` on every node and fails, naming the
   node, if a node refuses the key. Node host keys are pinned on first contact (`StrictHostKeyChecking=accept-new`,
   `/var/lib/ev2ahv/config/known_hosts`).

To rotate the key: stop `ev2ahv`, move `id_ed25519` and `id_ed25519.pub` out of `/var/lib/ev2ahv/config/`, start it, open
Health (a new key is made), repeat steps 1 to 3, then delete the old line from `authorized_keys` on the nodes and guests. To
revoke an appliance, delete its line (comment `ev2ahv-appliance`, `from=` its IP) on the nodes and guests.

Do not copy another machine's key into the appliance (the lab shortcut on `ev2ahv-appliance-2`, see below); it ties the
appliance's access to that machine's key and makes revocation per appliance impossible.

## Default credentials (change them)

| Where | User | Password | Note |
| --- | --- | --- | --- |
| Web UI | `nutanix` | `nutanix/4u` | forced change at first login |
| Console / ssh | `admin` (wheel, sudo with password) | `nutanix/4u` | expired (`chage -d 0`): forced change at first login |
| root | | locked | no root ssh login |

## Ports

| Port | Use |
| --- | --- |
| 22/tcp | ssh (firewalld public zone) |
| 443/tcp | https UI and `/api/` (firewalld public zone) |

Everything else is closed. Outbound: Prism Central/Element (9440), everRun, WinRM/ssh to sources as configured in the app.

## Files

| Path | Content |
| --- | --- |
| `/opt/ev2ahv/app` | app code (`ev2ahv`, `server`, `prep`, `VERSION`, `web/dist`), root-owned, read-only to the service |
| `/opt/ev2ahv/venv` | python venv |
| `/var/lib/ev2ahv` (0750 ev2ahv) | state DB, `ui.json`, staging, plans, `config/`, `tls/` |
| `/var/lib/ev2ahv/tls/server.{crt,key}` | nginx certificate (0640 ev2ahv:ev2ahv, nginx is in group ev2ahv); `.selfsigned` marks an auto-generated one |
| `/var/log/ev2ahv` | app logs (logrotate daily, 14 kept) |
| `/etc/ev2ahv` (0750 root:ev2ahv) | `ev2ahv.env`, `instance.key` (0640 root:ev2ahv), `.initialized` |
| `/run/ev2ahv/app.sock` | app unix socket (nginx proxies `/api/` to it) |
| `/run/ev2ahv-blockd.sock`, `/run/ev2ahv-boot/` | root block helper socket, boot baseline |

Services: `ev2ahv`, `ev2ahv-boot` (records boot disks/VM UUID), `ev2ahv-blockd.socket` (+ per-connection `ev2ahv-blockd@.service`, root, socket-activated),
`ev2ahv-firstboot`, `ev2ahv-tls.path/.service`, `ev2ahv-console`, `ev2ahv-issue`, `nginx`, plus chronyd, sshd, firewalld, NetworkManager, auditd, cloud-init.
Journal is persistent, capped at 200 MB.

## Upgrades (app)

An upgrade bundle replaces the app (`/opt/ev2ahv/app`), and the venv when `requirements.txt` changed, and keeps all
state. Build it on a machine with the repo, after `bash appliance/build.sh` (which provides `web/dist` and
`build/wheels`):

    python3 tools/make_bundle.py            # dist/ev2ahv-upgrade-<version>.tar.gz and .sha256

On the appliance (as admin, with sudo):

    sha256sum -c ev2ahv-upgrade-<version>.tar.gz.sha256
    sudo ev2ahv-upgrade apply ev2ahv-upgrade-<version>.tar.gz
    sudo ev2ahv-upgrade status              # installed version and backups
    sudo ev2ahv-upgrade rollback            # back to the previous app, venv and state

`apply` checks every file against the bundle's manifest, refuses a downgrade (`--force` allows it) and refuses to run
while a migration step is running (pause the plan or wait). It then stops `ev2ahv`, saves the app, the venv (when
the requirements change), the state directory (`/var/lib/ev2ahv/.local/share/ev2ahv`: database, engine plans, chunk
records) and `/etc/ev2ahv/ev2ahv.env` under `/var/lib/ev2ahv/backups/<UTC time>-<old version>/`, installs the new
version, starts it and waits for `/api/v2/appinfo` to report the new version. If that fails, it restores everything
and starts the old version. Database schema changes run at startup from `MIGRATIONS` in
`server/ev2ahv_server/store.py` (`PRAGMA user_version`); a newer database than the app knows is refused.
Backups are not pruned; delete old ones by hand.

Tried for real on 2026-11-27 (appliance-2, 0.2.0 -> 0.3.1 in 23 s, 0.3.1 -> 0.3.2 in 2 s; see docs/results.md). From 0.3.3 the image links the tool into `/usr/sbin`, so `sudo ev2ahv-upgrade ...` works; an appliance deployed from 0.3.2 or older gets the link only from the second upgrade on, so until then use `sudo /usr/local/sbin/ev2ahv-upgrade ...` or add it once with `sudo ln -s /usr/local/sbin/ev2ahv-upgrade /usr/sbin/ev2ahv-upgrade`. Since 0.3.3 the health check also reads `/api/v2/plans`, `plans/vms`, `providers`, `events` and `settings` and restores the old version if any is not 200. A 0.3.2 or older tool only checks `/api/v2/appinfo`, so check `/api/v2/plans` yourself after that upgrade.

Appliances older than 0.3.1 do not have `ev2ahv-upgrade` yet: extract it from the bundle and run it with
`sudo python3 ./ev2ahv-upgrade apply <bundle>`; it installs itself to `/usr/local/sbin`.

### Upgrade from the UI and signed bundles (0.3.7, D7)

- **Health > Upgrade** (admins): upload the bundle. The app only writes it into `/var/lib/ev2ahv/upgrade/` with a
  `request.json`; `ev2ahv-upgrade.path` (root) starts `ev2ahv-upgrade apply-request`, which copies the bundle out of
  the app-writable spool without following links, verifies and applies it exactly like the CLI, and writes
  `status.json` for the page. The app restarts during the upgrade; the page shows the result when it is back.
- **Signing**: `python3 tools/make_bundle.py --sign owner.key` adds `manifest.json.sig` (Ed25519 over the manifest).
  Put the matching public key in `/etc/ev2ahv/upgrade-keys/<name>.pub` and from then on the appliance refuses
  unsigned bundles and bundles signed with any other key. With no keys installed, unsigned bundles are accepted.
  Make a key pair with `openssl genpkey -algorithm ed25519 -out owner.key` and
  `openssl pkey -in owner.key -pubout -out owner.pub`; keep `owner.key` off the appliance.
- **Older appliances**: the first bundle must be applied with the bundled tool (`sudo ev2ahv-upgrade apply`, which
  installs the new tool); applying it once more, or any later bundle, installs the UI upgrade units and the spool.

## OS updates

Online: `sudo dnf update` (Rocky BaseOS/AppStream repos stay configured); reboot if the kernel changed. The venv/app are not managed by dnf; upgrade the appliance
by deploying a new image and migrating the state (`/var/lib/ev2ahv`, `/etc/ev2ahv/instance.key`) or by the in-app update mechanism when it exists.

Offline: put a repo mirror or the Rocky DVD contents on a transfer disk/ISO, mount it (for example `/mnt/repo`) and run
`sudo dnf --disablerepo='*' --repofrompath=local,file:///mnt/repo/BaseOS --repofrompath=localapp,file:///mnt/repo/AppStream --enablerepo=local --enablerepo=localapp --nogpgcheck update`
(drop `--nogpgcheck` after importing the Rocky key with `rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-9`; the key is already installed).
A tarball of `reposync` output works the same way.

## Security model summary

Details and scan results: `docs/security.md`.

- SELinux enforcing with a small custom module (`appliance/selinux/ev2ahv.te/.fc`): nginx may connect to the app socket, static content, cert and socket labels.
- The web app runs as `ev2ahv` with no capabilities, `NoNewPrivileges`, `ProtectSystem=strict`, private tmp, restricted address families.
  Raw disk access goes through `ev2ahv-blockd` (socket-activated, root, reachable only by group `ev2ahv`).
- TLS 1.2/1.3 only, HSTS, CSP, DENY framing; no port 80.
- SSH: no root login, password auth configurable from the console; root password locked; firewall allows only ssh and https.
- Every instance generates its own SSH host keys, TLS key and instance key at first boot.

## Validation on AHV (2026-11-26, NTNX-DC, image 0.2.0)

| Step | Result |
|---|---|
| Upload qcow2 as image `ev2ahv-appliance-0.2.0`, create `ev2ahv-appliance-test` (2 vCPU, 4 GiB, 20 GiB, BIOS, VLAN135-IPAM) | OK |
| First boot | 16.6 s to multi-user (4.4 kernel + 3.5 initrd + 8.6 userspace); no failed units; SELinux Enforcing; console menu on tty1 shows the URL |
| UI login with the default account | `must_change: true`; every other API call returns 403 until the password is changed; change OK |
| Add Prism Central environment (API key) | Connected |
| Health page | VM UUID found in Prism (`ev2ahv-appliance-test`), self-signed cert generated at first boot, appliance SSH key generated |
| Disk hot-add self-test | PASS: 1 GiB disk hot-added to itself, root helper accepted it and read its size, offlined, removed via Prism |
| Root helper negative tests | `/dev/sda` (system disk), `/dev/sr0`, `/etc/shadow` refused; the app user cannot open raw disks directly |
| Stage A from the appliance (fake source on the lab writer VM, plan-file environment) | prep-check, seed, cutover OK (~1 min); rollback OK; re-cutover OK |
| Reboot during a cutover | back in ~21 s; the interrupted cutover resumed automatically and completed |
| Resources | ~460 MiB RAM used idle (app ~60 MiB, nginx ~17 MiB); 813 MiB of the 20 GiB disk used |

Found and fixed during validation (in the rebuilt image): the boot-baseline directory was not readable by the
app; AHV names vdisks `scsi-1NUTANIX_<serial>` (the code expected `scsi-SNUTANIX_VDISK_*`), so claims and
stable paths now use the real serial; staging disks attached across a reboot are accepted when ev2ahv claimed
them, while any disk in use at boot stays refused; a job interrupted by a service stop now resumes instead of failing.

Sizing: **recommended 2 vCPU, 4 GiB RAM, 20 GiB disk; minimum 2 vCPU, 2 GiB.** Each running migration step adds one
engine process (~60–100 MiB). The appliance only stores state and sparse staging metadata; staging disks are
separate vdisks hot-added per migration.

Not exercised on AHV: the console menu's static-IP / hostname / NTP actions (the test VM used DHCP from an
IPAM subnet and cloud-init for the admin SSH key), UEFI boot, the upgrade bundle and a second fresh deploy
(deferred by the owner), Stage B (everRun) from the appliance.

## Validation of the Python 3.12 build on AHV (2026-11-26, `ev2ahv-appliance-2`)

`dist/ev2ahv-0.2.0.qcow2` (sha256 a13c5b01...b995) uploaded as Prism image `ev2ahv-appliance-0.2.0-py312`; VM `ev2ahv-appliance-2`
(2 vCPU, 4 GiB, 20 GiB, BIOS, VLAN135-IPAM), copied from `ev2ahv-appliance-test`.

| Step | Result |
|---|---|
| Boot | 18.5 s to multi-user (4.1 kernel + 3.5 initrd + 10.9 userspace); `systemctl --failed` empty, system state running; SELinux Enforcing; 0 AVC denials since boot; venv Python 3.12.16 |
| HTTPS UI | up (nginx 502 for ~20 s while the app starts, then 200) |
| Forced password change | default `nutanix`/`nutanix/4u` login gives `must_change`, other API calls 403 until changed; new password saved to `~/.config/ev2ahv/appliance2-ui.pw` (0600) |
| Console admin | `admin` forced password change over ssh worked (new password in `~/.config/ev2ahv/appliance2-admin.pw`, 0600); the bastion public key was added to admin's authorized_keys |
| Prism environment (API key) | Connected, 91 VMs visible |
| `/api/v2/health` | shows its own VM `ev2ahv-appliance-2` |
| `POST /api/v2/health/selftest` | PASS (1 GiB disk hot-added, root helper read it, offlined, removed), also after the helper patch below |
| Idle memory | about 475 MiB used of 3.8 GiB |
| Stage B (everRun) from this appliance | works, see results.md "Stage B from the appliance" |

Notes:
- The first VM was created with a DHCP address from the IPAM pool (10.12.135.94). That address is in use by another
  device on the lab VLAN (the pool overlaps non-IPAM hosts), so the VM answered nothing useful; it was deleted and recreated with the free
  static address 10.12.135.154 on the same subnet. The image is not at fault.
- Lab shortcut: the bastion's ssh keypair was installed as the app key (`/var/lib/ev2ahv/config/id_ed25519`, owner ev2ahv, 0600) so the
  appliance could use the key already authorized on the everRun nodes and in the guest. Production: authorize the key shown on the Health page instead (see Authorize the appliance SSH key).
- Bug found: failed `ev2ahv-blockd@` instances after each write (BrokenPipeError on the last "ok" line). Fixed in the repo,
  hot-patched here; needs an image rebuild to ship.

## Validation of 0.3.2 on AHV (2026-11-27, `ev2ahv-appliance-3`)

Fresh deploy (T1) from Prism image `ev2ahv-appliance-0.3.2`: VM `ev2ahv-appliance-3`, 2 vCPU, 4 GiB, 20 GiB, BIOS, VLAN135-IPAM,
static 10.12.135.160 (checked free). 19.8 s to multi-user, no failed units, SELinux Enforcing, 0 AVC, forced UI password change,
AOS environment Connected, health finds itself, self-test PASS, no failed `ev2ahv-blockd@` units afterwards (BrokenPipe fix shipped).
Passwords: `~/.config/ev2ahv/appliance3-ui.pw`, `appliance3-admin.pw` (0600). Details in docs/results.md.
The same image line was reached on `ev2ahv-appliance-2` by upgrade bundle (0.2.0 -> 0.3.2), see Upgrades.
