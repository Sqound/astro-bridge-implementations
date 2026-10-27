#!/bin/bash
# Build only inside Docker's isolated VM. No physical disk is passed through.
set -euo pipefail
umask 077
cd /project
IMAGE=/work/astro-bridge-alpine.img
ROOT=/work/root
B=""; R=""
cleanup(){
 set +e
 for path in dev/pts dev proc sys boot ""; do mountpoint -q "$ROOT/$path" && umount "$ROOT/$path"; done
 [ -z "$R" ] || losetup -d "$R"
 [ -z "$B" ] || losetup -d "$B"
 [ ! -e /proc/sys/fs/binfmt_misc/astro-alpine-arm ] || echo -1 > /proc/sys/fs/binfmt_misc/astro-alpine-arm
}
trap cleanup EXIT
# Match the boot geometry of the known-working Raspberry Pi OS image for the
# original Zero W.  Its legacy firmware starts from an MBR FAT partition at
# 8 MiB (rather than the common 1 MiB alignment), followed by a 512 MiB boot
# volume.  This is an independent physical-Pi test image, not a replacement
# for the smaller experimental layout.
truncate -s 1536M "$IMAGE"
parted -s "$IMAGE" mklabel msdos mkpart primary fat32 8MiB 520MiB mkpart primary ext4 520MiB 100% set 1 boot on
B="$(losetup --find --show --offset 8388608 --sizelimit 536870912 "$IMAGE")"
R="$(losetup --find --show --offset 545259520 "$IMAGE")"
mkfs.vfat -n ASTRO_BOOT "$B"
mkfs.ext4 -q -F -L ASTRO_ROOT "$R"
mkdir -p "$ROOT"
mount "$R" "$ROOT"
tar -xzf image/cache/alpine/rootfs.tar.gz -C "$ROOT"
mkdir -p "$ROOT/boot"
mount "$B" "$ROOT/boot"
# Firmware, DTBs and the official Raspberry Pi boot layout from the
# checksum-pinned Alpine distribution. Keep boot/ here: legacy Pi firmware
# resolves its kernel and initramfs from the same paths as the official image.
tar -xzf image/cache/alpine/boot.tar.gz -C "$ROOT/boot" --exclude='./apks'
mount --bind /dev "$ROOT/dev"
mount --bind /dev/pts "$ROOT/dev/pts"
mount -t proc proc "$ROOT/proc"
mount -t sysfs sysfs "$ROOT/sys"
QEMU="$(command -v qemu-arm-static || command -v qemu-arm)"
install -m 755 "$QEMU" "$ROOT/usr/bin/astro-qemu-arm"
mountpoint -q /proc/sys/fs/binfmt_misc || mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc
printf ':astro-alpine-arm:M::\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x28\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:%s:F\n' "$QEMU" > /proc/sys/fs/binfmt_misc/register
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
cp "$ROOT/etc/hosts" /work/hosts.original
getent ahostsv4 dl-cdn.alpinelinux.org | awk '$2=="STREAM"{print $1,"dl-cdn.alpinelinux.org";exit}' >> "$ROOT/etc/hosts"
printf 'https://dl-cdn.alpinelinux.org/alpine/v3.24/main\nhttps://dl-cdn.alpinelinux.org/alpine/v3.24/community\n' > "$ROOT/etc/apk/repositories"
mkdir -p "$ROOT/etc/mkinitfs"
echo 'features="base mmc usb ext4"' > "$ROOT/etc/mkinitfs/mkinitfs.conf"
chroot "$ROOT" /usr/bin/astro-qemu-arm -cpu arm1176 /bin/sh -eu -c '
apk add --no-cache alpine-base openrc linux-rpi raspberrypi-bootloader mkinitfs \
 python3 networkmanager networkmanager-cli networkmanager-wifi networkmanager-openrc wpa_supplicant \
 chrony chrony-openrc dbus dbus-openrc hostapd dnsmasq avahi avahi-openrc openssh openssh-server-common-openrc \
 nftables conntrack-tools iw iproute2 wireless-regdb util-linux util-linux-misc sfdisk partx findmnt e2fsprogs-extra \
 linux-firmware-rtw88 eudev eudev-openrc shadow sudo ca-certificates openssl
