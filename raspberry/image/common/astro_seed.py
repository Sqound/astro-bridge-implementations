"""The first-boot zone of every Astro Bridge card, shared by the Pi 5 and Pi Zero W images.

Astro Bridge Setup writes a few settings into a reserved 4 KiB zone at byte
65536 of the card, before the first partition; it is not a file and no
partition holds it. The image reads the zone once at first boot, keeps what it
needs in memory, then writes the blank reservation back with fsync.

Version 1 (marker ``ASTRO-WIFI-SEED-1``) carries only the private Wi-Fi network,
for the Raspberry Pi 5 layout. Version 2 (marker ``ASTRO-SEED-2``) carries, each
part optional: the private Wi-Fi network, the single bridge password (the only
place it is ever in clear, because the hotspot key depends on the radio
address, unknown when the card is written), and a pointer to the camera driver
drop appended after the image.

The blank reservation is the version 1 marker followed by zeros, so Setup
releases that only know version 1 keep writing the Wi-Fi network. An image that
reads version 2 also carries the reader mark at byte 69632; Setup writes version
2 only into an image that carries it. Both layouts below are the only ones
accepted: any other partition table means "no zone".

The camera driver drop starts after the last byte of the image, 4 KiB aligned:
a header (``ASTRO-DROP-1`` and one JSON line) then the manufacturer archives,
unchanged. Nothing in it is secret. The bridge checks every archive against its
own driver catalog, never against the card.

No function here ever puts the password in an exception, a file or a log.
"""
import hashlib
import json
import os
import re
import struct

SLOT_OFFSET, SLOT_SIZE = 65536, 4096
MARKER_V1 = b'ASTRO-WIFI-SEED-1\n'
MARKER_V2 = b'ASTRO-SEED-2\n'
READER_OFFSET = SLOT_OFFSET + SLOT_SIZE
READER_MARK = b'ASTRO-SEED-READER-2\n'
DROP_MAGIC = b'ASTRO-DROP-1\n'
ALIGN = 4096
MAX_HEADER = 65536
MAX_DROP_DRIVERS = 8
MAX_ARCHIVE = 512 * 1024 * 1024
SECTOR = 512

# (boot flag, accepted types, first sector, sectors) for p1; (first sector, sectors) for an ext4 p2.
LAYOUTS = {
    'pi5': {'p1': ({0x06}, 2048, 264192), 'p2': (266240, 2097152)},
    'zero-w': {'p1': ({0x0b, 0x0c}, 2048, 1048576), 'p2': (1050624, 2095104)},
}
for _layout in LAYOUTS.values():
    _layout['image_bytes'] = sum(_layout['p2']) * SECTOR

IDENT = re.compile('[a-z][a-z0-9_]{0,31}')
VERSION = re.compile(r'[0-9A-Za-z][0-9A-Za-z.+-]{0,31}')
SHA256 = re.compile('[0-9a-f]{64}')


class SeedError(ValueError):
    """A zone or drop that is not exactly what an image accepts. Never carries a value."""


def blank_slot():
    return MARKER_V1 + bytes(SLOT_SIZE - len(MARKER_V1))


def reader_block():
    return READER_MARK + bytes(SLOT_SIZE - len(READER_MARK))


def layout(mbr):
    """Name of the known card layout of this MBR, or None."""
    if len(mbr) < 512 or mbr[510:512] != b'\x55\xaa' or any(mbr[478:510]):
        return None
    first = struct.unpack_from('<B3sB3sII', mbr, 446)
    second = struct.unpack_from('<B3sB3sII', mbr, 462)
    for name, value in LAYOUTS.items():
        types, start, sectors = value['p1']
        if (first[0], first[4], first[5]) == (0x80, start, sectors) and first[2] in types \
                and (second[0], second[2], second[4], second[5]) == (0, 0x83, *value['p2']):
            return name
    return None


def reads_version_2(prefix):
    """True when the first bytes of an image carry the reader mark of version 2."""
    return prefix[READER_OFFSET:READER_OFFSET + SLOT_SIZE] == reader_block()


def read_slot(stream, size):
    """(layout name, raw zone) when the card has a known layout and a zone marker, else None."""
    stream.seek(0)
    mbr = stream.read(512)
    name = layout(mbr)
    if name is None or size < LAYOUTS[name]['image_bytes']:
        return None
    stream.seek(SLOT_OFFSET)
    raw = stream.read(SLOT_SIZE)
    if len(raw) != SLOT_SIZE or not (raw.startswith(MARKER_V1) or raw.startswith(MARKER_V2)):
        return None
    return name, raw


def is_blank(raw):
    return raw == blank_slot()


