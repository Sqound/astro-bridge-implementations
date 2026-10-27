#!/usr/bin/env bash
# On a dedicated Raspberry Pi: sudo ./scripts/install.sh
# Offline image: ./scripts/install.sh --root /mnt/image --no-start
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_ROOT=""
START=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) TARGET_ROOT="$2"; shift 2 ;;
    --no-start) START=0; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ "$(uname -s)" != Linux ] || [ "$(id -u)" != 0 ]; then
  echo "Installation requires Linux and root. On macOS: python3 -m wifi_bridge --demo" >&2
  exit 1
fi
if [ -n "$TARGET_ROOT" ]; then
  TARGET_ROOT="$(realpath "$TARGET_ROOT")"
  [ "$TARGET_ROOT" != / ] && [ -f "$TARGET_ROOT/etc/os-release" ] || { echo "Invalid image root" >&2; exit 1; }
  START=0
fi
if [ -z "$TARGET_ROOT" ]; then
  apt-get update
  apt-get install -y --no-install-recommends python3 network-manager hostapd dnsmasq-base avahi-daemon nftables conntrack iw rfkill openssl
fi
install -d -m 755 "$TARGET_ROOT/opt/wifi-bridge/wifi_bridge" "$TARGET_ROOT/opt/wifi-bridge/wifi_bridge/static"
install -m 644 "$PROJECT_DIR"/wifi_bridge/*.py "$TARGET_ROOT/opt/wifi-bridge/wifi_bridge/"
install -m 644 "$PROJECT_DIR"/wifi_bridge/static/* "$TARGET_ROOT/opt/wifi-bridge/wifi_bridge/static/"
install -d -m 700 "$TARGET_ROOT/etc/wifi-bridge" "$TARGET_ROOT/var/lib/wifi-bridge"
install -d -m 755 "$TARGET_ROOT/etc/NetworkManager/system-connections" "$TARGET_ROOT/etc/avahi/services" "$TARGET_ROOT/etc/systemd/system" "$TARGET_ROOT/etc/sysctl.d"
install -m 644 "$PROJECT_DIR/systemd/wifi-bridge.service" "$PROJECT_DIR/systemd/wifi-bridge-ap.service" "$PROJECT_DIR/systemd/wifi-bridge-dhcp.service" "$PROJECT_DIR/systemd/wifi-bridge-password.service" "$TARGET_ROOT/etc/systemd/system/"
install -m 644 "$PROJECT_DIR/systemd/wifi-bridge-http.service" "$TARGET_ROOT/etc/avahi/services/"
echo 'net.ipv4.ip_forward=1' > "$TARGET_ROOT/etc/sysctl.d/80-wifi-bridge.conf"
BOOT_DIR="$TARGET_ROOT/boot/firmware"
[ -d "$BOOT_DIR" ] || BOOT_DIR="$TARGET_ROOT/boot"
mkdir -p "$BOOT_DIR"
# Access code is deliberately outside source control; never print it to logs.
python3 "$PROJECT_DIR/scripts/bootstrap.py" "$TARGET_ROOT/etc/wifi-bridge/bootstrap.json" "$BOOT_DIR/wifi-bridge-access.txt"
# Create the documented account on a fresh dedicated install, but never reset an
# existing account during an upgrade.
if [ -z "$TARGET_ROOT" ] && ! id bridge >/dev/null 2>&1; then
  python3 "$PROJECT_DIR/scripts/create_console_user.py"
fi
install -d -m 755 "$TARGET_ROOT/usr/local/lib/astro-bridge"
printf '%s\n' 2 > "$TARGET_ROOT/etc/wifi-bridge/updater-version"
install -m 644 "$PROJECT_DIR/scripts/password.py" "$TARGET_ROOT/usr/local/lib/astro-bridge/password.py"
install -m 644 "$PROJECT_DIR/scripts/ota.py" "$TARGET_ROOT/usr/local/lib/astro-bridge/ota.py"
install -m 644 "$PROJECT_DIR"/systemd/astro-bridge-update-* "$TARGET_ROOT/etc/systemd/system/"
install -d -m 755 "$TARGET_ROOT/etc/systemd/system/wifi-bridge.service.d"
printf '[Unit]\nWants=astro-bridge-update-recover.service\nAfter=astro-bridge-update-recover.service\n' > "$TARGET_ROOT/etc/systemd/system/wifi-bridge.service.d/updates.conf"
if [ -n "$TARGET_ROOT" ]; then
  systemctl --root="$TARGET_ROOT" enable astro-bridge-update-recover.service astro-bridge-update-check.timer
  systemctl --root="$TARGET_ROOT" enable NetworkManager avahi-daemon wifi-bridge
else
  systemctl enable astro-bridge-update-recover.service astro-bridge-update-check.timer
  systemctl enable NetworkManager avahi-daemon wifi-bridge
  if [ "$START" = 1 ]; then
    rfkill unblock wifi
    sysctl -w net.ipv4.ip_forward=1
    systemctl daemon-reload
    systemctl start NetworkManager avahi-daemon
    systemctl start astro-bridge-update-check.timer
    systemctl restart wifi-bridge
  fi
fi
echo "Installation complete. Initial access code: $BOOT_DIR/wifi-bridge-access.txt"
