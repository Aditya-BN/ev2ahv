#!/bin/bash
# Runs INSIDE the privileged builder container. /src = repo (ro), /out = build dir (rw).
set -euxo pipefail
# Safety: this script partitions, formats and mounts a loop device and runs rm in a chroot. It only ever works on the
# image file /out/ev2ahv.raw, and refuses to run outside the throw-away builder container.
[ -f /.dockerenv ] || [ -f /run/.containerenv ] || { echo "refusing: mkimage.sh must run inside the builder container" >&2; exit 2; }
[ -d /src/appliance ] && [ -d /out ] || { echo "refusing: /src/appliance and /out must be mounted" >&2; exit 2; }
SZ=${IMG_SIZE:-20G}
[[ "$SZ" =~ ^[0-9]+[GM]$ ]] || { echo "refusing: bad IMG_SIZE '$SZ'" >&2; exit 2; }
RAW=/out/ev2ahv.raw
ROOT=/mnt/root
APPL=/src/appliance
rm -f "$RAW"; truncate -s "$SZ" "$RAW"
sfdisk "$RAW" <<P
label: gpt
size=1MiB, type=21686148-6449-6E6F-744E-656564454649, name=biosboot
size=100MiB, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=esp
type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=root
P
LOOP=$(losetup -f --show -P "$RAW")
case "$LOOP" in /dev/loop[0-9]*) ;; *) echo "refusing: unexpected loop device '$LOOP'" >&2; exit 2 ;; esac
# The trap only undoes what this script set up: its own mounts under $ROOT and its own loop device.
cleanup() { set +e; for m in dev/pts dev sys proc boot/efi ""; do umount -l "$ROOT/$m" 2>/dev/null; done; losetup -d "$LOOP"; }
trap cleanup EXIT
n=$(basename "$LOOP")
for p in "" p1 p2 p3; do
  [ -b "/dev/$n$p" ] || { IFS=: read -r ma mi < "/sys/block/$n/${n}${p}/dev" 2>/dev/null || IFS=: read -r ma mi < "/sys/block/$n/dev"; mknod "/dev/$n$p" b "$ma" "$mi"; }
done
# mkfs only on partitions of the loop device that is backed by our image file
[ "$(losetup -nO BACK-FILE "$LOOP" 2>/dev/null | head -1)" = "$RAW" ] || { echo "refusing: $LOOP is not backed by $RAW" >&2; exit 2; }
mkfs.vfat -F32 -n EFI "/dev/${n}p2"
mkfs.xfs -f -L ev2ahv-root "/dev/${n}p3"
mkdir -p "$ROOT"; mount "/dev/${n}p3" "$ROOT"; mkdir -p "$ROOT/boot/efi"; mount "/dev/${n}p2" "$ROOT/boot/efi"
ROOT_UUID=$(blkid -s UUID -o value "/dev/${n}p3"); EFI_UUID=$(blkid -s UUID -o value "/dev/${n}p2")
mkdir -p $ROOT/{proc,sys,dev/pts,etc/rpm,etc/dracut.conf.d}
mount -t proc proc $ROOT/proc; mount -t sysfs sys $ROOT/sys; mount --bind /dev $ROOT/dev; mount --bind /dev/pts $ROOT/dev/pts
cp $APPL/rootfs/etc/rpm/macros.image-language-conf $ROOT/etc/rpm/
cp $APPL/rootfs/etc/dracut.conf.d/10-ev2ahv.conf $ROOT/etc/dracut.conf.d/
echo "root=UUID=$ROOT_UUID" > /dev/null
# shellcheck disable=SC2046  # packages.txt is split into words on purpose
dnf -y --installroot="$ROOT" --releasever=9 --setopt=install_weak_deps=False --setopt=tsflags=nodocs \
    --setopt=cachedir=/out/dnfcache --setopt=keepcache=1 install $(grep -v '^#' $APPL/packages.txt | tr '\n' ' ')
