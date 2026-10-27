#!/usr/bin/env bash
set -euo pipefail
umask 077
cd /project
IMAGE=/work/wifi-bridge.img
ROOT=/work/root
LOOP=""
LOOP_BOOT=""
cleanup() {
  set +e
  mountpoint -q "$ROOT/dev/pts" && umount "$ROOT/dev/pts"
  mountpoint -q "$ROOT/dev" && umount "$ROOT/dev"
  mountpoint -q "$ROOT/proc" && umount "$ROOT/proc"
  mountpoint -q "$ROOT/sys" && umount "$ROOT/sys"
  mountpoint -q "$ROOT/boot/firmware" && umount "$ROOT/boot/firmware"
  mountpoint -q "$ROOT" && umount "$ROOT"
  [ -z "$LOOP" ] || losetup -d "$LOOP"
  [ -z "$LOOP_BOOT" ] || losetup -d "$LOOP_BOOT"
  [ ! -e /proc/sys/fs/binfmt_misc/wifi-bridge-arm ] || echo -1 > /proc/sys/fs/binfmt_misc/wifi-bridge-arm
}
trap cleanup EXIT
xz -dc image/cache/base.img.xz > "$IMAGE"
# Leave space for apt, runtime and future updates. Expanded to full SD on first boot.
truncate -s +768M "$IMAGE"
parted -s "$IMAGE" resizepart 2 100%
# Offset loop devices avoid udev/partition-node assumptions in Docker VMs.
read -r BOOT_START BOOT_SIZE ROOT_START ROOT_SIZE < <(
  parted -sm "$IMAGE" unit B print | python3 -c '
import sys
parts = {line.split(":")[0]: line.split(":") for line in sys.stdin if line[0:1].isdigit()}
print(*(parts[n][i].rstrip("B") for n in ("1", "2") for i in (1, 3)))'
)
LOOP_BOOT="$(losetup --find --show --offset "$BOOT_START" --sizelimit "$BOOT_SIZE" "$IMAGE")"
LOOP="$(losetup --find --show --offset "$ROOT_START" --sizelimit "$ROOT_SIZE" "$IMAGE")"
e2fsck -pf "$LOOP" || [ "$?" -eq 1 ]
resize2fs "$LOOP"
mkdir -p "$ROOT"
mount "$LOOP" "$ROOT"
mount "$LOOP_BOOT" "$ROOT/boot/firmware"
mount --bind /dev "$ROOT/dev"
mount --bind /dev/pts "$ROOT/dev/pts"
mount -t proc proc "$ROOT/proc"
mount -t sysfs sysfs "$ROOT/sys"
# Explicit interpreter works on Apple Silicon and x86 Linux without changing
# the VM's global binfmt registrations. ARMv6 is provided by the official image.
QEMU="$(command -v qemu-arm-static || command -v qemu-arm)"
install -m 755 "$QEMU" "$ROOT/usr/bin/wifi-bridge-qemu-arm"
# Executables launched by the ARM shell need binfmt. Registration is scoped by
# name and removed after the build, never overwrites an existing interpreter.
mountpoint -q /proc/sys/fs/binfmt_misc || mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc
if [ ! -f /proc/sys/fs/binfmt_misc/wifi-bridge-arm ]; then
  printf ':wifi-bridge-arm:M::\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x28\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:%s:F\n' "$QEMU" > /proc/sys/fs/binfmt_misc/register
fi
cp -L "$ROOT/etc/resolv.conf" /work/resolv.original || true
rm -f "$ROOT/etc/resolv.conf"
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
# QEMU/ARM DNS resolution can fail in a chroot on Docker Desktop/Colima.
# Resolve official mirrors using the builder, and remove these build-only entries.
cp "$ROOT/etc/hosts" /work/hosts.original
for mirror in archive.raspberrypi.com raspbian.raspberrypi.com archive.raspbian.org; do
  getent ahostsv4 "$mirror" | awk -v host="$mirror" '$2=="STREAM" {print $1,host;exit}' >> "$ROOT/etc/hosts"
done

