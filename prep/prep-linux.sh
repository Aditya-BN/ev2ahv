#!/bin/bash
# ev2ahv Linux guest prep and cleanup. Run as root. Idempotent.
#
#   prep-linux.sh            check: read-only, prints the facts ev2ahv grades as PASS/WARN/FAIL
#   prep-linux.sh --apply    prep, on the source: adds virtio drivers to the initramfs, pins each NIC
#                            name to its MAC, makes NetworkManager profiles match by MAC, and moves the
#                            LVM devices file aside. Writes /root/ev2ahv/prep.json. Backups: /root/ev2ahv/backup
#   prep-linux.sh --cleanup  on the migrated VM on AHV: removes Stratus packages and rebuilds the
#                            initramfs if prep changed it. Writes /root/ev2ahv/cleanup.json
#
# Every change also works on the source, so booting it again after a rollback is safe.
# The last output line is "EV2AHV-JSON:" followed by the facts as JSON.
# Exit: 0 done, 1 something needs attention, 2 refused.
set -u
export LC_ALL=C
MODE=check
case "${1:-}" in
  --apply) MODE=prep ;;
  --cleanup) MODE=cleanup ;;
  "") ;;
  *) echo "usage: $0 [--apply|--cleanup]"; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 2; }

D=/root/ev2ahv; BK=$D/backup
mkdir -p "$BK"; chmod 700 "$D"
CHANGES=(); PROBLEMS=()
change()  { CHANGES+=("$1"); echo "[CHG ] $1"; }
problem() { PROBLEMS+=("$1"); echo "[PROB] $1"; }
js()   { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\t'/ }; s=${s//$'\n'/ }; printf '"%s"' "$s"; }
jarr() { local out="" x; for x in "$@"; do out+="${out:+,}$(js "$x")"; done; printf '[%s]' "$out"; }
jbool() { [ "$1" = true ] && printf true || printf false; }
backup() { [ -e "$1" ] && [ ! -e "$BK$1" ] && mkdir -p "$BK$(dirname "$1")" && cp -a "$1" "$BK$1"; }

VENDOR=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)
ON_AHV=false; [[ "$VENDOR" == *Nutanix* ]] && ON_AHV=true
. /etc/os-release 2>/dev/null
FIRMWARE=bios; [ -d /sys/firmware/efi ] && FIRMWARE=uefi
NOW=$(date -u +%FT%TZ)

if [ "$MODE" = prep ] && [ "$ON_AHV" = true ]; then
  problem "refusing: --apply runs on the source, and this is already on AHV"
  printf 'EV2AHV-JSON:{"version":2,"mode":"prep","problems":%s}\n' "$(jarr "${PROBLEMS[@]}")"; exit 2
fi
if [ "$MODE" = cleanup ] && [ "$ON_AHV" != true ]; then
  problem "refusing: --cleanup runs on the migrated VM on AHV"
  printf 'EV2AHV-JSON:{"version":2,"mode":"cleanup","problems":%s}\n' "$(jarr "${PROBLEMS[@]}")"; exit 2
fi

