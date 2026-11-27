# ev2ahv appliance security notes

Scans run against the root tree of `dist/ev2ahv-0.2.0.qcow2` (`make appliance-scan`, output in `build/scan/`). Version scanned: 0.2.0, Rocky 9.9 packages as of the build date.

## Hardening in the image

- SELinux enforcing, custom module `appliance/selinux/ev2ahv.te/.fc` (nginx connect to the app socket; labels for static content, TLS dir, `/run/ev2ahv`). Smoke boot: 0 AVC denials for nginx start, `GET /` and `GET /api/v2/appinfo` (audit backlog raised to 8192 so no events are lost).
- App service: unprivileged `ev2ahv`, empty capability set, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, kernel tunables/modules/cgroups protected, restricted address families. `PrivateDevices=no` because the app lists disks through `/dev/disk/by-id`, `lsblk`, `udevadm`.
- Root work only through `ev2ahv-blockd` (socket mode 0660 root:ev2ahv, one process per connection, hardened unit); `ev2ahv-blockd@` requires `ev2ahv-boot` (fail closed if the baseline could not be recorded).
- nginx: TLS 1.2/1.3, AEAD ECDHE ciphers, HSTS, nosniff, DENY framing, no-referrer, CSP `default-src 'self'` (plus `img-src data:` for the inline favicon and `style-src 'unsafe-inline'` for React inline styles), no port 80, `server_tokens off`.
- firewalld public zone: ssh and https only. sshd: no root login, `MaxAuthTries 4`, `LogLevel VERBOSE`, `MaxStartups 10:30:60`, keepalive, banner, no X11, no empty passwords.
- Accounts: root locked; `admin` (wheel, sudo with password, `use_pty`, sudo log) with an expired password; `PASS_MAX_DAYS 365`; shell `TMOUT=900`; core dumps off.
- sysctl hardening (`/etc/sysctl.d/90-ev2ahv.conf`), uncommon filesystems/protocols blacklisted, `/tmp` and `/dev/shm` as nodev/nosuid tmpfs (`/dev/shm` also noexec), persistent journal capped at 200 MB, auditd enabled.
- Unique SSH host keys, TLS key and `instance.key` per instance, generated at first boot. No secrets are baked into the image except the documented initial passwords, which are forced to change.

## OpenSCAP CIS Level 1 Server

