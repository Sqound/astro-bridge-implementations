"""Checks the assembled rootfs, not just the source tree. Never emits secrets."""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
project = Path(__file__).resolve().parents[1]
for source in (project / "wifi_bridge").rglob("*"):
    if source.is_file() and "__pycache__" not in source.parts:
        target = root / "opt/wifi-bridge" / source.relative_to(project)
        assert target.read_bytes() == source.read_bytes(), "Deployed source differs: " + source.name
unit = root / "etc/systemd/system/multi-user.target.wants/wifi-bridge.service"
assert unit.is_symlink() and unit.readlink() == Path("/etc/systemd/system/wifi-bridge.service")
for name in ("wifi-bridge-ap.service", "wifi-bridge-dhcp.service", "wifi-bridge-password.service"):
    assert (root / "etc/systemd/system" / name).read_bytes() == (project / "systemd" / name).read_bytes()
for name in ("userconfig.service",):
    assert (root / "etc/systemd/system" / name).readlink() == Path("/dev/null")
assert (root / "etc/cloud/cloud-init.disabled").exists()
assert (root / "etc/systemd/system/multi-user.target.wants/ssh.service").is_symlink()
assert (root / "etc/systemd/system/timers.target.wants/astro-bridge-update-check.timer").is_symlink()
assert (root / "usr/local/lib/astro-bridge/ota.py").read_bytes() == (project / "wifi_bridge/ota.py").read_bytes()
assert not (root / "var/lib/wifi-bridge/updates/preferences.json").exists()
assert (root / "etc/systemd/journald.conf.d/astro-bridge.conf").exists()
assert (root / "usr/lib/systemd/system/rpi-resize.service").exists(), "Vendor first-boot partition expansion must be retained"
bootstrap = root / "etc/wifi-bridge/bootstrap.json"
assert bootstrap.stat().st_mode & 0o777 == 0o600
secret = json.loads(bootstrap.read_text())["password"]
assert secret == "12345678"
assert secret in (root / "boot/firmware/wifi-bridge-access.txt").read_text()
assert not (root / "var/lib/wifi-bridge/config.json").exists(), "The image already contains private settings"
assert (root / "var/lib/wifi-bridge").stat().st_mode & 0o777 == 0o700
users = (root / "etc/passwd").read_text().splitlines()
assert any(line.startswith("bridge:") for line in users)
shadow = (root / "etc/shadow").read_text().splitlines()
hashed = next(line.split(":")[1] for line in shadow if line.startswith("bridge:"))
assert hashed.startswith("$") and len(hashed) > 40
assert "net.ipv4.ip_forward=1" in (root / "etc/sysctl.d/80-wifi-bridge.conf").read_text()
assert "rfkill unblock wifi" in (root / "etc/systemd/system/NetworkManager.service.d/wifi-bridge.conf").read_text()
print("PASS: assembled rootfs — source, enabled services, bootstrap, console account, no private settings", flush=True)
