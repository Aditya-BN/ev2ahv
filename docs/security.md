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

Important caveat: the sysctl and kernel-module rules (about 20 of the failures) are false negatives. `oscap-chroot` reads the running kernel of the build container, not the image; the values are set in `/etc/sysctl.d/90-ev2ahv.conf` and `/etc/modprobe.d/ev2ahv-blacklist.conf`. They are not verified on a booted instance by this scan.

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

Python venv (Python 3.12 from Rocky AppStream, venv `/opt/ev2ahv/venv`): 0 CRITICAL, 1 HIGH reported by trivy. The remaining one is `setuptools 70.3.0` (CVE-2025-47273, fixed in 78.1.1). The venv ships setuptools 84.2.1 and the image holds no 70.3.0 metadata I could locate (the SBOM lists only 53.0.0 for the system python3.9 and 84.2.1 for the venv), so this is most likely a trivy false positive on a vendored copy; unconfirmed.

pip-audit on `appliance/requirements.txt`: 0 known vulnerabilities (was 19 on Python 3.9). The venv moved from python3.9 to python3.12 and the pins were bumped to the latest releases (fastapi 0.165.0, starlette 1.10.0, anyio 4.16.0, urllib3 2.8.0, requests 2.34.2, pip 26.3, setuptools 84.2.1, uvicorn 0.54.0, pydantic 2.14.4, PyYAML 6.0.4, xxhash 4.1.0, among others). The system python3.9 stays installed because dnf and cloud-init need it; `ev2ahv-config` and the root helper `blockd.py` unit run from the venv python. Rocky's python3.9 rpm packages carry setuptools 53.0.0 (trivy reports no HIGH for it with fixes available).

## Not verified / limits

- Smoke boot used qemu TCG, SeaBIOS (BIOS path) only; UEFI boot of the image was not exercised (`SMOKE_UEFI=1` is wired in `smoke.sh` but was not run) and neither was a real AHV boot. The orchestrator validates on AHV.
- The console menu was exercised through `--status` and the unit start; the network/hostname/NTP actions were not run against a live NM in the smoke boot.