cat > "$ROOT/usr/sbin/policy-rc.d" <<'EOF'
#!/bin/sh
exit 101
EOF
chmod 755 "$ROOT/usr/sbin/policy-rc.d"
chroot "$ROOT" /usr/bin/wifi-bridge-qemu-arm /bin/bash -eu -c '
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::ForceIPv4=true -o APT::Update::Error-Mode=any update
apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends python3 network-manager hostapd dnsmasq-base avahi-daemon nftables conntrack iw rfkill openssl firmware-realtek openssh-server
apt-get clean
rm -rf /var/lib/apt/lists/*
'
install -m 700 image/minimize.sh "$ROOT/tmp/astro-bridge-minimize.sh"
chroot "$ROOT" /usr/bin/wifi-bridge-qemu-arm /bin/bash /tmp/astro-bridge-minimize.sh
rm -f "$ROOT/tmp/astro-bridge-minimize.sh"
bash scripts/install.sh --root "$ROOT" --no-start
install -m 600 scripts/create_console_user.py "$ROOT/tmp/wifi-bridge-console-user.py"
chroot "$ROOT" /usr/bin/wifi-bridge-qemu-arm -cpu arm1176 /usr/bin/python3 /tmp/wifi-bridge-console-user.py
rm -f "$ROOT/tmp/wifi-bridge-console-user.py"
# Verify the actual shipped Python/userspace on the original Zero W ARMv6 CPU.
mkdir -p "$ROOT/tmp/wifi-bridge-tests"
cp tests/test_bridge.py "$ROOT/tmp/wifi-bridge-tests/"
PYTHONPATH=/opt/wifi-bridge chroot "$ROOT" /usr/bin/wifi-bridge-qemu-arm -cpu arm1176 /usr/bin/python3 -m unittest discover -s /tmp/wifi-bridge-tests -v
rm -rf "$ROOT/tmp/wifi-bridge-tests"
# Appliance boot, no cloud provisioning or interactive user wizard.
# Keep the vendor first-boot/resize packages; disable cloud services instead.
mkdir -p "$ROOT/etc/cloud"
touch "$ROOT/etc/cloud/cloud-init.disabled"
systemctl --root="$ROOT" mask userconfig.service userconfig-pi.service cloud-init-local.service cloud-init.service cloud-config.service cloud-final.service
systemctl --root="$ROOT" unmask ssh.service ssh.socket
systemctl --root="$ROOT" enable ssh.service
systemctl --root="$ROOT" disable dhcpcd.service 2>/dev/null || true
# NetworkManager exclusively owns interfaces; netplan cloud-init is disabled.
find "$ROOT/etc/netplan" -maxdepth 1 -type f -name '*.yaml' -delete 2>/dev/null || true
mkdir -p "$ROOT/etc/NetworkManager/conf.d"
cat > "$ROOT/etc/NetworkManager/conf.d/80-wifi-bridge.conf" <<'EOF'
[main]
no-auto-default=*
[device]
wifi.scan-rand-mac-address=no
EOF
cat > "$ROOT/etc/avahi/avahi-daemon.conf" <<'EOF'
[server]
host-name=wifi-bridge
use-ipv6=no
[wide-area]
enable-wide-area=no
[publish]
publish-workstation=no
[reflector]
enable-reflector=no
EOF
# Prevent RF-kill persistent state from defeating the setup AP.
systemctl --root="$ROOT" enable NetworkManager avahi-daemon wifi-bridge
mkdir -p "$ROOT/etc/systemd/system/NetworkManager.service.d"
cat > "$ROOT/etc/systemd/system/NetworkManager.service.d/wifi-bridge.conf" <<'EOF'
[Service]
ExecStartPre=/usr/sbin/rfkill unblock wifi
EOF
echo JP > "$ROOT/etc/wifi-bridge/country"
systemd-analyze --root="$ROOT" verify wifi-bridge.service wifi-bridge-ap.service wifi-bridge-dhcp.service
python3 image/verify-root.py "$ROOT"
rm -f "$ROOT/usr/sbin/policy-rc.d" "$ROOT/usr/bin/wifi-bridge-qemu-arm"
rm -f "$ROOT/etc/resolv.conf"
ln -s /run/NetworkManager/resolv.conf "$ROOT/etc/resolv.conf"
cp /work/hosts.original "$ROOT/etc/hosts"
cp "$ROOT/boot/firmware/wifi-bridge-access.txt" dist/wifi-bridge-access.txt.partial
chmod 600 dist/wifi-bridge-access.txt.partial
python3 image/manifest.py "$ROOT"
sync
cleanup
LOOP=""
LOOP_BOOT=""
[ ! -e /proc/sys/fs/binfmt_misc/wifi-bridge-arm ] || echo -1 > /proc/sys/fs/binfmt_misc/wifi-bridge-arm
xz -T2 -3 -c "$IMAGE" > dist/astro-bridge-0.6.0-armhf.img.xz.partial
mv dist/astro-bridge-0.6.0-armhf.img.xz.partial dist/astro-bridge-0.6.0-armhf.img.xz
mv dist/wifi-bridge-access.txt.partial dist/wifi-bridge-access.txt
mv dist/build-manifest.json.partial dist/build-manifest.json
cd dist
sha256sum astro-bridge-0.6.0-armhf.img.xz > SHA256SUMS
rm -f "$IMAGE"
