#!/usr/bin/env python3
"""Download and verify Astro Bridge, then flash removable media with Imager CLI.

The graphical installer manages image and drive selection. The official pinned
Raspberry Pi Imager engine performs writing and read-back verification. The OS
requests administrator authorization; credentials never enter this application.
"""
import argparse
from contextlib import contextmanager
import struct
import unicodedata
import platform
import plistlib
import re
import shlex
import tempfile
import time
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import urllib.request
import urllib.error

# Packaged builds include a CA bundle instead of depending on a Python install.
try:
    import certifi
except ImportError:
    certifi = None

REPO = 'Sqound/astro-bridge-implementations'
IMAGE_PLATFORMS = {
    'alpine-standard': ('alpine-armhf-standard', 'ALPINE-STANDARD-SHA256SUMS'),
    'raspios': ('armhf', 'SHA256SUMS'),
}
CHECKSUM_LIMIT = 65536
WIFI_SEED_OFFSET = 65536
WIFI_SEED_SIZE = 4096
WIFI_SEED_MARKER = b'ASTRO-WIFI-SEED-1\n'
WIFI_IMAGE_SIZE = 1210056704
# First-boot zone, version 2 (the reader is image/common/astro_seed.py; tests keep both in step).
SEED_V2_MARKER = b'ASTRO-SEED-2\n'
SEED_READER_OFFSET = WIFI_SEED_OFFSET + WIFI_SEED_SIZE
SEED_READER_MARK = b'ASTRO-SEED-READER-2\n'
DROP_MAGIC = b'ASTRO-DROP-1\n'
DROP_ALIGN = 4096
# Zeros after the last archive. A vendor archive that is a ZIP (ZWO's is) ends the file with its end-of-central-directory
# record, and libarchive then takes the whole raw image for that ZIP (it looks for the record in the last few KB), so
# Raspberry Pi Imager would read the archive instead of the disk image. Tail zeros keep the record out of that window.
DROP_TAIL = 1024 * 1024
MAX_DROP_DRIVERS = 8
MAX_DRIVER_ARCHIVE = 512 * 1024 * 1024
# (accepted p1 types, p1 first sector, p1 sectors, p2 first sector, p2 sectors) of each known card.
SEED_LAYOUTS = {
    'pi5': ({0x06}, 2048, 264192, 266240, 2097152),
    'zero-w': ({0x0b, 0x0c}, 2048, 1048576, 1050624, 2095104),
}
SAFE_PERSONALIZED_ERRORS = {
    'Invalid Wi-Fi settings.', 'This image does not support Wi-Fi preparation.',
    'The expanded image does not fit on this drive.', 'Image checksum mismatch. Nothing has been flashed.',
    'Invalid bridge password.', 'This image does not support the bridge password or camera drivers.',
    'The image and camera drivers do not fit on this drive.',
    'A camera driver file changed. Nothing has been flashed.', 'Invalid camera driver request.',
}


def layout_bytes(name):
    return (SEED_LAYOUTS[name][3] + SEED_LAYOUTS[name][4]) * 512


def wifi_payload(settings):
    """Validate without ever including credential values in errors."""
    if not isinstance(settings, dict) or set(settings) != {'ssid', 'password', 'hidden', 'country'}:
        raise ValueError('Invalid Wi-Fi settings.')
    ssid, password, hidden, country = (settings[k] for k in ('ssid', 'password', 'hidden', 'country'))
    try:
        if not isinstance(ssid, str) or any(unicodedata.category(c) == 'Cc' for c in ssid):
            raise ValueError
        ssid_bytes = ssid.encode('utf-8')
        if not 1 <= len(ssid_bytes) <= 32:
            raise ValueError
        if not isinstance(password, str):
            raise ValueError
        if re.fullmatch(r'[0-9a-fA-F]{64}', password):
            psk = password.lower()
        elif not any(unicodedata.category(c) == 'Cc' for c in password) and 8 <= len(password.encode('utf-8')) <= 63:
            psk = hashlib.pbkdf2_hmac('sha1', password.encode('utf-8'), ssid_bytes, 4096, 32).hex()
        else:
            raise ValueError
        if type(hidden) is not bool or not isinstance(country, str) or not re.fullmatch(r'[A-Z]{2}', country):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('Invalid Wi-Fi settings.') from None
    return {'ssid': ssid, 'psk': psk, 'hidden': hidden, 'country': country}


def _zone(marker, value):
    payload = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n'
    if len(marker) + len(payload) > WIFI_SEED_SIZE:
        raise ValueError('Invalid Wi-Fi settings.')
    return marker + payload + bytes(WIFI_SEED_SIZE - len(marker) - len(payload))


def wifi_seed(settings):
    """Version 1 zone: the private Wi-Fi network only (images that read nothing else)."""
    return _zone(WIFI_SEED_MARKER, wifi_payload(settings))


def bridge_password(value):
    """The bridge rule (wifi_bridge/password.py): 8 to 63 printable ASCII characters, no space, not 12345678."""
    if not isinstance(value, str) or not re.fullmatch(r'[\x21-\x7e]{8,63}', value) or value == '12345678':
        raise ValueError('Invalid bridge password.')
    return value


def seed_v2(settings=None, password=None, drivers=None):
    """Version 2 zone: each part optional. The password is the only clear value, and only here."""
    value = {}
    if settings is not None:
        value['wifi'] = wifi_payload(settings)
    if password is not None:
        value['password'] = bridge_password(password)
    if drivers is not None:
        value['drivers'] = drivers
    if not value:
        raise ValueError('Invalid Wi-Fi settings.')
    return _zone(SEED_V2_MARKER, value)


def seed_layout(prefix):
    """Name of the known card layout of an image, or None."""
    if len(prefix) < 512 or prefix[510:512] != b'\x55\xaa' or any(prefix[478:510]):
        return None
    first, second = (struct.unpack_from('<B3sB3sII', prefix, 446 + 16*i) for i in range(2))
    for name, (types, p1, p1_size, p2, p2_size) in SEED_LAYOUTS.items():
        if (first[0], first[4], first[5]) == (0x80, p1, p1_size) and first[2] in types \
                and (second[0], second[2], second[4], second[5]) == (0, 0x83, p2, p2_size):
            return name
    return None


def check_wifi_base(prefix):
    if len(prefix) < WIFI_SEED_OFFSET + WIFI_SEED_SIZE or seed_layout(prefix) != 'pi5' or \
       prefix[WIFI_SEED_OFFSET:WIFI_SEED_OFFSET + WIFI_SEED_SIZE] != \
       WIFI_SEED_MARKER + bytes(WIFI_SEED_SIZE - len(WIFI_SEED_MARKER)):
        raise ValueError('This image does not support Wi-Fi preparation.')


def check_seed_base(prefix):
    """Layout of an image that reads the version 2 zone: blank zone, then the reader mark."""
    name = seed_layout(prefix)
    if (name is None or len(prefix) < SEED_READER_OFFSET + WIFI_SEED_SIZE
            or prefix[WIFI_SEED_OFFSET:WIFI_SEED_OFFSET + WIFI_SEED_SIZE] != WIFI_SEED_MARKER + bytes(WIFI_SEED_SIZE - len(WIFI_SEED_MARKER))
            or prefix[SEED_READER_OFFSET:SEED_READER_OFFSET + WIFI_SEED_SIZE] != SEED_READER_MARK + bytes(WIFI_SEED_SIZE - len(SEED_READER_MARK))):
        raise ValueError('This image does not support the bridge password or camera drivers.')
    return name


