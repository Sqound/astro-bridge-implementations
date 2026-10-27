"""Inspect an assembled Alpine sys-mode root without exposing private data."""
from pathlib import Path
import json
import sys
root = Path(sys.argv[1])
source = Path(__file__).resolve().parents[2]
for path in (source / 'wifi_bridge').rglob('*'):
    if path.is_file() and '__pycache__' not in path.parts:
        assert (root / 'opt/wifi-bridge' / path.relative_to(source)).read_bytes() == path.read_bytes(), path.name
assert (root / 'etc/wifi-bridge/init-system').read_text().strip() == 'openrc'
assert '3.24' in (root / 'etc/alpine-release').read_text()
for path in ['etc/runlevels/default/wifi-bridge','etc/runlevels/default/networkmanager','etc/runlevels/default/sshd']:
    assert (root / path).is_symlink(),path
for path in ['boot/boot/vmlinuz-rpi','boot/boot/initramfs-rpi','boot/bcm2835-rpi-zero-w.dtb','boot/bootcode.bin']:
    assert (root / path).is_file(),path
assert list((root / 'lib/modules').rglob('*8821cu*')), 'Missing RTL8821CU driver'
assert list((root / 'lib/modules').rglob('*brcmfmac*')), 'Missing onboard Wi-Fi driver'
assert not (root / 'var/lib/wifi-bridge/config.json').exists()
assert not (root / 'sbin/ip').is_symlink(), 'Full iproute2 JSON support is required'
assert (root / 'usr/sbin/rfkill').is_file()
assert (root / 'usr/bin/nmcli').is_file(), 'NetworkManager CLI is a separate Alpine package'
assert list((root / 'lib/firmware').rglob('regulatory.db*')), 'Missing regulatory database'
assert json.loads((root / 'etc/wifi-bridge/bootstrap.json').read_text())['password'] == '12345678'
assert (root / 'etc/wifi-bridge/bootstrap.json').stat().st_mode & 0o777 == 0o600
assert any(line.startswith('bridge:') for line in (root / 'etc/passwd').read_text().splitlines())
assert not list((root / 'etc/ssh').glob('ssh_host_*key')), 'Do not clone SSH host keys'
assert 'root=/dev/mmcblk0p2' in (root / 'boot/cmdline.txt').read_text()
assert 'kernel=boot/vmlinuz-rpi' in (root / 'boot/config.txt').read_text()
print('PASS: Alpine rootfs, application, OpenRC, boot files, Wi-Fi drivers and fresh private state')