# --- overlay
cp -a --no-preserve=ownership $APPL/rootfs/. $ROOT/
mkdir -p $ROOT/etc/ev2ahv
KVER=$(ls $ROOT/lib/modules | head -1)
# --- users
chroot $ROOT bash -euxo pipefail <<'C'
getent group ev2ahv >/dev/null || groupadd -r ev2ahv
getent passwd ev2ahv >/dev/null || useradd -r -g ev2ahv -d /var/lib/ev2ahv -s /sbin/nologin -M ev2ahv
usermod -aG ev2ahv nginx
id admin >/dev/null 2>&1 || useradd -m -G wheel -s /bin/bash admin
echo "admin:$(openssl passwd -6 'nutanix/4u')" | chpasswd -e
chage -d 0 admin
passwd -l root
# CIS no_empty_passwords: no account may log in with an empty password (all accounts here have one or are locked)
sed -i 's/ nullok\b//' /etc/pam.d/system-auth /etc/pam.d/password-auth
install -d -m 0750 -o ev2ahv -g ev2ahv /var/lib/ev2ahv /var/lib/ev2ahv/tls /var/lib/ev2ahv/config /var/log/ev2ahv
install -d -m 0750 -o root -g ev2ahv /etc/ev2ahv
chown root:ev2ahv /etc/ev2ahv/ev2ahv.env; chmod 0640 /etc/ev2ahv/ev2ahv.env
install -d -m 0755 /var/log/journal
chmod 0755 /usr/local/sbin/ev2ahv-*
ln -sf /usr/local/sbin/ev2ahv-upgrade /usr/sbin/ev2ahv-upgrade   # in sudo secure_path: `sudo ev2ahv-upgrade`
ln -sf /dev/null /etc/systemd/system/getty@tty1.service
ln -sf /usr/lib/systemd/system/ev2ahv-tls.path /etc/systemd/system/multi-user.target.wants/ev2ahv-tls.path 2>/dev/null || true
for u in chronyd sshd firewalld NetworkManager nginx auditd cloud-init-local cloud-init cloud-config cloud-final \
         ev2ahv ev2ahv-boot ev2ahv-blockd.socket ev2ahv-firstboot ev2ahv-tls.path ev2ahv-console ev2ahv-issue; do
  systemctl enable $u.service 2>/dev/null || systemctl enable $u