def holds_password(raw):
    """A written version 2 zone that may hold the password: never left on the card after a failure."""
    return raw is not None and raw.startswith(MARKER_V2) and b'"password"' in raw


def clear(stream):
    """Write the blank reservation back and make it durable."""
    stream.seek(SLOT_OFFSET)
    stream.write(blank_slot())
    stream.flush()
    os.fsync(stream.fileno())


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SeedError('duplicate')
        value[key] = item
    return value


def _line(raw, marker):
    payload = raw[len(marker):]
    if not payload.strip(b'\0'):
        return None
    line, separator, padding = payload.partition(b'\n')
    if not separator or any(padding):
        raise SeedError('padding')
    return json.loads(line.decode('ascii'), object_pairs_hook=_unique)


def _wifi(value, mac, validate_network):
    if (not isinstance(value, dict) or set(value) != {'ssid', 'psk', 'hidden', 'country'}
            or type(value['hidden']) is not bool or not isinstance(value['psk'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['psk']) or not isinstance(value['country'], str)
            or not re.fullmatch('[A-Z]{2}', value['country'])):
        raise SeedError('wifi')
    home = validate_network({'ssid': value['ssid'], 'password': value['psk'], 'hidden': value['hidden'], 'mac': mac},
                            {}, {mac})
    return home, value['country']


def _pointer(value, name):
    if (not isinstance(value, dict) or set(value) != {'offset', 'length', 'sha256'}
            or type(value['offset']) is not int or type(value['length']) is not int
            or not isinstance(value['sha256'], str) or not SHA256.fullmatch(value['sha256'])
            or value['offset'] < LAYOUTS[name]['image_bytes'] or value['offset'] % ALIGN
            or not 0 < value['length'] <= MAX_HEADER or value['length'] % ALIGN):
        raise SeedError('drivers')
    return dict(value)


def parse(raw, name, mac, validate_network, check_password):
    """What the zone asks for, or None when it is blank or invalid as a whole.

    Returns {'version', 'wifi': (home, country) or None, 'password': str or None,
    'drivers': pointer or None, 'problems': [...]}. A part that is invalid is
    dropped and named in problems (wifi, password, drivers); the others still
    apply. The password is kept only in this value, in memory.
    """
    try:
        if raw.startswith(MARKER_V2):
            data = _line(raw, MARKER_V2)
            if data is None:
                return None
            if not isinstance(data, dict) or not data or not set(data) <= {'wifi', 'password', 'drivers'}:
                raise SeedError('fields')
            version = 2
        elif raw.startswith(MARKER_V1):
            if name != 'pi5':
                return None
            data = _line(raw, MARKER_V1)
            if data is None:
                return None
            data = {'wifi': data}
            version = 1
        else:
            return None
    except (SeedError, UnicodeError, ValueError, RecursionError):
        return None
    result = {'version': version, 'wifi': None, 'password': None, 'drivers': None, 'problems': []}
    if 'wifi' in data:
        try:
            result['wifi'] = _wifi(data['wifi'], mac, validate_network)
        except Exception:
            result['problems'].append('wifi')
            if version == 1:
                return None
    if 'password' in data:
        try:
            result['password'] = check_password(data['password'])
        except Exception:
            result['problems'].append('password')
    if 'drivers' in data:
        try:
            result['drivers'] = _pointer(data['drivers'], name)
        except SeedError:
            result['problems'].append('drivers')
    return result


def sha512_crypt_hash(password, run):
    """A new $6$ hash for the Linux account, by openssl through stdin only."""
    result = run(['openssl', 'passwd', '-6', '-stdin'], input=password + '\n', text=True,
                 capture_output=True, check=True, timeout=60)
    value = result.stdout.strip()
    if not re.fullmatch(r'\$6\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}', value):
        raise SeedError('hash')
    return value


# --- camera driver drop -------------------------------------------------------

def _drop_entry(value):
    keys = {'id', 'version', 'arch', 'source', 'size', 'sha256', 'offset', 'license_sha256', 'terms_url'}
    if not isinstance(value, dict) or set(value) != keys:
        raise SeedError('entry')
    if (not isinstance(value['id'], str) or not IDENT.fullmatch(value['id'])
            or not isinstance(value['version'], str) or not VERSION.fullmatch(value['version'])
            or value['arch'] not in ('arm64', 'armv6') or value['source'] != 'download'
            or not (value['license_sha256'] is None or isinstance(value['license_sha256'], str)
                    and SHA256.fullmatch(value['license_sha256']))
            or not isinstance(value['terms_url'], str) or not value['terms_url'].startswith('https://')
            or len(value['terms_url']) > 512):
        raise SeedError('entry')
    if (type(value['size']) is not int or not 0 < value['size'] <= MAX_ARCHIVE
            or not isinstance(value['sha256'], str) or not SHA256.fullmatch(value['sha256'])
            or type(value['offset']) is not int or value['offset'] % ALIGN):
        raise SeedError('entry')
    return dict(value)


def read_drop(stream, pointer, size):
    """The validated drop header the zone points to. Raises SeedError otherwise."""
    if pointer['offset'] + pointer['length'] > size:
        raise SeedError('truncated')
    stream.seek(pointer['offset'])
    raw = stream.read(pointer['length'])
    if len(raw) != pointer['length'] or hashlib.sha256(raw).hexdigest() != pointer['sha256'] \
            or not raw.startswith(DROP_MAGIC):
        raise SeedError('header')
    try:
        data = _line(raw, DROP_MAGIC)
    except (UnicodeError, ValueError, RecursionError):
        raise SeedError('header') from None
    if (not isinstance(data, dict) or set(data) != {'catalog_version', 'drivers'}
            or not isinstance(data['catalog_version'], str) or len(data['catalog_version']) > 32
            or not isinstance(data['drivers'], list) or not 1 <= len(data['drivers']) <= MAX_DROP_DRIVERS):
        raise SeedError('header')
    entries, seen, end = [], set(), pointer['offset'] + pointer['length']
    for item in data['drivers']:
        entry = _drop_entry(item)
        if entry['id'] in seen:
            raise SeedError('duplicate')
        seen.add(entry['id'])
        # Archives follow the header in order, never overlapping; a short card is reported per archive.
        if entry['offset'] < end:
            raise SeedError('placement')
        end = entry['offset'] + entry['size']
        entries.append(entry)
    return {'catalog_version': data['catalog_version'], 'drivers': entries}


def copy_archive(stream, entry, destination, expected_size, expected_sha256):
    """Copy one archive from the card to a private file, checking the bridge catalog's size and SHA-256."""
    digest, remaining = hashlib.sha256(), expected_size
    if entry['size'] != expected_size or entry['sha256'] != expected_sha256:
        raise SeedError('archive_changed')
    stream.seek(entry['offset'])
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise SeedError('archive_truncated')
            digest.update(chunk)
            out.write(chunk)
            remaining -= len(chunk)
        out.flush()
        os.fsync(out.fileno())
    if digest.hexdigest() != expected_sha256:
        raise SeedError('archive_changed')
    return destination


def clear_drop(stream, pointer):
    """Erase the drop header: the archives behind it are public and no longer reachable."""
    stream.seek(pointer['offset'])
    stream.write(bytes(pointer['length']))
    stream.flush()
    os.fsync(stream.fileno())


def apply_drop(stream, pointer, size, *, catalog, arch, install, workdir):
    """Install each driver of the drop, then erase the header. Never raises.

    catalog is the bridge's own driver catalog; every expected fingerprint comes
    from it. install(vendor, build, archive_path) installs a downloaded build:
    no manufacturer file is ever part of an image. Returns {vendor: outcome}:
    installed, or a plain failure code (unknown_driver, unknown_build, wrong_architecture,
    fingerprint_missing, archive_changed, archive_truncated, licence_changed,
    invalid_drop, ...). One refused driver never stops the others or the boot.
    """
    results = {}
    try:
        header = read_drop(stream, pointer, size)
    except (SeedError, OSError):
        results['*'] = 'invalid_drop'
        header = None
    if header:
        entries = {entry['id']: entry for entry in catalog['drivers']}
        for item in header['drivers']:
            vendor = item['id']
            try:
                entry = entries.get(vendor)
                if entry is None:
                    raise SeedError('unknown_driver')
                if item['arch'] != arch:
                    raise SeedError('wrong_architecture')
                builds = [build for build in entry['builds'] if build['arch'] == arch
                          and build['source'] == item['source'] and build['version'] == item['version']]
                if not builds:
                    raise SeedError('unknown_build')
                build = builds[0]
                if build['license_sha256'] != item['license_sha256']:
                    raise SeedError('licence_changed')
                if build['size'] is None or build['sha256'] is None:
                    raise SeedError('fingerprint_missing')
                path = os.path.join(workdir, '.drop-' + vendor)
                if os.path.lexists(path):
                    os.unlink(path)
                try:
                    copy_archive(stream, item, path, build['size'], build['sha256'])
                    install(vendor, build, path)
                finally:
                    if os.path.lexists(path):
                        os.unlink(path)
                results[vendor] = 'installed'
            except Exception as error:
                code = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]{3,40}', str(error)) else 'install_failed'
                results[vendor] = code
    try:
        clear_drop(stream, pointer)
    except OSError:
        results['*'] = results.get('*', 'clear_failed')
    return results
