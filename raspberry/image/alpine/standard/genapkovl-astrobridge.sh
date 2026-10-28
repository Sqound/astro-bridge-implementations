#!/bin/sh -eu
set -eu

# This script is called by Alpine's mkimage tooling.  An apkovl is unpacked
# into the RAM root on boot; packages named in world are included in the same
# official boot archive.  The firmware-owned files remain untouched.
# Generated public directories must not inherit a restrictive builder umask.
# The mktemp root stays private until the payload is complete.
umask 022
HOSTNAME="$1"
PROJECT="${ASTRO_BRIDGE_PROJECT:?ASTRO_BRIDGE_PROJECT is required}"
tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT


mkdir -p "$tmp/opt/wifi-bridge/wifi_bridge/static" \
  "$tmp/etc/wifi-bridge" "$tmp/etc/NetworkManager/conf.d" \
  "$tmp/etc/NetworkManager/system-connections" "$tmp/etc/avahi/services" \
  "$tmp/usr/libexec" "$tmp/usr/sbin" "$tmp/etc/conf.d" "$tmp/etc/periodic/hourly" "$tmp/etc/periodic/daily" "$tmp/etc/ssh/sshd_config.d" "$tmp/etc/sudoers.d" "$tmp/etc/init.d" "$tmp/etc/sysctl.d" "$tmp/etc/apk" "$tmp/etc/runlevels/sysinit" \
  "$tmp/etc/runlevels/boot" "$tmp/etc/runlevels/default" \
  "$tmp/etc/runlevels/shutdown" "$tmp/var/lib/wifi-bridge"
# These directories can hold credentials or runtime configuration. Set their
# modes before writing files, rather than changing the complete tree recursively.
chmod 0700 "$tmp/etc/wifi-bridge" \
  "$tmp/etc/NetworkManager/system-connections" "$tmp/var/lib/wifi-bridge"