done
systemctl set-default multi-user.target
systemctl mask kdump tuned postfix rpcbind 2>/dev/null || true
rm -f /etc/nginx/conf.d/* /etc/nginx/default.d/* 2>/dev/null || true
sed -i 's/^#\?\s*PASS_MAX_DAYS.*/PASS_MAX_DAYS   365/;s/^#\?\s*PASS_MIN_DAYS.*/PASS_MIN_DAYS   1/;s/^#\?\s*PASS_WARN_AGE.*/PASS_WARN_AGE   7/' /etc/login.defs
echo "ev2ahv" > /etc/hostname
echo 'LANG=en_US.UTF-8' > /etc/locale.conf
echo 'KEYMAP=us' > /etc/vconsole.conf
C
# --- app
install -d $ROOT/opt/ev2ahv/app
cd /src
tar -c --exclude=__pycache__ --exclude='*.pyc' --exclude=tests --exclude=node_modules --exclude=run-dev.sh ev2ahv server prep VERSION | tar -x -C $ROOT/opt/ev2ahv/app
mkdir -p $ROOT/opt/ev2ahv/app/web; cp -a /src/web/dist $ROOT/opt/ev2ahv/app/web/dist
# kept so ev2ahv-upgrade can tell whether a bundle needs a new venv
cp /src/appliance/requirements.txt $ROOT/opt/ev2ahv/app/requirements.txt
mkdir -p $ROOT/tmp/wheels; cp /out/wheels/* $ROOT/tmp/wheels/; cp $APPL/requirements.txt $ROOT/tmp/
chroot $ROOT bash -euxo pipefail <<'C'
python3.12 -m venv /opt/ev2ahv/venv
/opt/ev2ahv/venv/bin/pip install --no-cache-dir --no-index --find-links /tmp/wheels -r /tmp/requirements.txt
/opt/ev2ahv/venv/bin/python -m compileall -q /opt/ev2ahv/app || true
chown -R root:root /opt/ev2ahv; chmod -R go-w /opt/ev2ahv
rm -rf /tmp/wheels /tmp/requirements.txt /root/.cache
C
# --- selinux module
cd /tmp; cp $APPL/selinux/ev2ahv.{te,fc} .
checkmodule -M -m -o ev2ahv.mod ev2ahv.te; semodule_package -o ev2ahv.pp -m ev2ahv.mod -f ev2ahv.fc
cp ev2ahv.pp $ROOT/tmp/; chroot $ROOT semodule -n -i /tmp/ev2ahv.pp; rm $ROOT/tmp/ev2ahv.pp
sed -i 's/^SELINUX=.*/SELINUX=enforcing/' $ROOT/etc/selinux/config
# --- initramfs + bootloader
chroot $ROOT dracut --force --kver $KVER /boot/initramfs-$KVER.img
CMDLINE="root=UUID=$ROOT_UUID ro rootflags=defaults console=tty0 console=ttyS0,115200n8 net.ifnames=0 selinux=1 enforcing=1 audit=1 audit_backlog_limit=8192 crashkernel=no"
echo "$CMDLINE" > $ROOT/etc/kernel/cmdline
mkdir -p $ROOT/boot/loader/entries $ROOT/boot/grub2
cp $ROOT/lib/modules/$KVER/vmlinuz $ROOT/boot/vmlinuz-$KVER 2>/dev/null || true
ls $ROOT/boot/loader/entries/ || true
rm -f $ROOT/boot/loader/entries/*.conf
cat > $ROOT/boot/loader/entries/ev2ahv-$KVER.conf <<B
title Rocky Linux ($KVER)
version $KVER
linux /boot/vmlinuz-$KVER
initrd /boot/initramfs-$KVER.img
options $CMDLINE
grub_users \$grub_users
grub_arg --unrestricted
grub_class rocky
B
cat > $ROOT/boot/grub2/grub.cfg <<B
set timeout=3
set pager=1
serial --speed=115200 --unit=0 --word=8 --parity=no --stop=1
terminal_input serial console
terminal_output serial console
insmod part_gpt
insmod xfs
insmod blscfg
search --no-floppy --fs-uuid --set=root $ROOT_UUID
set boot=\$root
if [ -f \${prefix}/grubenv ]; then load_env; fi
if [ -n "\$saved_entry" ]; then set default="\$saved_entry"; else set default=0; fi
function load_video { true; }
blscfg
B
grub2-editenv $ROOT/boot/grub2/grubenv create
chroot $ROOT bash -c 'chown root:root /boot/grub2/grubenv'
# BIOS
grub2-install --target=i386-pc --boot-directory=$ROOT/boot --modules="part_gpt xfs" $LOOP
# UEFI
EFI=$ROOT/boot/efi/EFI; mkdir -p $EFI/rocky $EFI/BOOT
cp $ROOT/boot/efi/EFI/rocky/shimx64.efi $EFI/BOOT/BOOTX64.EFI
cp $ROOT/boot/efi/EFI/rocky/grubx64.efi $EFI/BOOT/grubx64.efi
cp $ROOT/boot/efi/EFI/rocky/shimx64.efi $ROOT/boot/efi/EFI/rocky/shimx64.efi 2>/dev/null || true
cat > $EFI/rocky/grub.cfg <<B
search --no-floppy --fs-uuid --set=dev $ROOT_UUID
set prefix=(\$dev)/boot/grub2
export \$prefix
configfile \$prefix/grub.cfg
B
cp $EFI/rocky/grub.cfg $EFI/BOOT/grub.cfg
cat > $ROOT/etc/fstab <<B
UUID=$ROOT_UUID / xfs defaults 0 0
UUID=$EFI_UUID /boot/efi vfat umask=0077,shortname=winnt 0 2
tmpfs /tmp tmpfs defaults,nodev,nosuid,size=1G 0 0
tmpfs /dev/shm tmpfs defaults,nodev,nosuid,noexec 0 0
B
# --- relabel, cleanup
# The rm -rf below runs inside the chroot; make sure the chroot is the image root, not an empty or host directory.
mountpoint -q "$ROOT" && [ -x "$ROOT/usr/bin/bash" ] && [ -f "$ROOT/etc/fstab" ] || { echo "refusing to clean: $ROOT is not the mounted image root" >&2; exit 2; }
chroot $ROOT bash -euxo pipefail <<'C'
dnf clean all
rm -rf /var/cache/dnf/* /var/cache/ldconfig/* /tmp/* /var/tmp/* /var/log/dnf*.log /var/log/hawkey.log /var/log/*.log
rm -f /etc/machine-id /var/lib/systemd/random-seed /root/.bash_history /etc/ssh/ssh_host_*
: > /etc/machine-id
rm -rf /usr/share/doc /usr/share/man /usr/share/info
C
setfiles -F -r $ROOT /etc/selinux/targeted/contexts/files/file_contexts $ROOT 2>/dev/null || \
  setfiles -F -r $ROOT $ROOT/etc/selinux/targeted/contexts/files/file_contexts $ROOT
setfiles -F -r $ROOT $ROOT/etc/selinux/targeted/contexts/files/file_contexts $ROOT/boot/efi 2>/dev/null || true
chroot $ROOT rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > /out/rpm.list
chroot $ROOT /opt/ev2ahv/venv/bin/pip list --format=freeze > /out/pip.list
# D10: nothing installs packages at runtime (upgrades rebuild the venv), so pip does not ship. This also removes
# the vendored setuptools 70.3.0 metadata that trivy reported as a HIGH false positive.
chroot $ROOT /opt/ev2ahv/venv/bin/python -m pip uninstall -q -y pip
du -sh $ROOT --exclude=proc --exclude=sys --exclude=dev || true
# tree for scanners
rm -rf /out/tree; mkdir /out/tree   # constant path inside the builder container
tar -C $ROOT --exclude=./proc/* --exclude=./sys/* --exclude=./dev/* -c . | tar -C /out/tree -x --no-same-owner 2>/dev/null || true
fstrim -v $ROOT/boot/efi || true; fstrim -v $ROOT || true
cleanup; trap - EXIT
qemu-img convert -c -O qcow2 $RAW /out/ev2ahv.qcow2
ls -l /out/ev2ahv.qcow2 $RAW
