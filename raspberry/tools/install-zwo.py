#!/usr/bin/env python3
"""Owner-run, host-side ASI SDK installer. No vendor executable is invoked."""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

from asi_archive import download, known_build, nested_version, select_members, MAX_DOWNLOAD

URL = 'https://dl.zwoastro.com/software?app=DeveloperCameraSdk&platform=windows86&region=Overseas'
STATE = Path('/var/lib/wifi-bridge')
INSTALL = STATE / 'camera-drivers/zwo'
LIBRARY = 'libASICamera2.so'
RULE = '70-astro-bridge-asi.rules'
RULE_LINK = Path('/etc/udev/rules.d') / RULE
# Root-owned markers of the Raspberry Pi 5 appliance image (the second one predates 1.2.0).
PI5_MARKERS = (Path('/etc/wifi-bridge/pi5-appliance'), Path('/etc/wifi-bridge/pi5-asi220-test'))
PRODUCTS = (0x120a, 0x120b, 0x120c, 0x120e, 0x2209, 0x224a, 0x294f, 0x533f)
ABI = {(1, 40): 'armv6', (2, 183): 'armv8', (1, 3): 'x86', (2, 62): 'x64'}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def binary_abi(path):
    with path.open('rb') as stream:
        head = stream.read(64)
    require(len(head) == 64 and head[:4] == b'\x7fELF' and head[5:7] == b'\x01\x01',
            'unsupported_userland')
    machine = int.from_bytes(head[18:20], 'little')
    abi = ABI.get((head[4], machine))
    require(abi and (abi != 'armv6' or int.from_bytes(head[36:40], 'little') & 0x600 == 0x400),
            'unsupported_userland')
    return abi


def safe_directory(path):
    for ancestor in [*reversed(path.parents), path]:
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'unsafe_directory')


def astro_data(mounts, label, root='/'):
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != str(STATE) or '-' not in fields:
            continue
        fs = fields[fields.index('-')+1:]
        if (len(fs) >= 3 and fields[3] == root and fs[0] == 'ext4' and 'rw' in fields[5].split(',')
                and 'rw' in fs[2].split(',') and Path(fs[1]).resolve() == label.resolve(strict=True)):
            return True
    return False


def pi5_data(mounts):
    # Match the image's astro-data authority without relying on udev labels.
    device = Path('/dev/mmcblk0p2')
    try:
        if not stat.S_ISBLK(device.stat().st_mode):
            return False
        result = subprocess.run(['busybox', 'blkid', '/dev/mmcblk0p2'],
                                check=True, timeout=10, capture_output=True, text=True)
        if not re.search(r'(?:^|\s)LABEL="ASTRO_DATA"(?:\s|$)', result.stdout):
            return False
        return astro_data(mounts, device, '/state')
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return False


def persistent_state(mounts):
    best = None
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields:
            continue
        point = Path(fields[4])
        if point != STATE and point not in STATE.parents:
            continue
        fs = fields[fields.index('-')+1:]
        if len(fs) >= 3 and (best is None or len(point.parts) >= best[0]):
            best = (len(point.parts), fs[0] in ('ext2','ext3','ext4','xfs','btrfs','f2fs','zfs')
                    and 'rw' in fields[5].split(',') and 'rw' in fs[2].split(','))
    return best is not None and best[1]


def preflight(target, docker_abi):
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_host_root_required')
    safe_directory(STATE)
    safe_directory(RULE_LINK.parent)
    mounts = Path('/proc/self/mountinfo').read_text()
    require(persistent_state(mounts), 'writable_persistent_state_required')
    require(shutil.disk_usage(STATE).free >= 320*1024*1024, 'insufficient_state_space')
    require(not Path('/.dockerenv').exists() and not Path('/run/.containerenv').exists(), 'run_on_host')
    alpine = Path('/etc/alpine-release').exists()
    model_path = Path('/proc/device-tree/model')
    model = ''
    if model_path.exists():
        with model_path.open('rb') as stream:
            model = stream.read(256).rstrip(b'\0\n').decode('ascii')
    abi = binary_abi(Path('/proc/self/exe'))
    if target == 'alpine-pi5':
        require(alpine and model.startswith('Raspberry Pi 5 ') and abi == 'armv8', 'pi5_aarch64_alpine_required')
        safe_directory(PI5_MARKERS[0].parent)
        require(any(marker.is_file() and not marker.is_symlink() and marker.lstat().st_uid == 0
                    and not marker.lstat().st_mode & 0o022 for marker in PI5_MARKERS), 'pi5_image_required')
    elif target != 'docker-linux' or abi.startswith('arm') or model.startswith('Raspberry Pi'):
        require(model.startswith('Raspberry Pi 3 Model B'), 'pi_3b_required')
    if alpine and Path('/etc/wifi-bridge/diskless').is_file():
        require(target in ('alpine', 'alpine-pi5'), 'use_diskless_target')
    if target in ('alpine', 'alpine-pi5'):
        require(alpine and (pi5_data(mounts) if target == 'alpine-pi5' else
                astro_data(mounts, Path('/dev/disk/by-label/ASTRO_DATA'))), 'astro_data_mount_required')
        require(Path('/lib/libgcompat.so.0').is_file(), 'gcompat_required')
        require(RULE_LINK.is_symlink() and os.readlink(RULE_LINK) == str(INSTALL / RULE),
                'diskless_rule_link_required')
    elif target == 'raspios':
        require(not alpine and Path('/etc/rpi-issue').is_file(), 'raspberry_pi_os_required')
    if target == 'docker-linux':
        require(docker_abi == abi, 'matching_native_container_abi_required')
    else:
        require(docker_abi is None, 'unexpected_container_abi')
    require(shutil.which('udevadm') is not None, 'udev_required')
    return abi


