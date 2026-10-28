# Native Linux installation

[Documentation](README.md) / Native Linux

Use a dedicated recent Debian/Ubuntu systemd host, Python 3.11+, NetworkManager and suitable radios. Native activity collection requires **nftables 1.1.3+**; the installer checks it. Debian 13 is a suitable baseline.

## Install

```sh
python3 tools/core-artifact.py fetch
sudo python3 linux/host-install.py --mode native \
  --wheel build/core/astro_bridge_core-1.0.1-py3-none-any.whl
# After reviewing the plan:
sudo python3 linux/host-install.py --mode native --apply \
  --wheel build/core/astro_bridge_core-1.0.1-py3-none-any.whl
```

![Private Wi-Fi and hotspot configuration](images/networks.png)

## What the installer changes

| Area | Result |
| --- | --- |
| Application | Isolated `/opt/astro-bridge-1.0.1` virtual environment |
| Networking | Required host packages, app-owned AP/DHCP units, IPv4 forwarding |
| Initial access | Private `/etc/wifi-bridge/bootstrap.json` with a random code |
| Persistent state | `/var/lib/wifi-bridge` |
| Certificates | Fixed helper and private persistent certificate state |
| Activity tools | Pinned Netfilter sources compiled into the app prefix |
| Service | `wifi-bridge` systemd service |

Compiler/development prerequisites are installed for native Netfilter builds. The distribution's conntrack binary/libraries are not replaced: `ASTRO_BRIDGE_CONNTRACK` selects the private executable. Matching source archives and licenses are retained in its installation prefix.

The installer preserves existing NM profiles, does not create a Linux login account, and does not globally rename the host. Configure local DNS/mDNS separately for the chosen `.local` HTTPS name, or use supported management addresses. Additional HTTP hostnames must be explicit `--allow-host` entries.

## Management

```sh
sudo systemctl status wifi-bridge
sudo journalctl -u wifi-bridge
sudo cat /etc/wifi-bridge/bootstrap.json
```

Keep access codes out of logs and issue reports. Host reboot/system-account controls are not exposed as appliance features in native mode. Upgrades use the [deployment workflow](updates.md); full physical host-radio acceptance remains separate from helper tests.