# ------------------------------------------------------------------ initramfs
KVER=$(uname -r)
if command -v grubby >/dev/null; then
  k=$(grubby --default-kernel 2>/dev/null); [ -n "$k" ] && KVER=${k#/boot/vmlinuz-}
fi
initrd_for() {
  local f
  for f in "/boot/initramfs-$1.img" "/boot/initrd.img-$1" "/boot/initrd-$1"; do [ -f "$f" ] && { echo "$f"; return; }; done
}
list_initrd() {
  local img; img=$(initrd_for "$KVER")
  [ -n "$img" ] || return 1
  if command -v lsinitrd >/dev/null; then lsinitrd "$img" 2>/dev/null
  elif command -v lsinitramfs >/dev/null; then lsinitramfs "$img" 2>/dev/null
  else return 1; fi
}
has_mod() { { [ -f "/lib/modules/$KVER/modules.builtin" ] && grep -qE "/$1\.ko" "/lib/modules/$KVER/modules.builtin"; } || echo "$INITRD_LIST" | grep -qE "/$1\.ko"; }
VIRTIO="virtio_scsi virtio_blk virtio_net virtio_pci"
INITRD_LIST=$(list_initrd); INITRD_OK=false; [ -n "$INITRD_LIST" ] && INITRD_OK=true

rebuild_initramfs() {
  if command -v dracut >/dev/null; then
    dracut -f --regenerate-all >/dev/null 2>&1 || dracut -f --kver "$KVER" >/dev/null 2>&1
  elif command -v update-initramfs >/dev/null; then
    update-initramfs -u -k all >/dev/null 2>&1
  else
    return 1
  fi
}

if [ "$MODE" = prep ]; then
  missing=""; for m in $VIRTIO; do has_mod "$m" || missing+=" $m"; done
  if [ -n "$missing" ]; then
    if command -v dracut >/dev/null; then
      conf=/etc/dracut.conf.d/ev2ahv-virtio.conf
      [ -f "$conf" ] || { printf 'add_drivers+=" %s "\n' "$VIRTIO" > "$conf"; change "wrote $conf"; }
    elif [ -f /etc/initramfs-tools/modules ]; then
      backup /etc/initramfs-tools/modules
      for m in $VIRTIO; do grep -qx "$m" /etc/initramfs-tools/modules || { echo "$m" >> /etc/initramfs-tools/modules; change "added $m to /etc/initramfs-tools/modules"; }; done
    fi
    if rebuild_initramfs; then change "rebuilt initramfs to add:$missing"; else problem "could not rebuild the initramfs"; fi
    INITRD_LIST=$(list_initrd); [ -n "$INITRD_LIST" ] && INITRD_OK=true
  fi
fi

# ------------------------------------------------------------------ disk references
FSTAB_BAD=(); CRYPT_BAD=()
while read -r src _; do FSTAB_BAD+=("$src"); done < <(awk '!/^[[:space:]]*#/ && NF>=2 && $1 ~ /^\/dev\/(sd|vd|hd|xvd|nvme)/' /etc/fstab 2>/dev/null)
while read -r _ src _; do CRYPT_BAD+=("$src"); done < <(awk '!/^[[:space:]]*#/ && NF>=2 && $2 ~ /^\/dev\/(sd|vd|hd|xvd|nvme)/' /etc/crypttab 2>/dev/null)
for g in /etc/default/grub /proc/cmdline; do
  grep -oE 'root=/dev/(sd|vd|hd|xvd)[a-z0-9]*' "$g" 2>/dev/null | while read -r r; do echo "[WARN] $g has $r"; done
done
LVMDEV=false
if [ -s /etc/lvm/devices/system.devices ]; then
  if [ "$MODE" = prep ]; then
    backup /etc/lvm/devices/system.devices && rm -f /etc/lvm/devices/system.devices && change "moved the LVM devices file to $BK"
  else
    LVMDEV=true
  fi
fi

# ------------------------------------------------------------------ network
nm_conn() { command -v nmcli >/dev/null && nmcli -g GENERAL.CONNECTION dev show "$1" 2>/dev/null | head -1; }
pinned() {   # $1 name, $2 mac
  grep -qE 'net\.ifnames=0' /proc/cmdline && [[ "$1" == eth* ]] && return 0
  grep -lis "MACAddress=$2" /etc/systemd/network/*.link 2>/dev/null | xargs -r grep -qs "^Name=$1\$" && return 0
  grep -qis "ATTR{address}==\"$2\".*NAME=\"$1\"" /etc/udev/rules.d/*.rules 2>/dev/null
}
bound_by_name() {
  local c; c=$(nm_conn "$1")
  [ -n "$c" ] && [ -n "$(nmcli -g connection.interface-name con show "$c" 2>/dev/null)" ] && return 0
  grep -qsE "^DEVICE=\"?$1\"?\$" /etc/sysconfig/network-scripts/ifcfg-* && ! grep -qsE '^HWADDR=' "$(grep -lsE "^DEVICE=\"?$1\"?\$" /etc/sysconfig/network-scripts/ifcfg-* | head -1)" && return 0
  grep -qsw "$1" /etc/netplan/*.y*ml /etc/network/interfaces && return 0
  grep -qs "^Name=$1\$" /etc/systemd/network/*.network
}
NICS_JSON=""
for path in /sys/class/net/*; do
  n=${path##*/}; [ -e "$path/device" ] || continue
  mac=$(cat "$path/address"); drv=$(basename "$(readlink -f "$path/device/driver" 2>/dev/null)")
  if [ "$MODE" = prep ]; then
    if ! pinned "$n" "$mac"; then
      f=/etc/systemd/network/70-ev2ahv-$n.link
      mkdir -p /etc/systemd/network
      printf '# ev2ahv: keep this NIC name when the PCI slot changes on AHV\n[Match]\nMACAddress=%s\n\n[Link]\nName=%s\n' "$mac" "$n" > "$f"
      change "pinned NIC name $n to MAC $mac in $f"
    fi
    c=$(nm_conn "$n")
    if [ -n "$c" ] && [ -z "$(nmcli -g 802-3-ethernet.mac-address con show "$c" 2>/dev/null)" ]; then
      backup "$(nmcli -g connection.filename con show "$c" 2>/dev/null)"
      nmcli con mod "$c" connection.interface-name "" 802-3-ethernet.mac-address "$mac" && change "NetworkManager profile '$c' now matches MAC $mac"
    fi
  fi
  c=$(nm_conn "$n"); nmmac=false
  [ -n "$c" ] && [ -n "$(nmcli -g 802-3-ethernet.mac-address con show "$c" 2>/dev/null)" ] && nmmac=true
  pin=false; pinned "$n" "$mac" && pin=true
  byname=false; bound_by_name "$n" && byname=true
  ips=(); while read -r a; do ips+=("$a"); done < <(ip -o -4 addr show dev "$n" | awk '{print $4}')
  NICS_JSON+="${NICS_JSON:+,}{\"name\":$(js "$n"),\"mac\":$(js "$mac"),\"driver\":$(js "$drv"),\"ipv4\":$(jarr "${ips[@]}"),\"pinned\":$(jbool $pin),\"nm_by_mac\":$(jbool $nmmac),\"bound_by_name\":$(jbool $byname)}"
  echo "[info] NIC $n mac=$mac driver=$drv ipv4=${ips[*]:-none} pinned=$pin nm_by_mac=$nmmac bound_by_name=$byname"
done
ROUTE=$(ip route show default | head -1)
DNS=(); while read -r a; do DNS+=("$a"); done < <(awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null)

# ------------------------------------------------------------------ Stratus software
STRATUS=(); while read -r p; do [ -n "$p" ] && STRATUS+=("$p"); done < <( { rpm -qa 2>/dev/null; dpkg-query -W -f '${Package}\n' 2>/dev/null; } | grep -iE 'stratus|everrun')
if [ "$MODE" = cleanup ]; then
  if [ ${#STRATUS[@]} -gt 0 ]; then
    rc=1
    if command -v dnf >/dev/null; then dnf -y remove "${STRATUS[@]}" >/dev/null 2>&1; rc=$?
    elif command -v yum >/dev/null; then yum -y remove "${STRATUS[@]}" >/dev/null 2>&1; rc=$?
    elif command -v apt-get >/dev/null; then apt-get -y remove "${STRATUS[@]}" >/dev/null 2>&1; rc=$?; fi
    if [ "$rc" = 0 ]; then change "removed Stratus packages: ${STRATUS[*]}"; else problem "could not remove: ${STRATUS[*]}"; fi
    STRATUS=()
  fi
  if grep -qs 'rebuilt initramfs' "$D/prep.json"; then
    if rebuild_initramfs; then change "rebuilt the initramfs on AHV hardware"; else problem "could not rebuild the initramfs"; fi
    INITRD_LIST=$(list_initrd)
  fi
fi

# ------------------------------------------------------------------ report
PREPARED_AT=""
[ -f "$D/prep.json" ] && PREPARED_AT=$(grep -o '"prepared_at":"[^"]*"' "$D/prep.json" | head -1 | cut -d'"' -f4)
[ "$MODE" = prep ] && PREPARED_AT=$NOW
INITRD_JSON="\"image\":$(js "$(initrd_for "$KVER")"),\"readable\":$(jbool $INITRD_OK)"
for m in $VIRTIO; do v=false; has_mod "$m" && v=true; INITRD_JSON+=",\"$m\":$(jbool $v)"; echo "[info] $m at boot: $v"; done
JSON="{\"version\":2,\"mode\":\"$MODE\",\"time\":\"$NOW\",\"host\":$(js "$(hostname)"),\"os\":$(js "${PRETTY_NAME:-unknown}"),\"kernel\":$(js "$KVER"),\"firmware\":\"$FIRMWARE\",\"on_ahv\":$ON_AHV,\"initramfs\":{$INITRD_JSON},\"fstab_bad\":$(jarr "${FSTAB_BAD[@]}"),\"crypttab_bad\":$(jarr "${CRYPT_BAD[@]}"),\"lvm_devices_file\":$LVMDEV,\"nics\":[$NICS_JSON],\"route\":$(js "$ROUTE"),\"dns\":$(jarr "${DNS[@]}"),\"stratus\":$(jarr "${STRATUS[@]}"),\"prepared_at\":$(js "$PREPARED_AT"),\"changes\":$(jarr "${CHANGES[@]}"),\"problems\":$(jarr "${PROBLEMS[@]}")}"
[ "$MODE" = prep ] && { umask 077; echo "$JSON" > "$D/prep.json"; }
[ "$MODE" = cleanup ] && { umask 077; echo "$JSON" > "$D/cleanup.json"; }
echo "EV2AHV-JSON:$JSON"
[ ${#PROBLEMS[@]} -gt 0 ] && exit 1
exit 0
