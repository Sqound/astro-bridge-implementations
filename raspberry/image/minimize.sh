#!/bin/bash
# Run only inside the disposable appliance root, never on a development host.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# Keep Wi-Fi firmware, regulatory data, NetworkManager, SSH and Python intact.
remove=()
for package in bluez pi-bluetooth modemmanager udisks2 alsa-utils; do
  if [ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" = installed ]; then
    # Vendor meta-packages can depend on apparently optional components. Never
    # sacrifice first-boot, firmware or network support for a few megabytes.
    plan="$(apt-get -s purge "$package")"
    if echo "$plan" | awk '$1=="Remv" || $1=="Purg" {print $2}' | grep -Eq '^(raspberrypi-|raspi-config|raspi-firmware|linux-image|network-manager|openssh-server|firmware-)'; then
      echo "Keeping $package: removal would affect protected appliance packages"
    else
      remove+=("$package")
    fi
  fi
done
[ "${#remove[@]}" -eq 0 ] || apt-get purge -y "${remove[@]}"
# Manual pages and GNU info are not part of the appliance runtime. Copyright
# notices and package license documentation are deliberately retained.
rm -rf /usr/share/man/* /usr/share/info/*
systemctl disable bluetooth.service 2>/dev/null || true
apt-get clean
rm -rf /var/lib/apt/lists/*
# Bound writes and logs on small SD cards; persistent configuration stays intact.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/astro-bridge.conf <<'CONF'
[Journal]
SystemMaxUse=16M
RuntimeMaxUse=8M
MaxRetentionSec=7day
CONF
