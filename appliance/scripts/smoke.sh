#!/bin/bash
# Smoke boot of build/ev2ahv.raw in qemu TCG (no KVM) inside a container. A throw-away copy of the image gets a
# test unit injected (the shipped image is untouched). Results arrive on the serial console -> build/smoke.log
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD; S=build/smoke; mkdir -p $S
docker build -q -t ev2ahv-smoke appliance/builder-smoke >/dev/null
rm -f $S/test.raw $S/serial.log; cp --sparse=always build/ev2ahv.raw $S/test.raw
cp appliance/scripts/smoke-inner.sh $S/smoke-inner.sh
cat > $S/inject.sh <<'I'
set -e
truncate -s 25G /s/test.raw
L=$(losetup -f --show -P /s/test.raw); n=$(basename $L)
ma=$(cut -d: -f1 /sys/block/$n/${n}p3/dev); mi=$(cut -d: -f2 /sys/block/$n/${n}p3/dev); mknod /dev/${n}p3 b $ma $mi
mount -o nouuid /dev/${n}p3 /mnt
install -m 0755 /s/smoke-inner.sh /mnt/usr/local/sbin/smoke-inner
cat > /mnt/etc/systemd/system/smoke.service <<U
[Unit]
After=multi-user.target cloud-final.service
[Service]
Type=simple
ExecStart=/usr/local/sbin/smoke-inner
StandardOutput=tty
TTYPath=/dev/ttyS0
[Install]
WantedBy=multi-user.target
U
ln -sf /etc/systemd/system/smoke.service /mnt/etc/systemd/system/multi-user.target.wants/smoke.service
chcon -t bin_t /mnt/usr/local/sbin/smoke-inner; chcon -t systemd_unit_file_t /mnt/etc/systemd/system/smoke.service
umount /mnt; losetup -d $L
I
docker run --rm --privileged -v $REPO/$S:/s ev2ahv-smoke bash /s/inject.sh
docker run --rm --name ev2ahv-smoke -v $REPO/$S:/s ev2ahv-smoke bash -c '
 BIOS=${SMOKE_UEFI:+-drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd}
 timeout '${SMOKE_TIMEOUT:-1500}' /usr/libexec/qemu-kvm -machine q35 -accel tcg -cpu max -m 2048 -smp 2 -nographic -serial file:/s/serial.log -monitor none \
  $BIOS -drive file=/s/test.raw,format=raw,if=none,id=d0 -device virtio-scsi-pci,id=sc -device scsi-hd,drive=d0 \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 || true' &
QP=$!
for i in $(seq 1 400); do sleep 5; grep -aq '===SMOKE-END' $S/serial.log 2>/dev/null && break; done
docker kill ev2ahv-smoke >/dev/null 2>&1 || true; wait $QP 2>/dev/null || true
tr -d '\r' < $S/serial.log | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' > build/smoke.log
sed -n '/===SMOKE-BEGIN/,/===SMOKE-END/p' build/smoke.log