def _padded(size):
    return -(-size // DROP_ALIGN) * DROP_ALIGN


def driver_plan(drivers, image_bytes):
    """The drop appended after the image: (header bytes, zone pointer, [(path, size, sha256, offset)], end).

    drivers = {'catalog_version': ..., 'items': [...]}, as Setup sends it. Every
    archive file is checked again here (regular file, size, SHA-256) before
    anything is written. Nothing is unpacked or executed.
    """
    if (not isinstance(drivers, dict) or set(drivers) != {'catalog_version', 'items'}
            or not isinstance(drivers['catalog_version'], str)
            or not re.fullmatch(r'[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{1,3}', drivers['catalog_version'])
            or not isinstance(drivers['items'], list) or not 1 <= len(drivers['items']) <= MAX_DROP_DRIVERS):
        raise ValueError('Invalid camera driver request.')
    keys = {'id', 'version', 'arch', 'source', 'path', 'size', 'sha256', 'license_sha256', 'terms_url'}
    entries, files, seen = [], [], set()
    for item in drivers['items']:
        if (not isinstance(item, dict) or set(item) != keys or not isinstance(item['id'], str)
                or not re.fullmatch('[a-z][a-z0-9_]{0,31}', item['id']) or item['id'] in seen
                or not isinstance(item['version'], str) or not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+-]{0,31}', item['version'])
                or item['arch'] not in ('arm64', 'armv6') or item['source'] != 'download'
                or not (item['license_sha256'] is None or isinstance(item['license_sha256'], str)
                        and re.fullmatch('[0-9a-f]{64}', item['license_sha256']))
                or not isinstance(item['terms_url'], str) or not item['terms_url'].startswith('https://')
                or len(item['terms_url']) > 512):
            raise ValueError('Invalid camera driver request.')
        seen.add(item['id'])
        entry = {key: item[key] for key in ('id', 'version', 'arch', 'source', 'license_sha256', 'terms_url')}
        entry.update(size=None, sha256=None, offset=None)
        entries.append(entry)
        if (type(item['size']) is not int or not 0 < item['size'] <= MAX_DRIVER_ARCHIVE
                or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                or not isinstance(item['path'], str) or not os.path.isabs(item['path'])):
            raise ValueError('Invalid camera driver request.')
        path = Path(item['path'])
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size']:
            raise ValueError('A camera driver file changed. Nothing has been flashed.')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                raise ValueError('A camera driver file changed. Nothing has been flashed.')
        entry.update(size=item['size'], sha256=item['sha256'])
        files.append((len(entries) - 1, path, item['size'], item['sha256']))
    header_size = DROP_ALIGN
    while True:
        offset, placed = image_bytes + header_size, []
        for index, path, size, digest in files:
            entries[index]['offset'] = offset
            placed.append((path, size, digest, offset))
            offset += _padded(size)
        line = json.dumps({'catalog_version': drivers['catalog_version'], 'drivers': entries},
                          ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n'
        needed = _padded(len(DROP_MAGIC) + len(line))
        if needed <= header_size:
            break
        header_size = needed
    if header_size > 65536:
        raise ValueError('Invalid camera driver request.')
    header = (DROP_MAGIC + line).ljust(header_size, b'\0')
    pointer = {'offset': image_bytes, 'length': header_size, 'sha256': hashlib.sha256(header).hexdigest()}
    return header, pointer, placed, _padded(offset) + DROP_TAIL


@contextmanager
def personalized_image(image, checksum, disk_size, settings, password=None, drivers=None):
    """Create a short-lived raw image, checking the compressed source twice.

    The first-boot zone receives the Wi-Fi network, the bridge password and the
    pointer to the camera drivers; the drivers follow the image, unchanged.
    The copy lives in a private folder (0700, file 0600) removed afterwards.
    """
    if settings is not None:
        wifi_payload(settings)
    if password is not None:
        bridge_password(password)
    if not image.name.endswith('.img.xz'):
        raise ValueError('This image does not support Wi-Fi preparation.')
    if disk_size < min(WIFI_IMAGE_SIZE, *(layout_bytes(name) for name in SEED_LAYOUTS)):
        raise ValueError('The expanded image does not fit on this drive.')
    verified(image, checksum)
    parent = cache_directory() / 'personalized'
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wifi-', dir=parent) as directory:
        os.chmod(directory, 0o700)
        target = Path(directory) / 'personalized.img'
        digest = hashlib.sha256()
        with image.open('rb') as source, os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out:
            with lzma.LZMAFile(source) as stream:
                prefix = stream.read(1024 * 1024)
                version_2 = password is not None or bool(drivers) or seed_layout(prefix) != 'pi5' or \
                    prefix[SEED_READER_OFFSET:SEED_READER_OFFSET + WIFI_SEED_SIZE] == SEED_READER_MARK + bytes(WIFI_SEED_SIZE - len(SEED_READER_MARK))
                if version_2:
                    # Never a version 2 zone in an image that only reads version 1.
                    name = check_seed_base(prefix)
                    expected = layout_bytes(name)
                    plan = driver_plan(drivers, expected) if drivers else None
                    seed = seed_v2(settings, password, plan[1] if plan else None)
                    total = plan[3] if plan else expected
                else:
                    check_wifi_base(prefix)
                    expected = total = WIFI_IMAGE_SIZE
                    plan, seed = None, wifi_seed(settings)
                if disk_size < expected:
                    raise ValueError('The expanded image does not fit on this drive.')
                if disk_size < total:
                    raise ValueError('The image and camera drivers do not fit on this drive.')
                prefix = prefix[:WIFI_SEED_OFFSET] + seed + prefix[WIFI_SEED_OFFSET + WIFI_SEED_SIZE:]
                seed = None
                size = len(prefix)
                out.write(prefix); digest.update(prefix)
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > expected:
                        raise ValueError('This image does not support Wi-Fi preparation.')
                    out.write(chunk); digest.update(chunk)
                if size != expected:
                    raise ValueError('This image does not support Wi-Fi preparation.')
            if plan:
                header, _, files, _ = plan
                out.write(header); digest.update(header)
                for path, length, expected_digest, offset in files:
                    pad = offset - out.tell()
                    out.write(bytes(pad)); digest.update(bytes(pad))
                    check = hashlib.sha256()
                    with path.open('rb') as archive:
                        while chunk := archive.read(1024 * 1024):
                            check.update(chunk); out.write(chunk); digest.update(chunk)
                    if check.hexdigest() != expected_digest or out.tell() != offset + length:
                        raise ValueError('A camera driver file changed. Nothing has been flashed.')
                end = plan[3]
                if out.tell() > end:
                    raise ValueError('A camera driver file changed. Nothing has been flashed.')
                while out.tell() < end:
                    pad = bytes(min(1024 * 1024, end - out.tell()))
                    out.write(pad); digest.update(pad)
            source.seek(0)
            if hashlib.file_digest(source, 'sha256').hexdigest() != checksum.lower():
                raise ValueError('Image checksum mismatch. Nothing has been flashed.')
        yield target.resolve(), digest.hexdigest()


def external_options(command, options):
    # macOS GUI processes must spawn native tools without forking Cocoa state
    # from a background thread. Absolute paths + close_fds=False use posix_spawn.
    if sys.platform == 'darwin':
        command = [shutil.which(command[0]) or command[0], *command[1:]]
        options['close_fds'] = False
    return command, options


def command_output(command, **options):
    command, options = external_options(command, options)
    return subprocess.check_output(command, **options)


def run_command(command, **options):
    command, options = external_options(command, options)
    return subprocess.run(command, **options)


def start_command(command, **options):
    command, options = external_options(command, options)
    return subprocess.Popen(command, **options)


def network_context():
    return ssl.create_default_context(cafile=certifi.where() if certifi else None)


def imager():
    candidates = [shutil.which('rpi-imager'),
                  '/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager']
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            for relative in ('Raspberry Pi Ltd/Imager/rpi-imager.exe',
                             'Raspberry Pi Imager/rpi-imager.exe',
                             'Programs/Raspberry Pi Imager/rpi-imager.exe'):
                candidates.append(str(Path(os.environ[variable]) / relative))
    return next((str(p) for p in candidates if p and Path(p).is_file()), None)


def verified(path, checksum):
    if not isinstance(checksum,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',checksum):
        raise ValueError('A complete 64-digit SHA-256 checksum is required.')
    if not path.is_file() or not (path.name.endswith('.img') or path.name.endswith('.img.xz')):
        raise ValueError('Select a .img or .img.xz image.')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != checksum.lower():
        raise ValueError('Image checksum mismatch. Nothing has been flashed.')
    return path.resolve()


def checksum_for(data, name):
    if isinstance(data,bytes):data=data.decode('utf-8')
    if len(data.encode('utf-8'))>CHECKSUM_LIMIT:raise ValueError('Checksum file is too large.')
    matches=[parts[0] for line in data.splitlines() if len(parts:=line.split())==2 and parts[1].lstrip('*')==name]
    if len(matches)!=1 or not re.fullmatch(r'[0-9a-fA-F]{64}',matches[0]):
        raise ValueError('The image needs one unambiguous valid SHA-256 entry in the selected checksum file.')
    return matches[0].lower()


def checksum_file(path, name):
    with path.open('rb') as stream:return checksum_for(stream.read(CHECKSUM_LIMIT+1),name)


def latest(directory, image_platform='alpine-standard'):
    if image_platform not in IMAGE_PLATFORMS:raise ValueError('Choose Alpine standard or Raspberry Pi OS explicitly.')
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/latest', headers={'User-Agent': 'Astro-Bridge-Flasher'})
    try:
        with urllib.request.urlopen(request, timeout=30, context=network_context()) as response:
            data=response.read(1024*1024+1)
        if len(data)>1024*1024:raise ValueError('Release metadata is too large.')
        release=json.loads(data)
    except urllib.error.HTTPError as error:
        if error.code in (401,403,404,429):
            error.close()
            raise ValueError('No accessible stable release. The repository may be private, unreleased or rate limited. Choose a reviewed local image and its checksum file; no account token is needed by this launcher.') from None
        raise
    if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('No stable release is available. Choose a reviewed local image and checksum file.')
    tag=release.get('tag_name','')
    if not isinstance(tag,str) or not re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',tag):raise ValueError('Unsupported stable release version.')
    entries=release.get('assets')
    if not isinstance(entries,list) or len(entries)>1000:raise ValueError('Invalid release assets.')
    assets={}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('name'),str) or entry['name']in assets:raise ValueError('Ambiguous release assets.')
        assets[entry['name']]=entry.get('browser_download_url')
    suffix,sums=IMAGE_PLATFORMS[image_platform]
    name=f"astro-bridge-{tag.removeprefix('v')}-{suffix}.img.xz"
    prefix=f"https://github.com/{REPO}/releases/download/{tag}/"
    for asset in (name,sums):
        if assets.get(asset)!=prefix+asset:
            raise ValueError('The latest stable release has no complete image for the selected platform. Choose another platform explicitly or a reviewed local image; no fallback was downloaded.')
    with urllib.request.urlopen(assets[sums], timeout=30, context=network_context()) as response:
        checksum=checksum_for(response.read(CHECKSUM_LIMIT+1),name)
    directory.mkdir(parents=True, exist_ok=True)
    image=directory/name
    if not image.exists():
        partial=image.with_suffix('.partial')
        try:
            with urllib.request.urlopen(assets[name], timeout=60, context=network_context()) as response, partial.open('wb') as output:
                shutil.copyfileobj(response,output)
            partial.rename(image)
            verified(image,checksum)
        except Exception:
            partial.unlink(missing_ok=True);image.unlink(missing_ok=True)
            raise
    return verified(image,checksum)


# Pinned official Raspberry Pi Imager assets. Updates require a reviewed hash change.
IMAGER_VERSION = 'v2.0.12'
IMAGER_ASSETS = {
    'darwin': ('rpi-imager-v2.0.12.dmg', '15b1a94c712c0032750855653c2bf129dceb401329fbce0a83d7cf05dc58a421'),
    'win32': ('imager-v2.0.12.exe', '98bff90afe23339d0768915f6f68d35ac254740ec1eac2082ebeaa9ba41bc8e3'),
    'linux-x86_64': ('Raspberry_Pi_Imager-v2.0.12-cli-x86_64.AppImage', '8a2fa9c0fe6c61f57d5826a7419131b18f33226c681c740ebc30a713928d05e3'),
    'linux-aarch64': ('Raspberry_Pi_Imager-v2.0.12-cli-aarch64.AppImage', 'b87c82db68fbe8f4fd7c266af8a5d7b2aad3d0ddc2e4243ebd6ea77bcb65573c'),
    'linux-armv7l': ('Raspberry_Pi_Imager-v2.0.12-cli-armhf.AppImage', 'dcf25bcbd83256e2b568d2fde8f994629937ebe254cfbf8ec7ad718491cff3bc'),
}


def cache_directory():
    if os.environ.get('ASTRO_BRIDGE_SETUP_CACHE'):
        return Path(os.environ['ASTRO_BRIDGE_SETUP_CACHE'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches/Astro-Bridge'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Astro-Bridge/Cache'
    return Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'astro-bridge'


def checked_download(url, destination, checksum, notify=lambda text: None):
    """Only make a file executable after checking the pinned vendor hash."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    def matches(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == checksum
    if destination.is_file() and matches(destination):
        return destination
    partial = destination.with_suffix(destination.suffix + '.partial')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Astro-Bridge-Flasher'})
        with urllib.request.urlopen(request, timeout=60, context=network_context()) as response, partial.open('wb') as output:
            size = int(response.headers.get('Content-Length', 0))
            done, previous = 0, -1
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                done += len(chunk)
                percent = int(done * 100 / size) if size else 0
                if percent != previous:
                    notify(f'Downloading Raspberry Pi Imager: {percent} %' if size else 'Downloading Raspberry Pi Imager…')
                    previous = percent
        if not matches(partial):
            raise ValueError('Raspberry Pi Imager checksum mismatch. Download discarded.')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def run_json(command):
    return json.loads(command_output(command, text=True, timeout=15))


def disks():
    """Return whole removable drives only; never offer internal/system disks."""
    result = []
    if sys.platform == 'darwin':
        root = plistlib.loads(command_output(['diskutil', 'info', '-plist', '/'], timeout=8))
        protected = {root.get('ParentWholeDisk')}
        protected.update(re.sub(r's[0-9]+$', '', store['APFSPhysicalStore']) for store in root.get('APFSPhysicalStores', []))
        # Query hardware metadata without opening mounted volumes. diskutil info
        # on external volumes can wait for macOS privacy authorization.
        devices = plistlib.loads(command_output(['ioreg', '-a', '-r', '-l', '-c', 'IOBlockStorageDevice'], timeout=8))
        def media_nodes(node):
            yield node
            for child in node.get('IORegistryEntryChildren', []):
                yield from media_nodes(child)
        seen = set()
        for hardware in devices:
            interconnect = hardware.get('Protocol Characteristics', {}).get('Physical Interconnect')
            if not interconnect or interconnect == 'Virtual Interface':
                continue
            for info in media_nodes(hardware):
                name = info.get('BSD Name', '')
                if (name in seen or name in protected or not re.fullmatch(r'disk[0-9]+', name)
                        or not info.get('Whole') or not info.get('Removable')
                        or not info.get('Writable') or int(info.get('Size', 0)) <= 0):
                    continue
                seen.add(name)
                result.append(dict(device='/dev/' + name,
                                   name=info.get('IORegistryEntryName', 'SD card').removesuffix(' Media'),
                                   size=int(info['Size']), identity=str(info['IORegistryEntryID'])))
    elif sys.platform == 'win32':
        script = "$removable = @(Get-CimInstance Win32_DiskDrive | Where-Object { $_.MediaType -like '*Removable*' } | Select-Object -ExpandProperty Index); Get-Disk | Where-Object { ($_.BusType -in @('SD','MMC') -or ($_.BusType -eq 'USB' -and $_.Number -in $removable)) -and -not $_.IsBoot -and -not $_.IsSystem -and -not $_.IsOffline -and -not $_.IsReadOnly } | Select-Object Number,FriendlyName,Size,UniqueId | ConvertTo-Json -Compress"
        data = command_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], text=True).strip()
        rows = json.loads(data) if data else []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            result.append(dict(device='\\\\.\\PhysicalDrive' + str(row['Number']), name=row['FriendlyName'],
                               size=int(row['Size']), identity=row['UniqueId']))
    else:
        listing = run_json(['lsblk', '--json', '--bytes', '--paths', '--output', 'NAME,MODEL,SIZE,TYPE,RM,HOTPLUG,RO,SERIAL,MOUNTPOINTS'])
        def critical(node):
            return any(p == '/' or (p and (p == '/boot' or p.startswith('/boot/'))) for p in node.get('mountpoints', [])) or any(critical(c) for c in node.get('children', []))
        for row in listing['blockdevices']:
            if row['type'] != 'disk' or row.get('ro') or not row.get('rm') or critical(row):
                continue
            result.append(dict(device=row['name'], name=(row.get('model') or 'Removable drive').strip(),
                               size=int(row['size']), identity=row.get('serial') or row['name']))
    return result


def isolated_disks():
    """Keep native drive queries out of the macOS Tk process entirely."""
    command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
    output = command_output([*command, '--list-disks'], text=True, timeout=40,
                            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
    return json.loads(output)


def validate_target(selected, isolated=False, setup=False):
    if setup and (not isinstance(selected.get('identity'), str) or not selected['identity'].strip()
                  or selected['identity'] == selected.get('device')):
        raise ValueError('The removable drive has no stable hardware identity.')
    rows = isolated_disks() if isolated else disks()
    if setup:
        rows = setup_media(rows)
    current = next((disk for disk in rows if disk == selected), None)
    if not current:
        raise ValueError('The selected drive changed or disappeared. Refresh and select it again.')
    return current


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def elevated(command):
    """Ask the OS for authorization; never request or store a password ourselves."""
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    if sys.platform == 'darwin':
        shell = 'env PYINSTALLER_RESET_ENVIRONMENT=1 ' + shlex.join(command)
        return start_command(['osascript', '-e', 'do shell script ' + json.dumps(shell, ensure_ascii=False) + ' with administrator privileges'],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
    if sys.platform == 'win32':
        arguments = subprocess.list2cmdline(command[1:])
        script = ("$ErrorActionPreference='Stop'; $env:PYINSTALLER_RESET_ENVIRONMENT='1'; "
                  "$p=Start-Process -FilePath " + ps_literal(command[0]) +
                  ' -ArgumentList ' + ps_literal(arguments) + ' -Verb RunAs -Wait -PassThru; exit $p.ExitCode')
        return start_command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
    if os.geteuid() == 0:
        return start_command(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)
    if not shutil.which('pkexec'):
        raise ValueError('Install PolicyKit (pkexec) and start its desktop authentication agent, then retry.')
    return start_command(['pkexec', 'env', 'PYINSTALLER_RESET_ENVIRONMENT=1', *command],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)


def ensure_imager(notify=lambda text: None):
    """Download the official engine without requiring a manual installation."""
    key = 'linux-' + platform.machine().lower() if sys.platform.startswith('linux') else sys.platform
    if key not in IMAGER_ASSETS:
        raise ValueError('This platform has no bundled Imager download.')
    name, checksum = IMAGER_ASSETS[key]
    directory = cache_directory() / IMAGER_VERSION
    asset = checked_download(f'https://github.com/raspberrypi/rpi-imager/releases/download/{IMAGER_VERSION}/{name}',
                             directory / name, checksum, notify)
    notify('Preparing Raspberry Pi Imager…')
    if sys.platform == 'darwin':
        target = directory / 'Raspberry Pi Imager.app'
        if not target.exists():
            mounted = plistlib.loads(command_output(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(asset)]))
            mount = next(e['mount-point'] for e in mounted['system-entities'] if 'mount-point' in e)
            try:
                source = next(Path(mount).glob('*.app'))
                run_command(['ditto', str(source), str(target)], check=True)
            finally:
                run_command(['hdiutil', 'detach', mount], check=True)
        run_command(['codesign', '--verify', '--deep', '--strict', str(target)], check=True)
        return str(target / 'Contents/MacOS/rpi-imager')
    if sys.platform == 'win32':
        existing = imager()
        if not existing:
            notify('Installing Raspberry Pi Imager. Approve the Windows authorization prompt…')
            process = elevated([str(asset), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-'])
            output = process.communicate()[0]
            if process.returncode:
                raise ValueError('Imager installation was cancelled or failed: ' + output[-1000:])
            existing = imager()
        if not existing:
            raise ValueError('Imager installation finished, but its executable was not found.')
        return existing
    target = directory / 'squashfs-root/AppRun'
    if not target.exists():
        asset.chmod(0o755)
        run_command([str(asset), '--appimage-extract'], cwd=directory, check=True, stdout=subprocess.DEVNULL)
    return str(target)


def setup_tree_digest(root):
    """Fingerprint all engine files and symlinks in a private extracted tree."""
    root = Path(root).resolve(strict=True)
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode('utf-8') + b'\0')
        digest.update(str(path.lstat().st_mode).encode('ascii') + b'\0')
        if path.is_symlink():
            target = os.readlink(path)
            if not path.resolve().is_relative_to(root):
                raise ValueError('Imager contains a link outside its private directory.')
            digest.update(b'L' + target.encode('utf-8') + b'\0')
        elif path.is_file():
            digest.update(b'F')
            with path.open('rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
        elif path.is_dir():
            digest.update(b'D')
        else:
            raise ValueError('Unexpected file in Imager.')
    return digest.hexdigest()

def windows_program_roots():
    script = ("@([Environment]::GetFolderPath('ProgramFiles'),"
              "[Environment]::GetFolderPath('ProgramFilesX86')) | ConvertTo-Json -Compress")
    roots = json.loads(command_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], text=True, stderr=subprocess.PIPE))
    if isinstance(roots, str):
        roots = [roots]
    if not isinstance(roots, list):
        raise ValueError('Cannot determine system Program Files directories.')
    return [Path(root).resolve() for root in roots if isinstance(root, str) and root]

def windows_imager_candidates():
    candidates = []
    for root in windows_program_roots():
        root = root.resolve()
        for relative in ('Raspberry Pi Ltd/Imager/rpi-imager.exe', 'Raspberry Pi Imager/rpi-imager.exe'):
            candidate = root / relative
            if candidate.resolve().is_relative_to(root):
                candidates.append(candidate)
    return candidates

def windows_signature(path):
    script = ("$ErrorActionPreference='Stop'; $p=" + ps_literal(str(path)) + "; "
              "$s=Get-AuthenticodeSignature -LiteralPath $p; $v=(Get-Item -LiteralPath $p).VersionInfo; "
              "@{status=[string]$s.Status; thumbprint=$s.SignerCertificate.Thumbprint; "
              "file=$v.FileVersion; product=$v.ProductVersion} | ConvertTo-Json -Compress")
    data = json.loads(command_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], text=True, stderr=subprocess.PIPE))
    if not isinstance(data, dict) or data.get('status') != 'Valid' or not re.fullmatch(r'[A-Fa-f0-9]{40,128}', data.get('thumbprint') or ''):
        raise ValueError('Imager Authenticode signature is not valid.')
    return data

def windows_engine_metadata(engine, installer_thumb):
    engine = Path(engine).resolve(strict=True)
    candidates = [candidate.resolve() for candidate in windows_imager_candidates()]
    if engine not in candidates or not engine.is_file():
        raise ValueError('Imager is not in a system Program Files location.')
    signature = windows_signature(engine)
    if signature['thumbprint'].upper() != installer_thumb.upper():
        raise ValueError('Imager publisher does not match the pinned installer.')
    versions = [signature.get(k) for k in ('file', 'product') if signature.get(k)]
    if not versions or any(not re.fullmatch(r'2\.0\.12(?:\.0)?', version.strip()) for version in versions):
        raise ValueError('Installed Imager does not have the pinned version.')
    with engine.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def windows_find_engine(thumbprint):
    for candidate in windows_imager_candidates():
        if candidate.is_file():
            try:
                digest = windows_engine_metadata(candidate, thumbprint)
                return str(candidate.resolve()), digest
            except (ValueError, OSError, subprocess.SubprocessError):
                continue
    return None

def prepare_setup_imager(directory, notify):
    """Derive a fresh engine exclusively from the pinned verified vendor asset."""
    key = 'linux-' + platform.machine().lower() if sys.platform.startswith('linux') else sys.platform
    if key not in IMAGER_ASSETS:
        raise ValueError('This platform has no pinned Setup Imager.')
    name, checksum = IMAGER_ASSETS[key]
    source = checked_download(f'https://github.com/raspberrypi/rpi-imager/releases/download/{IMAGER_VERSION}/{name}',
                              cache_directory() / IMAGER_VERSION / name, checksum, notify)
    # Canonical paths: the pre-launch check compares against the resolved engine root,
    # so a symlinked cache or temporary directory (macOS /var) must not differ.
    directory = Path(directory).resolve(strict=True)
    asset = directory / name
    shutil.copyfile(source, asset)
    with asset.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
            raise ValueError('Pinned Imager asset changed during preparation.')
    notify('Preparing verified Raspberry Pi Imager…')
    if key == 'win32':
        thumbprint = windows_signature(asset)['thumbprint']
        found = windows_find_engine(thumbprint)
        if found is None:
            notify('Installing verified Raspberry Pi Imager; system authorization is required…')
            process = elevated([str(asset), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-'])
            output = process.communicate()[0]
            if process.returncode:
                raise ValueError('Imager installation failed or was cancelled: ' + output[-1000:])
            found = windows_find_engine(thumbprint)
        if found is None:
            raise ValueError('No signed Imager with the pinned version was found in Program Files.')
        engine, engine_digest = found
        return engine, {'asset': str(asset), 'key': key, 'thumbprint': thumbprint, 'engine_sha256': engine_digest}
    if key == 'darwin':
        root = directory / 'Raspberry Pi Imager.app'
        mounted = plistlib.loads(command_output(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(asset)], stderr=subprocess.PIPE))
        mount = next(e['mount-point'] for e in mounted['system-entities'] if 'mount-point' in e)
        try:
            source_app = next(Path(mount).glob('*.app'))
            run_command(['ditto', str(source_app), str(root)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        finally:
            run_command(['hdiutil', 'detach', mount], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run_command(['codesign', '--verify', '--deep', '--strict', str(root)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        engine = root / 'Contents/MacOS/rpi-imager'
    else:
        asset.chmod(0o700)
        run_command([str(asset), '--appimage-extract'], cwd=directory, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        root = directory / 'squashfs-root'
        engine = root / 'AppRun'
    if not engine.is_file():
        raise ValueError('The verified Imager engine is missing.')
    return str(engine), {'root': str(root), 'asset': str(asset), 'key': key, 'tree': setup_tree_digest(root)}

def verify_setup_imager(engine, provenance):
    key = 'linux-' + platform.machine().lower() if sys.platform.startswith('linux') else sys.platform
    if key not in IMAGER_ASSETS or provenance.get('key') != key:
        raise ValueError('Unsupported Imager provenance.')
    asset = Path(provenance['asset'])
    expected_name, expected_hash = IMAGER_ASSETS[key]
    if key == 'win32':
        if asset.name != expected_name:
            raise ValueError('Invalid Imager installer location.')
        with asset.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_hash:
                raise ValueError('Pinned Imager installer changed before launch.')
        thumbprint = windows_signature(asset)['thumbprint']
        if thumbprint.upper() != provenance['thumbprint'].upper():
            raise ValueError('Imager installer signer changed before launch.')
        if windows_engine_metadata(engine, thumbprint) != provenance['engine_sha256']:
            raise ValueError('Installed Imager changed before launch.')
        return
    root = Path(provenance['root']).resolve(strict=True)
    expected_engine = root / ('Contents/MacOS/rpi-imager' if key == 'darwin' else 'AppRun')
    if Path(engine) != expected_engine or asset.name != expected_name or asset.parent != root.parent:
        raise ValueError('Invalid Imager engine location.')
    with asset.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_hash:
            raise ValueError('Pinned Imager asset changed before launch.')
    if setup_tree_digest(root) != provenance['tree']:
        raise ValueError('Imager engine changed before launch.')
    if key == 'darwin':
        run_command(['codesign', '--verify', '--deep', '--strict', str(root)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def check_source_drive(image, selected):
    """The image must survive erasing the destination drive."""
    if sys.platform == 'win32':
        letter = image.drive.rstrip(':')
        script = "Get-Partition -DriveLetter " + ps_literal(letter) + " -ErrorAction Stop | Select-Object -ExpandProperty DiskNumber"
        number = command_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], text=True).strip()
        if selected['device'] == '\\\\.\\PhysicalDrive' + number:
            raise ValueError('Move the image off the destination card before writing.')
    else:
        source = command_output(['df', '-P', str(image)], text=True).splitlines()[-1].split()[0]
        target = selected['device']
        if sys.platform == 'darwin':
            info = plistlib.loads(command_output(['diskutil', 'info', '-plist', source]))
            sources = [source] + ['/dev/' + store['APFSPhysicalStore'] for store in info.get('APFSPhysicalStores', [])]
        else:
            sources = [source]
        if any(part == target or re.fullmatch(re.escape(target) + r'(?:s|p)?[0-9]+', part) for part in sources):
            raise ValueError('Move the image off the destination card before writing.')


def engine_command(executable, image, device):
    # The image is hashed before invoking Imager. Its own read-back verification
    # remains enabled. --sha256 is omitted: compressed/raw hash semantics vary.
    flags = [] if sys.platform.startswith('linux') else ['--cli']
    return [executable, *flags, str(image), device]


def start_flash_worker(command):
    if sys.platform == 'darwin':
        # Imager's MacFile::authOpen requests the disk right in the user's
        # graphical session. An outer root session can make AuthorizationCreate
        # fail; Imager labels every such failure as "cancelled by user".
        return start_command(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
    return elevated(command)


def flash_worker(job_path):
    """Privileged, non-GUI worker: recheck the target after authorization."""
    job = json.loads(Path(job_path).read_text())
    log = Path(job_path).with_suffix('.log')
    with log.open('a', encoding='utf-8') as output:
        def report(message):
            output.write(json.dumps(message) + '\n')
            output.flush()
        try:
            report('Checking the image and selected drive…')
            image = verified(Path(job['image']), job['sha256'])
            validate_target(job['disk'], setup=job.get('setup') is True)
            check_source_drive(image, job['disk'])
            check_source_drive(Path(job['engine']), job['disk'])
            check_source_drive(Path(sys.executable), job['disk'])
            if job.get('setup') is True:
                verify_setup_imager(job['engine'], job['provenance'])
            report('Starting Imager; keep the reader connected…' if job.get('setup') is True
                   else 'Writing the card. Keep the reader connected…')
            process = start_command(engine_command(job['engine'], image, job['disk']['device']),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       errors='replace', env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
            tail = []
            for line in process.stdout:
                clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line).strip()
                if clean:
                    tail = (tail + [clean])[-12:]
                    report(clean)
                    if 'Authorization cancelled by user' in clean or 'Authorization failed' in clean:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise ValueError('macOS did not grant access to the card. This can be an authorization failure, not necessarily a cancellation. No write was started.')
            if process.wait():
                raise ValueError('\n'.join(tail) or 'Imager failed.')
            report({'success': True})
            return 0
        except Exception as error:
            report({'error': str(error)})
            return 1


def stage_image(image, checksum, notify=lambda text: None):
    """Give the privileged engine a verified source outside protected folders.

    macOS administrator authorization does not grant TCC access to Documents or
    Downloads. Copy while still running as the user who selected the image.
    """
    if not checksum or not re.fullmatch(r'[a-fA-F0-9]{64}', checksum):
        raise ValueError('A previously verified image checksum is required.')
    directory = cache_directory() / 'images'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (checksum.lower() + '-' + image.name)
    if destination.is_file():
        try:
            return verified(destination, checksum)
        except ValueError:
            pass  # A damaged cache entry must be replaced, never flashed.
    notify('Preparing a verified local copy for the writing engine…')
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=directory) as temporary:
        staged = Path(temporary) / destination.name
        with image.open('rb') as source, staged.open('wb') as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        verified(staged, checksum)
        staged.replace(destination)
    return destination.resolve()


def write_card(image, selected, notify=lambda text: None, checksum=None, setup=False, personalized=False):
    """Keep a fresh Setup engine alive until the worker has exited."""
    if setup:
        directory = cache_directory() / 'engines'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='setup-', dir=directory) as private:
            return _write_card(image, selected, notify, checksum, setup, private, personalized)
    return _write_card(image, selected, notify, checksum, setup, None, personalized)

def _write_card(image, selected, notify, checksum, setup, private, personalized):
    """Prepare the engine before the privileged worker revalidates the target."""
    validate_target(selected, isolated=True, setup=setup)
    check_source_drive(image, selected)
    executable, provenance = prepare_setup_imager(private, notify) if setup else (ensure_imager(notify), None)
    validate_target(selected, isolated=True, setup=setup)
    notify('Checking image integrity before writing…')
    if not checksum:
        raise ValueError('A previously verified image checksum is required.')
    image = verified(image, checksum) if personalized else stage_image(image, checksum, notify)
    check_source_drive(image, selected)
    with tempfile.TemporaryDirectory(prefix='astro-bridge-flash-') as directory:
        job_path = Path(directory) / 'job.json'
        job_path.write_text(json.dumps(dict(image=str(image), sha256=checksum, disk=selected, engine=executable, setup=setup, provenance=provenance)))
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
        notify('Imager will request permission to access the selected card…' if sys.platform == 'darwin'
               else 'Waiting for system authorization…')
        process = start_flash_worker([*command, '--flash-worker', str(job_path)])
        log_path = job_path.with_suffix('.log')
        offset = 0
        outcome = None
        while True:
            if log_path.exists():
                with log_path.open(encoding='utf-8') as stream:
                    stream.seek(offset)
                    while line := stream.readline():
                        if not line.endswith('\n'):
                            break
                        message = json.loads(line)
                        if isinstance(message, dict):
                            outcome = message
                        else:
                            notify(message)
                        offset = stream.tell()
            if process.poll() is not None:
                # Read the final worker message on the next pass, after process exit.
                if log_path.exists() and log_path.stat().st_size > offset:
                    continue
                break
            time.sleep(0.15)
        detail = process.communicate()[0]
        if process.returncode or not outcome or not outcome.get('success'):
            raise ValueError((outcome or {}).get('error') or 'Authorization cancelled or writing failed. ' + detail[-1000:])
    return image



def gui(initial_image=None, image_platform="alpine-standard"):
    """One installer window; elevated work never runs on the Tk event thread."""
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    window = tk.Tk()
    window.title("Astro Bridge · SD installer")
    window.geometry("820x850")
    window.minsize(740, 850)
    # Shared Astro Sync design tokens; clam keeps controls consistent on macOS.
    colors = dict(background="#0a0d0b", panel="#151916", border="#2c342f",
                  text="#eef0ee", muted="#9ca5a3", gold="#e0a85e", active="#342918")
    window.configure(background=colors["background"])
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(".", font=("Avenir Next", 12), background=colors["background"],
                    foreground=colors["text"])
    style.configure("TFrame", background=colors["background"])
    style.configure("Panel.TFrame", background=colors["panel"],
                    bordercolor=colors["border"], relief="solid", borderwidth=1)
    style.configure("TLabel", background=colors["background"], foreground=colors["text"])
    style.configure("Muted.TLabel", foreground=colors["muted"])
    style.configure("Panel.TLabel", background=colors["panel"])
    style.configure("Status.TLabel", background=colors["panel"], foreground=colors["gold"])
    style.configure("TButton", background=colors["panel"], foreground=colors["text"],
                    bordercolor=colors["border"], padding=(18, 12), focusthickness=2,
                    focuscolor=colors["gold"])
    style.map("TButton", background=[("active", colors["active"])],
              foreground=[("disabled", colors["muted"])])
    style.configure("Gold.TButton", background=colors["gold"], foreground=colors["background"])
    style.map("Gold.TButton", background=[("disabled", colors["border"]), ("active", "#ffe0ae")],
              foreground=[("disabled", colors["muted"])])
    style.configure("TProgressbar", background=colors["gold"], troughcolor=colors["panel"],
                    bordercolor=colors["border"], lightcolor=colors["gold"], darkcolor=colors["gold"])
    frame = ttk.Frame(window, padding=32)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="✧  Astro Bridge", foreground=colors["gold"],
              font=("Avenir Next", 24, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Prepare your Raspberry Pi", font=("Avenir Next", 18, "bold")).pack(anchor="w", pady=(16, 6))
    ttk.Label(frame, text="Choose an image and a card. Astro Bridge handles the rest.",
              style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(0, 22))
    panel = ttk.Frame(frame, padding=22, style="Panel.TFrame")
    panel.pack(fill="x")
    ttk.Label(panel, text="1   Choose your system image", style="Panel.TLabel",
              font=("Avenir Next", 14, "bold")).pack(anchor="w", pady=(0, 14))
    image_name = tk.StringVar(value=initial_image[0].name if initial_image else "No image selected yet.")
    ttk.Label(panel, textvariable=image_name, style="Panel.TLabel", wraplength=650).pack(anchor="w", pady=(0, 8))
    status = tk.StringVar(value="Choose an image to get started.")
    events = queue.Queue()
    ready = [initial_image]
    drives = []
    busy = [False]

    def show_progress(text):
        events.put(("progress", text))

    def selected_index():
        selection = drive_choice.curselection()
        return selection[0] if selection else -1

    def set_controls(active):
        download.configure(state="normal" if active else "disabled")
        local.configure(state="normal" if active else "disabled")
        platform_choice.configure(state="readonly" if active else "disabled")
        refresh.configure(state="normal" if active else "disabled")
        drive_choice.configure(state="normal" if active else "disabled")
        launch.configure(state="normal" if active and ready[0] and selected_index() >= 0 else "disabled")

    def begin(operation, kind="image"):
        busy[0] = True
        if kind == "image":
            ready[0] = None
            image_name.set("Checking the selected image…")
        set_controls(False)
        progress.configure(mode="indeterminate")
        progress.start(12)
        status.set("Preparing and checking the image…" if kind == "image" else "Preparing…")
        def run():
            try:
                result = operation()
                if kind == "image":
                    with result.open('rb') as stream:
                        result = (result, hashlib.file_digest(stream, 'sha256').hexdigest())
                events.put((kind, result))
            except Exception as error:
                events.put(("error", str(error)))
        threading.Thread(target=run, daemon=True).start()

    def choose():
        selected = filedialog.askopenfilename(filetypes=[("SD image", "*.img *.img.xz")])
        if not selected:
            return
        path = Path(selected)
        checksum=None
        for name in ('ALPINE-STANDARD-SHA256SUMS','SHA256SUMS','ALPINE-SHA256SUMS'):
            try:checksum=checksum_file(path.parent/name,path.name);break
            except (OSError,ValueError):continue
        if checksum is None:
            choice=filedialog.askopenfilename(title="Select the matching SHA256SUMS file")
            if not choice:return
            try:checksum=checksum_file(Path(choice),path.name)
            except (OSError,ValueError) as error:
                messagebox.showerror("Checksum missing or invalid",str(error));return
        begin(lambda: verified(path, checksum))

    def refresh_disks():
        begin(isolated_disks, "disks")

    def flash():
        index = selected_index()
        if not ready[0] or index < 0 or index >= len(drives):
            return
        selected = dict(drives[index])
        label = drive_choice.get(index)
        if not messagebox.askyesno("Erase this card?",
                "All data on this drive will be erased:\n\n" + label +
                "\n\nImage: " + ready[0][0].name +
                "\n\nContinue? Keep the reader connected until verification finishes.", icon="warning", default="no"):
            return
        begin(lambda: write_card(ready[0][0], selected, show_progress, ready[0][1]), "written")

    platform_labels={'Alpine standard (experimental)':'alpine-standard','Raspberry Pi OS (legacy images)':'raspios'}
    platform_value=tk.StringVar(value=next(label for label,value in platform_labels.items() if value==image_platform))
    platform_choice=ttk.Combobox(panel,textvariable=platform_value,values=list(platform_labels),state="readonly")
    platform_choice.pack(fill="x",pady=(0,6))
    def download_selected():
        selected=platform_labels[platform_value.get()]
        begin(lambda:latest(Path.home()/"Downloads/Astro-Bridge",selected))
    download = ttk.Button(panel, text="Download latest stable for selected platform", command=download_selected)
    download.pack(fill="x", pady=4)
    local = ttk.Button(panel, text="Choose a downloaded image…", command=choose)
    local.pack(fill="x", pady=4)
    ttk.Label(panel, textvariable=status, style="Status.TLabel", wraplength=650).pack(anchor="w", pady=(16, 8))
    progress = ttk.Progressbar(panel, mode="indeterminate")
    progress.pack(fill="x")
    ttk.Label(frame, text="2   Choose your SD card", font=("Avenir Next", 14, "bold")).pack(anchor="w", pady=(24, 10))
    drive_row = ttk.Frame(frame)
    drive_row.pack(fill="x")
    drive_choice = tk.Listbox(drive_row, height=2, exportselection=False, selectmode="browse",
                             font=("Avenir Next", 11), background=colors["panel"],
                             foreground=colors["text"], selectbackground=colors["active"],
                             selectforeground="#ffe0ae", highlightbackground=colors["border"],
                             highlightcolor=colors["gold"], relief="flat", borderwidth=0)
    drive_choice.pack(side="left", fill="x", expand=True, ipady=8)
    drive_choice.bind("<<ListboxSelect>>", lambda event: set_controls(not busy[0]))
    refresh = ttk.Button(drive_row, text="Refresh", command=refresh_disks)
    refresh.pack(side="right", padx=(10, 0))
    launch = ttk.Button(frame, text="Write and verify card →", style="Gold.TButton", command=flash, state="disabled")
    launch.pack(fill="x", pady=(14, 0))
    ttk.Label(frame, text="Writing erases the selected card. You will confirm its name and capacity before any write.\nThe official writing engine downloads automatically; the system may request authorization.",
              style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(12, 0))
    def poll():
        try:
            while True:
                kind, result = events.get_nowait()
                if kind == "progress":
                    status.set(result[-500:])
                    match = re.search(r'(Writing|Verifying).*?(\d+)\s*%', result)
                    if match:
                        progress.stop()
                        progress.configure(mode="determinate", value=int(match[2]))
                    continue
                progress.stop()
                progress.configure(mode="determinate", value=0)
                busy[0] = False
                if kind == "image":
                    ready[0] = result
                    image_name.set(result[0].name)
                    status.set("Verified: " + result[0].name)
                elif kind == "disks":
                    drives[:] = result
                    drive_choice.configure(state="normal")
                    drive_choice.delete(0, "end")
                    for d in drives:
                        drive_choice.insert("end", f"{d['name']} · {d['size'] / 1e9:.1f} GB · {d['device']}")
                    status.set("Select a removable drive." if drives else "No removable card found. Connect your reader, then refresh.")
                elif kind == "written":
                    progress.configure(value=100)
                    status.set("Card written and verified. You can now close the installer.")
                    messagebox.showinfo("Card ready", "Writing and read-back verification completed successfully. Eject the card before removing it.")
                else:
                    status.set("Operation failed or cancelled. Check the details below.")
                    messagebox.showerror("Unable to complete operation", result)
                set_controls(True)
        except queue.Empty:
            pass
        window.after(150, poll)

    def close():
        if busy[0]:
            messagebox.showinfo("Operation in progress", "Keep this window open until the operation finishes. Removing power while writing can leave the card unusable.")
        else:
            window.destroy()
    window.protocol("WM_DELETE_WINDOW", close)
    poll()
    refresh_disks()
    window.mainloop()


def setup_media(rows=None):
    # An ambiguous identity or device cannot designate one exact drive.
    rows = disks() if rows is None else rows
    eligible = [d for d in rows if isinstance(d.get('identity'), str)
                and d['identity'].strip() and d['identity'] != d.get('device')
                and isinstance(d.get('device'), str) and d['device']
                and isinstance(d.get('size'), int) and d['size'] > 0]
    return [d for d in eligible if sum(x['identity'] == d['identity'] for x in eligible) == 1
            and sum(x['device'] == d['device'] for x in eligible) == 1]

def setup_command():
    def emit(value):
        print(json.dumps(value), flush=True)
    try:
        request = json.loads(sys.stdin.readline(65537))
        if not isinstance(request, dict) or request.get('command') not in ('list', 'flash'):
            raise ValueError('Invalid setup request.')
        if request['command'] == 'list':
            emit({'media': setup_media()})
            return 0
        base = {'command', 'image', 'sha256', 'disk'}
        if not base <= set(request) <= base | {'wifi', 'password', 'drivers'}:
            raise ValueError('Invalid setup request fields.')
        disk = request['disk']
        if not isinstance(disk, dict) or disk not in setup_media():
            raise ValueError('The selected drive changed or disappeared.')
        image = verified(Path(request['image']), request['sha256'])
        emit({'phase': 'preparing', 'message': 'Checking expanded image size…'})
        wifi = request.get('wifi')
        password = request.get('password')
        drivers = request.get('drivers') or None
        personalized = wifi is not None or password is not None or drivers is not None
        size = 0
        opener = lzma.open if image.name.endswith('.xz') else open
        if not personalized:
            with opener(image, 'rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > disk['size']:
                        raise ValueError('The expanded image does not fit on this drive.')
            if not size:
                raise ValueError('The image is empty.')
        phase = 'preparing'
        def notify(message):
            nonlocal phase
            lower = message.lower()
            # Only Imager's stage lines advance the operation after authorization.
            # Earlier integrity checks are preparation, not card read-back.
            if phase in ('authorizing', 'media_preparing') and re.match(r'^(?:unmounting (?:drive|volumes)|opening drive|cleaning disk|waiting for drive to become available|zero[’\']?ing out)\b', lower):
                phase = 'media_preparing'
            elif phase in ('authorizing', 'media_preparing', 'writing') and re.match(r'^(?:verifying|verification|reading back)\b', lower):
                phase = 'flash_verifying'
            elif phase in ('authorizing', 'media_preparing') and re.match(r'^writing\b', lower):
                phase = 'writing'
            elif phase == 'preparing' and ('permission' in lower or 'authorization' in lower):
                phase = 'authorizing'
            emit({'phase': phase, 'message': message})
        if not personalized:
            write_card(image, disk, notify, request['sha256'], setup=True)
        else:
            emit({'phase': 'preparing', 'message': 'Preparing the image with the first-boot settings…'})
            with personalized_image(image, request['sha256'], disk['size'], wifi, password, drivers) as (raw, raw_checksum):
                password = None
                write_card(raw, disk, notify, raw_checksum, setup=True, personalized=True)
        emit({'success': True})
        return 0
    except Exception as error:
        request = locals().get('request')
        if isinstance(request, dict) and any(request.get(key) is not None for key in ('wifi', 'password', 'drivers')):
            message = str(error) if isinstance(error, ValueError) and str(error) in SAFE_PERSONALIZED_ERRORS else 'Unable to prepare or write the personalized image.'
            emit({'error': message})
        else:
            emit({'error': str(error)})
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup-protocol', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--list-disks', action='store_true', help='List eligible removable drives without writing')
    parser.add_argument('--flash-worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--gui', action='store_true', help='Open the native preparation window')
    parser.add_argument('--self-test', action='store_true', help='Check the bundled GUI and TLS runtime without accessing disks')
    parser.add_argument('--image', type=Path, help='Local .img/.img.xz (offline mode)')
    sums=parser.add_mutually_exclusive_group()
    sums.add_argument('--sha256', help='Expected SHA-256 for a local image')
    sums.add_argument('--checksum-file',type=Path,help='Matching local SHA256SUMS file')
    parser.add_argument('--platform',choices=tuple(IMAGE_PLATFORMS),default='alpine-standard',help='Latest stable image platform; never falls back across OS families')
    parser.add_argument('--cache', type=Path, default=Path.home() / 'Downloads/Astro-Bridge')
    parser.add_argument('--verify-only', action='store_true', help='Verify without launching or writing a disk')
    args = parser.parse_args()
    if args.setup_protocol:
        raise SystemExit(setup_command())
    if args.self_test:
        import tkinter
        window = tkinter.Tk(); window.withdraw(); window.update_idletasks(); window.destroy()
        network_context()
        print('PASS GUI and TLS runtime; no disk access')
        return
    if args.list_disks:
        print(json.dumps(disks(), indent=2))
        return
    if args.flash_worker:
        raise SystemExit(flash_worker(args.flash_worker))
    if not args.image and (args.gui or len(sys.argv) == 1):
        gui(image_platform=args.platform)
        return
    try:
        if args.image and not (args.sha256 or args.checksum_file):
            parser.error('--image requires --sha256 or --checksum-file from the matching candidate')
        if args.checksum_file and not args.image:parser.error('--checksum-file requires --image')
        checksum=checksum_file(args.checksum_file,args.image.name) if args.checksum_file else args.sha256
        image = verified(args.image,checksum) if args.image else latest(args.cache,args.platform)
        print('Verified image:', image)
        if args.verify_only:
            return
        with image.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        gui((image, checksum),args.platform)
    except Exception as error:
        print('Unable to prepare image:', type(error).__name__, str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