'
install -d -m 755 "$ROOT/opt/wifi-bridge/wifi_bridge/static" "$ROOT/etc/wifi-bridge" "$ROOT/etc/NetworkManager/conf.d" "$ROOT/etc/NetworkManager/system-connections" "$ROOT/etc/avahi/services" "$ROOT/etc/sudoers.d"
install -d -m 700 "$ROOT/var/lib/wifi-bridge"
cp wifi_bridge/*.py "$ROOT/opt/wifi-bridge/wifi_bridge/"
cp wifi_bridge/static/* "$ROOT/opt/wifi-bridge/wifi_bridge/static/"
install -m 755 image/alpine/openrc/* "$ROOT/etc/init.d/"
install -m 644 systemd/wifi-bridge-http.service "$ROOT/etc/avahi/services/"
echo openrc > "$ROOT/etc/wifi-bridge/init-system"
echo astro-bridge-alpine > "$ROOT/etc/hostname"
printf '127.0.0.1 localhost astro-bridge-alpine\n::1 localhost\n' > "$ROOT/etc/hosts"
cat > "$ROOT/etc/NetworkManager/conf.d/80-wifi-bridge.conf" <<'CONF'
[main]
plugins=keyfile
no-auto-default=*
[device]
wifi.scan-rand-mac-address=no
[wifi]
backend=wpa_supplicant
CONF
# Use the root disk rather than a RAM overlay; state survives power cycles.
cat > "$ROOT/etc/fstab" <<'CONF'
LABEL=ASTRO_ROOT / ext4 defaults,noatime 0 1
LABEL=ASTRO_BOOT /boot vfat defaults,noatime 0 2
tmpfs /tmp tmpfs nosuid,nodev,size=32M 0 0
CONF
cat > "$ROOT/boot/config.txt" <<'CONF'
# Experimental Astro Bridge for the original Raspberry Pi Zero W / ARMv6.
kernel=boot/vmlinuz-rpi
initramfs boot/initramfs-rpi
arm_64bit=0
enable_uart=1
gpu_mem=16
dtoverlay=disable-bt
CONF
printf 'console=tty1 console=ttyAMA0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 modules=sd-mod,usb-storage,mmc_block,ext4 rootwait rw\n' > "$ROOT/boot/cmdline.txt"
mkdir -p "$ROOT/etc/sysctl.d"
echo 'net.ipv4.ip_forward=1' > "$ROOT/etc/sysctl.d/80-wifi-bridge.conf"
echo brcmfmac >> "$ROOT/etc/modules"
python3 scripts/bootstrap.py "$ROOT/etc/wifi-bridge/bootstrap.json" "$ROOT/boot/wifi-bridge-access.txt"
# Console setup runs entirely in the emulated target, with secrets on stdin.
cp image/alpine/provision.py "$ROOT/tmp/provision.py"
chroot "$ROOT" /usr/bin/astro-qemu-arm -cpu arm1176 /usr/bin/python3 /tmp/provision.py
rm "$ROOT/tmp/provision.py"
chroot "$ROOT" /usr/bin/astro-qemu-arm -cpu arm1176 /bin/sh -eu -c '
for svc in devfs dmesg udev udev-trigger; do rc-update add "$svc" sysinit; done
for svc in hwclock modules sysctl hostname bootmisc localmount; do rc-update add "$svc" boot; done
for svc in dbus networkmanager avahi-daemon sshd chronyd syslog crond astro-bridge-grow-root wifi-bridge; do rc-update add "$svc" default; done
for svc in mount-ro killprocs savecache; do rc-update add "$svc" shutdown; done
kernel="$(ls /lib/modules | head -1)"
mkinitfs -o /boot/initramfs-rpi "$kernel"
'
# linux-rpi installs its current kernel at the FAT root. Move both current
# artifacts into the layout used by Alpine's official Zero W boot config.
mkdir -p "$ROOT/boot/boot"
mv "$ROOT/boot/vmlinuz-rpi" "$ROOT/boot/boot/vmlinuz-rpi"
mv "$ROOT/boot/initramfs-rpi" "$ROOT/boot/boot/initramfs-rpi"
mkdir -p "$ROOT/tmp/tests"
cp tests/test_bridge.py tests/test_init_system.py "$ROOT/tmp/tests/"
PYTHONPATH=/opt/wifi-bridge chroot "$ROOT" /usr/bin/astro-qemu-arm -cpu arm1176 /usr/bin/python3 -m unittest discover -s /tmp/tests -v
rm -rf "$ROOT/tmp/tests" "$ROOT/var/cache/apk/"* "$ROOT/tmp/"* "$ROOT/boot/apks" "$ROOT/boot/modloop"*
python3 image/alpine/verify.py "$ROOT"
# Verify module and firmware inclusion, not just package installation success.
find "$ROOT/lib/modules" -iname '*8821cu*' -o -iname '*brcmfmac*'
mkdir -p dist
python3 image/alpine/manifest.py "$ROOT"
rm "$ROOT/usr/bin/astro-qemu-arm"
printf 'nameserver 1.1.1.1\n' > "$ROOT/etc/resolv.conf"
sync
cleanup
B=""; R=""
trap - EXIT
xz -T2 -3 -c "$IMAGE" > dist/astro-bridge-0.6.0-alpine-armhf-pios-layout-test.img.xz.partial
mv dist/astro-bridge-0.6.0-alpine-armhf-pios-layout-test.img.xz.partial dist/astro-bridge-0.6.0-alpine-armhf-pios-layout-test.img.xz
cd dist
sha256sum astro-bridge-0.6.0-alpine-armhf-pios-layout-test.img.xz > ALPINE-PIOS-LAYOUT-TEST-SHA256SUMS
