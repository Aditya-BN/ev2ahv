#!/bin/bash
# ev2ahv Linux guest prep check. Read-only: it changes nothing in the guest.
# Run as root inside the source VM. Writes a report to /var/tmp/ev2ahv-prep-<host>.txt
# Exit: 0 ready, 1 warnings to review, 2 blocked (fix before cutover).
set -u
export LC_ALL=C
REPORT="/var/tmp/ev2ahv-prep-$(hostname -s).txt"
WARN=0; BLOCK=0; KVER=$(uname -r)
exec > >(tee "$REPORT") 2>&1

ok()    { echo "[ OK ] $*"; }
warn()  { echo "[WARN] $*"; WARN=$((WARN+1)); }
block() { echo "[STOP] $*"; BLOCK=$((BLOCK+1)); }
info()  { echo "[info] $*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 2; }
echo "ev2ahv Linux prep check: $(hostname -f 2>/dev/null || hostname), kernel $KVER, $(date -u +%FT%TZ)"
[ -r /etc/os-release ] && . /etc/os-release && info "OS: ${PRETTY_NAME:-unknown}"

echo; echo "--- Firmware"
if [ -d /sys/firmware/efi ]; then
  info "Firmware: UEFI. Set target.firmware: uefi"
  [ "$(od -An -tu1 -j4 -N1 /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c 2>/dev/null | tr -d ' ')" = "1" ] \
    && warn "Secure Boot is on. ev2ahv creates the VM with Secure Boot off"
else
  info "Firmware: BIOS. Set target.firmware: bios"
fi

echo; echo "--- Boot drivers in the initramfs"
INITRD=""
for f in "/boot/initramfs-$KVER.img" "/boot/initrd.img-$KVER" "/boot/initrd-$KVER"; do [ -f "$f" ] && INITRD=$f && break; done
LIST=""
if [ -n "$INITRD" ]; then
  if command -v lsinitrd >/dev/null; then LIST=$(lsinitrd "$INITRD" 2>/dev/null)
  elif command -v lsinitramfs >/dev/null; then LIST=$(lsinitramfs "$INITRD" 2>/dev/null)
  fi
fi
BUILTIN="/lib/modules/$KVER/modules.builtin"
has_driver() {
  { [ -f "$BUILTIN" ] && grep -qE "/$1\.ko" "$BUILTIN"; } && return 0
  echo "$LIST" | grep -qE "/$1\.ko"
}
SCSI=no; BLK=no
if [ -z "$LIST" ]; then
  warn "Could not read the initramfs. Check the drivers by hand"
else
  has_driver virtio_scsi && SCSI=yes
  has_driver virtio_blk && BLK=yes
  has_driver virtio_pci || warn "virtio_pci is not in the initramfs"
  has_driver virtio_net || info "virtio_net is not in the initramfs (fine unless the root is on the network)"
fi
if [ "$SCSI" = yes ]; then ok "virtio_scsi available at boot. Use disk_bus: scsi"
elif [ "$BLK" = yes ]; then warn "virtio_scsi missing, virtio_blk present. Use disk_bus: pci, convert to SCSI later"
elif [ -n "$LIST" ]; then
  block "No virtio disk driver at boot. Fix: dracut -f --add-drivers 'virtio_scsi virtio_blk virtio_pci' (Debian: add to /etc/initramfs-tools/modules, then update-initramfs -u)"
fi

echo; echo "--- Disk references"
BAD=$(awk '!/^[[:space:]]*#/ && NF>=2 && $1 ~ /^\/dev\/(sd|vd|hd|xvd|nvme)/' /etc/fstab 2>/dev/null)
if [ -n "$BAD" ]; then
  warn "fstab uses device names, which can change on AHV. Switch these to UUID= or LABEL=:"
  echo "$BAD" | sed 's/^/       /'
else ok "fstab uses UUID, LABEL or LVM paths"; fi
for g in /etc/default/grub /proc/cmdline; do
  grep -qE 'root=/dev/(sd|vd|hd|xvd)' "$g" 2>/dev/null && warn "$g has root=/dev/... Use root=UUID= instead"
done
if command -v lvmconfig >/dev/null; then
  F=$(lvmconfig devices/filter devices/global_filter 2>/dev/null | grep -E '/dev/(sd|vd|hd)')
  [ -n "$F" ] && warn "LVM filter names specific devices, which may hide disks on AHV: $F"
  [ -s /etc/lvm/devices/system.devices ] && warn "LVM devices file exists. Device IDs change on AHV. Boot may need: vgimportdevices -a, or remove /etc/lvm/devices/system.devices before cutover"
  VGS=$(vgs --noheadings -o vg_name 2>/dev/null | xargs); [ -n "$VGS" ] && info "LVM volume groups: $VGS"
fi

echo; echo "--- Network (recorded for reference)"
for path in /sys/class/net/*; do
  n=${path##*/}; [ "$n" = lo ] && continue; [ -e "$path/device" ] || continue
  drv=$(basename "$(readlink -f "$path/device/driver" 2>/dev/null)")
  info "NIC $n mac=$(cat "$path/address") driver=$drv"
  ip -o -4 addr show dev "$n" | awk '{print "       ipv4 " $4}'
done
ip route show default | sed 's/^/       route /'
grep -E '^nameserver' /etc/resolv.conf 2>/dev/null | sed 's/^/       /'
info "ev2ahv keeps the MAC by default (nics[].keep_mac: true)"

echo; echo "--- NIC rename risk"
RISK=0
for f in /etc/udev/rules.d/*net*.rules; do
  [ -f "$f" ] && grep -q 'ATTR{address}' "$f" && { warn "$f pins NIC names to MACs. Safe only if the MAC is kept"; RISK=1; }
done
if grep -lsE '^(HWADDR|MACADDR)=' /etc/sysconfig/network-scripts/ifcfg-* >/dev/null 2>&1; then
  warn "ifcfg files pin a MAC. Safe only if the MAC is kept"; RISK=1
fi
if grep -lsE '^mac-address=' /etc/NetworkManager/system-connections/* >/dev/null 2>&1; then
  warn "NetworkManager profiles pin a MAC. Safe only if the MAC is kept"; RISK=1
fi
if grep -lsE 'macaddress:' /etc/netplan/*.y*ml >/dev/null 2>&1; then
  warn "netplan matches by MAC. Safe only if the MAC is kept"; RISK=1
fi
if grep -qE 'net\.ifnames=0' /proc/cmdline; then
  ok "net.ifnames=0: names stay eth0, eth1 by order"
else
  for path in /sys/class/net/*; do
    n=${path##*/}
    case "$n" in en*) warn "$n is a slot-based name and may change on AHV because the PCI slot changes. Check that the network config matches by MAC or uses a wildcard"; RISK=1 ;; esac
  done
fi
[ "$RISK" -eq 0 ] && ok "No NIC rename risk found"

echo; echo "--- Stratus guest software"
FOUND=$( { rpm -qa 2>/dev/null; dpkg -l 2>/dev/null | awk '{print $2}'; } | grep -iE 'stratus|everrun' )
[ -n "$FOUND" ] && warn "Stratus packages found. Remove after cutover, on AHV only: $FOUND" || ok "No Stratus guest packages"

echo; echo "Summary: $BLOCK blocking, $WARN warnings. Report: $REPORT"
[ "$BLOCK" -gt 0 ] && exit 2
[ "$WARN" -gt 0 ] && exit 1
exit 0