cp "$PROJECT"/wifi_bridge/*.py "$tmp/opt/wifi-bridge/wifi_bridge/"
cp "$PROJECT"/wifi_bridge/static/* "$tmp/opt/wifi-bridge/wifi_bridge/static/"
mkdir -p "$tmp/usr/share/doc/astro-bridge"
install -m 644 "$PROJECT/LICENSE" "$tmp/usr/share/doc/astro-bridge/LICENSE"
cat > "$tmp/usr/share/doc/astro-bridge/SOURCES.txt" <<'SOURCES'
Astro Bridge original source is MIT licensed. OS packages and firmware retain
their upstream terms. Common license texts are installed by Alpine's spdx-licenses-text
package. The matching release provides exact package build recipes and upstream
source archives alongside this image:
https://github.com/Sqound/astro-bridge-implementations/releases
SOURCES
python3 "$PROJECT/image/alpine/standard/candidate.py" stamp "$tmp/opt/wifi-bridge/wifi_bridge" >/dev/null
install -m 755 "$PROJECT"/scripts/ota.py "$tmp/usr/libexec/astro-bridge-ota"
install -m 755 "$PROJECT"/image/alpine/openrc/wifi-bridge* "$tmp/etc/init.d/"
install -m 755 "$PROJECT"/image/alpine/openrc/astro-bridge-provision "$tmp/etc/init.d/"
python3 "$PROJECT/scripts/install-push-runtime.py" --vapid-only "$tmp/usr/lib/astro-bridge-push/vapid"
install -m 755 "$PROJECT"/scripts/tls.py "$tmp/usr/libexec/astro-bridge-tls"
install -m 755 "$PROJECT"/image/alpine/persistence.py "$tmp/usr/libexec/astro-bridge-persist"
install -m 755 "$PROJECT"/image/alpine/standard/clock-checkpoint "$tmp/etc/periodic/hourly/astro-bridge-clock"
install -m 644 "$PROJECT"/systemd/wifi-bridge-http.service "$tmp/etc/avahi/services/"

install -m 755 "$PROJECT"/image/alpine/standard/update-check "$tmp/etc/periodic/daily/astro-bridge-updates"
printf '%s\n' 2 > "$tmp/etc/wifi-bridge/updater-version"
printf '%s\n' 2 > "$tmp/etc/wifi-bridge/tls-helper-version"
printf '%s\n' diskless > "$tmp/etc/wifi-bridge/diskless"
printf '%s\n' 'rc_need="astro-bridge-provision"' 'sshd_disable_keygen="yes"' > "$tmp/etc/conf.d/sshd"
printf '%s\n' 'rc_need="modules"' > "$tmp/etc/conf.d/sysctl"
printf '%s\n' 'PermitRootLogin no' 'PasswordAuthentication yes' > "$tmp/etc/ssh/sshd_config.d/astro-bridge.conf"
ln -s /bin/busybox "$tmp/usr/sbin/rfkill"
printf '%s\n' openrc > "$tmp/etc/wifi-bridge/init-system"
printf '%s\n' '{"password":"12345678"}' > "$tmp/etc/wifi-bridge/bootstrap.json"
chmod 600 "$tmp/etc/wifi-bridge/bootstrap.json"
printf '%s\n' '127.0.0.1 localhost astro-bridge-alpine' '::1 localhost' > "$tmp/etc/hosts"
printf '%s\n' astro-bridge-alpine > "$tmp/etc/hostname"
printf '%s\n' 'net.ipv4.ip_forward=1' > "$tmp/etc/sysctl.d/80-wifi-bridge.conf"
printf '%s\n' 'brcmfmac' 'ipv6' > "$tmp/etc/modules"

printf '%s\n' \
  '[main]' 'plugins=keyfile' 'no-auto-default=*' '[device]' \
  'wifi.scan-rand-mac-address=no' '[wifi]' 'backend=wpa_supplicant' \
  > "$tmp/etc/NetworkManager/conf.d/80-wifi-bridge.conf"

printf '%s\n' \
  'LABEL=ASTRO_DATA /var/lib/wifi-bridge ext4 defaults,noatime 0 2' \
  'tmpfs /tmp tmpfs nosuid,nodev,size=32M 0 0' > "$tmp/etc/fstab"

printf '%s\n' \
  'alpine-base' 'python3' 'networkmanager' 'networkmanager-cli' \
  'networkmanager-wifi' 'networkmanager-openrc' 'wpa_supplicant' \
  'chrony' 'chrony-openrc' 'dbus' 'dbus-openrc' 'hostapd' 'dnsmasq' \
  'avahi' 'avahi-openrc' 'openssh' 'openssh-server-common-openrc' \
  'nftables' 'conntrack-tools' 'iw' 'iproute2' 'wireless-regdb' 'linux-firmware-brcm' 'linux-firmware-rtw88' 'e2fsprogs-extra' \
  'eudev' 'eudev-openrc' 'usb-modeswitch' 'usb-modeswitch-udev' 'shadow' 'sudo' 'ca-certificates' 'openssl' 'py3-cryptography' 'py3-http-ece' \
  > "$tmp/etc/apk/world"

# modloop exposes /lib/modules before the Wi-Fi driver and rfkill are used.
for service in devfs dmesg udev udev-trigger modloop hwdrivers; do
  ln -s /etc/init.d/"$service" "$tmp/etc/runlevels/sysinit/$service"
done
for service in modules sysctl hostname bootmisc localmount; do
  ln -s /etc/init.d/"$service" "$tmp/etc/runlevels/boot/$service"
done
for service in dbus networkmanager avahi-daemon sshd chronyd syslog crond astro-bridge-provision wifi-bridge; do
  ln -s /etc/init.d/"$service" "$tmp/etc/runlevels/default/$service"
done
for service in mount-ro killprocs savecache; do
  ln -s /etc/init.d/"$service" "$tmp/etc/runlevels/shutdown/$service"
done

# tar records the "." entry and Alpine extracts it onto /. A mktemp root left
# at 0700 prevents non-root users from traversing / or executing their shell.
chmod 0755 "$tmp"
# Avoid macOS AppleDouble metadata in portable overlay/test archives.
COPYFILE_DISABLE=1 tar -c -C "$tmp" . | gzip -9n > "$HOSTNAME.apkovl.tar.gz"