Profile `xccdf_org.ssgproject.content_profile_cis_server_l1` from scap-security-guide (`ssg-rl9-ds.xml`, the Rocky 9 datastream shipped in Rocky's own repos, so no RHEL CPE workaround was needed), evaluated with `oscap-chroot` on the image tree.

Result (re-run on the Python 3.12 image, unchanged): 187 pass, 69 fail, 34 not applicable, OpenSCAP score 83.7 (about 73 % of applicable rules pass; first run before the hardening pass above: 172 pass / 81 fail).

Sysctl and kernel-module rules (22 of the failures): `oscap-chroot` reads the running kernel of the build container, not the
image, so these fail in this scan whatever the image sets. A static check of the 0.2.0 tree (2026-11-26, D10) found that most
values were already right in `/etc/sysctl.d/90-ev2ahv.conf`, but three gaps were real and are fixed in the repo for the next
build (not yet rebuilt or rescanned):

| Rule | Gap in the 0.2.0 tree | Fix (`appliance/`) |
| --- | --- | --- |
| sysctl_net_ipv6_conf_all_forwarding | key not set | `net.ipv6.conf.{all,default}.forwarding = 0` in `rootfs/etc/sysctl.d/90-ev2ahv.conf` |
| kernel_module_{cramfs,freevxfs,hfs,hfsplus,jffs2,usb-storage}_disabled | `install X /bin/false` only; the rules also want `blacklist X` | `blacklist` lines in `rootfs/etc/modprobe.d/ev2ahv-blacklist.conf` |
| no_empty_passwords | `nullok` on pam_unix in system-auth and password-auth | `mkimage.sh` removes `nullok` (every account has a password or is locked) |

The other sysctl rules (ipv4 redirects, source route, log_martians, rp_filter, ip_forward, ipv6 accept_ra/redirects,
yama ptrace_scope) are set correctly in the tree. All of these still need checking on a booted instance (`sysctl -a`,
`modprobe -n -v cramfs`).

Not explained in the table below and left open: `ensure_pam_wheel_group_empty` (wheel holds `admin`, needed for sudo).

Accepted exceptions (real failures):

| Rules | Reason |
| --- | --- |
| AIDE (4 rules) | integrity DB would be stale after the first `dnf update`; out of scope for an appliance, use image replacement |
| partition_for_tmp, partition_for_dev_shm, mount_option_tmp_noexec | single root partition by design; `/tmp` is tmpfs nodev/nosuid but not noexec because rpm scriptlets and pip may need to execute from it during updates |
| authselect, pam faillock/pwquality/pwhistory (about 17 rules), password max life for existing accounts, post-password expiry, root password configured, `su` wheel restriction | PAM profile hardening was not applied: it risks locking the only admin out of an unattended appliance and interferes with the forced-change flow. Candidate for a later iteration with an authselect custom profile |
| umask 027 rules, user dot-file ownership/permissions | `admin` home is created by useradd with defaults; umask left at 022 so app/dnf-created files stay readable by services |
| accounts_tmout | set via `/etc/profile.d` but the rule wants it in /etc/profile or bashrc form |
| configure_custom_crypto_policy_cis | system-wide crypto policy stays DEFAULT (needed to reach older everRun/WinRM endpoints) |
| grub2_password | console access already equals root on a VM; the hypervisor controls console access |
| rsyslog_filecreatemode, journal-remote package | rsyslog is not installed; journald only; no remote log forwarding by default |
| firewalld loopback rules | loopback is allowed by default firewalld behaviour, rule wants explicit trusted zone config |
| cron/at allow files, crond | cron is not installed; there are no scheduled jobs (the app has its own scheduler) |
| sshd_limit_user_access, sshd_set_keepalive | access limited by account policy and firewall; keepalive set via ClientAliveInterval/CountMax in a form the rule's regex does not match |

## trivy rootfs (HIGH, CRITICAL)

OS packages (Rocky 9.9): 0 HIGH/CRITICAL (with fixes available) at build time.

Python venv (Python 3.12 from Rocky AppStream, venv `/opt/ev2ahv/venv`): 0 CRITICAL, 1 HIGH reported by trivy:
`setuptools 70.3.0` (CVE-2025-47273, path traversal in `setuptools.package_index.PackageIndex`, fixed in 78.1.1).
Confirmed false positive (2026-11-26, trivy 0.76.2, on the 0.2.0 tree): the 70.3.0 entry has no file path and comes with
exactly the package list of pip 26.3's `pip/_vendor/vendor.txt` (CacheControl, distlib, resolvelib, rich, truststore, ...).
pip vendors only `pkg_resources` from setuptools, and there is no `package_index` module anywhere under `pip/_vendor`.
With `--skip-dirs opt/ev2ahv/venv/lib/python3.12/site-packages/pip` the HIGH count is 0. The installed setuptools is 84.2.1.
Options if a clean report is required: drop pip from the shipped venv (nothing installs packages at runtime) or record the
exception in a trivy ignore file; neither is done yet.

`scan.sh`: both trivy runs now share `build/trivy-cache`; before, the second run (`--skip-db-update`) had no DB on a cold
cache and failed. Tested on the 0.2.0 tree: both runs exit 0.

pip-audit on `appliance/requirements.txt`: 0 known vulnerabilities (was 19 on Python 3.9). The venv moved from python3.9 to python3.12 and the pins were bumped to the latest releases (fastapi 0.165.0, starlette 1.10.0, anyio 4.16.0, urllib3 2.8.0, requests 2.34.2, pip 26.3, setuptools 84.2.1, uvicorn 0.54.0, pydantic 2.14.4, PyYAML 6.0.4, xxhash 4.1.0, among others). The system python3.9 stays installed because dnf and cloud-init need it; the blockd units run from the venv python (3.12); `ev2ahv-config` (stdlib only) stays on system python3. Rocky's python3.9 rpm packages carry setuptools 53.0.0 (trivy reports no HIGH for it with fixes available).

## Not verified / limits

- Smoke boot used qemu TCG, SeaBIOS (BIOS path) only; UEFI boot of the image was not exercised (`SMOKE_UEFI=1` is wired in `smoke.sh` but was not run) and neither was a real AHV boot. The orchestrator validates on AHV.
- The console menu was exercised through `--status` and the unit start; the network/hostname/NTP actions were not run against a live NM in the smoke boot.

## UI users and roles (D13, 2026-11-26)

Local accounts are stored in the app database (`/var/lib/ev2ahv`, kind `user`), with a PBKDF2-SHA256 hash
(200,000 rounds, per-user salt), a role and a must-change flag. On first start after an upgrade the 0.2.0 login
(`ui.json`) is imported as an admin with its password, so nobody is locked out.

| Role | Can |
| --- | --- |
| admin | everything, including Users, Settings, TLS, support bundle |
| viewer | read every page and API (plans, events, logs, health, settings); change only their own password |

- Enforcement is one middleware: every non-GET `/api/` call from a viewer gets 403, except login, logout and their own
  password change. A new route is admin-only by default. The support bundle (a GET) is admin-only.
- New users and admin password resets must change the password at first login. A reset ends that user's sessions;
  a role change applies to their open sessions at once.
- The last admin cannot be demoted or deleted, and nobody can delete their own account.
- Console "reset UI password" (`ui.reset`) restores the default account (`nutanix`) as an admin with the default
  password and a forced change; other accounts are left alone.
- Every add, change, reset and delete is an event (who and what), so it is in the audit trail.

LDAP / Active Directory (D13, 0.3.7+): Appliance Settings > Directory sign-in. Local accounts are tried first. For a
directory user ev2ahv binds with a service account, finds exactly one entry with `user_filter` (default
`(sAMAccountName={username})`, the username is escaped) under `user_base`, binds as that DN with the typed password, and
gives the admin or viewer role from membership of `admin_group` / `viewer_group` (`member`, `uniqueMember` or
`memberUid` on the group entry, so it works with AD and OpenLDAP without `memberOf`). Users in neither group cannot sign
in. Nothing about the user is stored; the role is decided at every login. The service account password is write-only
(encrypted secret store). Clear-text `ldap://` is refused unless StartTLS is on (or `allow_plain` for a lab); certificate
verification is on by default. A directory outage never blocks local accounts. Library: `ldap3` 2.9.1 + `pyasn1` 0.6.5.

## Booted-instance check (2026-11-27, `ev2ahv-appliance-3`, 0.3.5 on AHV)

The sysctl and kernel-module rules fail under `oscap-chroot` because it reads the build container's kernel. Read on the
running appliance instead (read-only, `sysctl -n`, `modprobe -n -v`, `lsmod`):

- all 18 sysctl keys from the CIS rules have the required values (ipv4 forward/redirects/source route/martians/rp_filter,
  ipv6 accept_ra/redirects/forwarding, `kernel.yama.ptrace_scope=1`);
- cramfs, freevxfs, hfs, hfsplus, jffs2, usb-storage and squashfs resolve to `install /bin/false` and none is loaded;
- `nullok` is gone from system-auth and password-auth; SELinux Enforcing.

So the 22 sysctl/module "fails" in the chroot scan are passes on a real instance.

