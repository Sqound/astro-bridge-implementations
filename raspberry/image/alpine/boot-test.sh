#!/bin/bash
# Boot an expendable copy, never mutate the release image or any physical disk.
set -euo pipefail
cd /project
cp --sparse=always /work/astro-bridge-alpine.img /work/alpine-boot-test.img
truncate -s 2G /work/alpine-boot-test.img
mkdir -p /work/test-root /work/test-boot
# Same 8 MiB / 512 MiB boot geometry as the known-working Pi OS image.
R="$(losetup --find --show --offset 545259520 /work/alpine-boot-test.img)"
B="$(losetup --find --show --offset 8388608 --sizelimit 536870912 /work/alpine-boot-test.img)"
cleanup(){ umount /work/test-root 2>/dev/null || true; umount /work/test-boot 2>/dev/null || true; losetup -d "$R" 2>/dev/null || true; losetup -d "$B" 2>/dev/null || true; }
trap cleanup EXIT
mount "$R" /work/test-root
mount "$B" /work/test-boot
cp /work/test-boot/boot/vmlinuz-rpi /work/test-vmlinuz
cp /work/test-boot/boot/initramfs-rpi /work/test-initramfs
# QEMU's raspi0 board models the BCM2708-compatible DTB. The physical image
# retains the official BCM2835 Zero W DTB, checked separately by verify.py.
cp /work/test-boot/bcm2708-rpi-zero-w.dtb /work/test-dtb
mkdir -p /work/test-root/etc/local.d
cat > /work/test-root/etc/local.d/zz-boot-proof.start <<'PROOF'
#!/bin/sh
{
 cat /etc/alpine-release
 rc-status
 findmnt /
 nmcli device status
 echo "IPv4 forwarding: $(cat /proc/sys/net/ipv4/ip_forward)"
 if rc-service networkmanager status && rc-service sshd status &&
    [ "$(cat /proc/sys/net/ipv4/ip_forward)" = 1 ] &&
    nft add table ip astro_boot_test && nft delete table ip astro_boot_test; then
   echo ASTRO_ALPINE_BOOT_PASS
 else
   echo ASTRO_ALPINE_BOOT_CHECK_FAILED
 fi
} >/var/lib/wifi-bridge/qemu-boot-proof.txt 2>&1
sync
cat /var/lib/wifi-bridge/qemu-boot-proof.txt >/dev/kmsg
/sbin/poweroff -f
PROOF
chmod 755 /work/test-root/etc/local.d/zz-boot-proof.start
ln -sf /etc/init.d/local /work/test-root/etc/runlevels/default/local
cleanup
trap - EXIT
# Firmware normally enables this UART from config.txt. QEMU loads a DTB directly,
# so make the equivalent console-only adjustment on the expendable DTB copy.
fdtput -t s /work/test-dtb /soc/serial@7e201000 status okay
# QEMU does not emulate the Pi's power-domain block used by this vendor driver.
# Disable it only in the VM DTB; the physical image retains the official DTB.
fdtput -t s /work/test-dtb /soc/watchdog@7e100000 status disabled
set +e
qemu-system-arm -M raspi0 -m 512M -nographic -no-reboot \
 -kernel /work/test-vmlinuz -initrd /work/test-initramfs -dtb /work/test-dtb \
 -append 'earlycon=pl011,mmio32,0x20201000 console=ttyAMA0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 modules=sd-mod,usb-storage,mmc_block,ext4 rootwait rw' \
 -drive file=/work/alpine-boot-test.img,format=raw,if=sd \
 > /project/artifacts/alpine-full-boot.log 2>&1 &
pid=$!
for attempt in $(seq 1 120); do
 sleep 1
 if grep -aqE "ASTRO_ALPINE_BOOT_PASS|System halted instead" /project/artifacts/alpine-full-boot.log; then break; fi
 kill -0 "$pid" 2>/dev/null || break
done
kill "$pid" 2>/dev/null
wait "$pid"
set -e
R="$(losetup --find --show --offset 545259520 /work/alpine-boot-test.img)"
mount -o ro,noload "$R" /work/test-root
cp /work/test-root/var/lib/wifi-bridge/qemu-boot-proof.txt /project/artifacts/alpine-boot-proof.txt
umount /work/test-root
losetup -d "$R"
grep -q ASTRO_ALPINE_BOOT_PASS /project/artifacts/alpine-boot-proof.txt
cat /project/artifacts/alpine-boot-proof.txt
echo 'PASS: full ARMv6 kernel, SD rootfs and OpenRC boot on an expendable QEMU copy'