def rule_bytes():
    return ''.join(f'SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_device", ATTR{{idVendor}}=="03c3", '
                   f'ATTR{{idProduct}}=="{pid:04x}", OWNER="root", GROUP="root", MODE="0600"\n'
                   for pid in PRODUCTS).encode('ascii')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stage(temp, abi, destination, accept, fetch=download):
    # The vendor replaces its archive with each release: accept only a known
    # build, identified by the exact library and licence it contains.
    archive = temp / 'input.zip'
    outer = fetch(URL, archive)
    nested, version = nested_version(archive)
    prefix, name = f'ASI_linux_mac_SDK_V{version}', f'{LIBRARY}.{version}'
    payload = temp / 'payload'
    payload.mkdir(mode=0o700)
    lib, licence = payload / name, payload / 'license.txt'
    wanted = {f'{prefix}/lib/{abi}/{name}': (lib, 32 * 1024 * 1024),
              f'{prefix}/license.txt': (licence, 64 * 1024)}
    select_members(archive, nested, wanted, temp / 'nested.tar.bz2')
    require(binary_abi(lib) == abi, 'wrong_library_abi')
    digest = sha256(lib)
    require(known_build(version, abi, digest, sha256(licence), outer), 'unknown_sdk_build')
    require(accept(licence.read_bytes(), version), 'license_not_accepted')
    (payload / RULE).write_bytes(rule_bytes())
    (payload / 'platform.json').write_text(json.dumps({'path': str(destination / name),
                                                       'sha256': digest})+'\n')
    for item in payload.iterdir():
        item.chmod(0o600)
        with item.open('rb') as stream:
            os.fsync(stream.fileno())
    return payload, version


def sync_dir(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def local_fetch(source):
    def fetch(url, destination):
        digest, size = hashlib.sha256(), 0
        with source.open('rb') as stream, destination.open('xb') as output:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                require(size <= MAX_DOWNLOAD, 'download_too_large')
                digest.update(chunk)
                output.write(chunk)
        return digest.hexdigest()
    return fetch


def install(target, docker_abi, accept, archive=None):
    abi = preflight(target, docker_abi)
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The appliance has a 32 MiB RAM /tmp. Keep bounded temporary archive
        # bytes on the verified state volume; publish only the accepted files.
        with tempfile.TemporaryDirectory(prefix='.asi-setup-', dir=STATE) as tmp:
            payload, version = (stage(Path(tmp), abi, INSTALL, accept, local_fetch(archive)) if archive
                                else stage(Path(tmp), abi, INSTALL, accept))
            INSTALL.parent.mkdir(mode=0o700, exist_ok=True)
            safe_directory(INSTALL.parent)
            if os.path.lexists(INSTALL):
                safe_directory(INSTALL)
                same = {p.name for p in INSTALL.iterdir()} == {p.name for p in payload.iterdir()} and all(
                    (INSTALL / item.name).is_file() and not (INSTALL / item.name).is_symlink()
                    and sha256(INSTALL / item.name) == sha256(item) for item in payload.iterdir())
                if not same:
                    # A newer accepted SDK replaces the previous one in one rename.
                    INSTALL.rename(Path(tmp) / 'previous')
                    sync_dir(INSTALL.parent)
            if os.path.lexists(RULE_LINK):
                require(RULE_LINK.is_symlink() and os.readlink(RULE_LINK) == str(INSTALL / RULE),
                        'existing_rule_conflict')
            else:
                RULE_LINK.symlink_to(INSTALL / RULE)
                sync_dir(RULE_LINK.parent)
            if not INSTALL.exists():
                sync_dir(payload)
                payload.rename(INSTALL)
                sync_dir(INSTALL.parent)
            # Reload rules for future additions; never trigger/rebind existing USB devices.
            subprocess.run(['udevadm', 'control', '--reload-rules'], timeout=10, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return version
    finally:
        os.close(fd)


def confirmation(licence, version):
    sys.stdout.buffer.write(licence + b'\n')
    sys.stdout.buffer.flush()
    print(json.dumps({'license': 'ZWO ASI SDK ' + version, 'type_to_accept': 'ACCEPT'}), flush=True)
    return sys.stdin.isatty() and sys.stdin.readline(32).rstrip('\r\n') == 'ACCEPT'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('target', choices=('alpine', 'alpine-pi5', 'raspios', 'docker-linux'))
    parser.add_argument('--container-abi', choices=tuple(ABI.values()))
    parser.add_argument('--archive', type=Path, help='local vendor SDK zip for offline installation')
    args = parser.parse_args()
    try:
        version = install(args.target, args.container_abi, confirmation, args.archive)
    except Exception as error:
        # Network errors can contain signed redirects. Expose only internal codes.
        code = str(error) if type(error) is ValueError else ''
        print(json.dumps({'error': code if code.isidentifier() and code.isascii() and len(code)<64
                          else 'installation_failed'}), file=sys.stderr)
        return 1
    print(json.dumps({'status': 'installed', 'version': version}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
