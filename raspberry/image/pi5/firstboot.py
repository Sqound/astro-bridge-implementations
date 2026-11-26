#!/usr/bin/python3
"""Create a durable identity and consume the first-boot zone written by Astro Bridge Setup.

The zone (see astro_seed.py) may carry the private Wi-Fi network, the single
bridge password and a pointer to camera drivers. The password stays in memory:
only derived values are ever written (the web hash, the hotspot WPA key bound to
the hotspot name, the root crypt hash), first in identity-pending.json, then
the zone is erased with fsync, then the identity is applied. If deriving fails,
the bridge starts with the default password 12345678 and says so.
"""
import fcntl, json, os, re, stat, struct, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

for _folder in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1] / 'common'):
    if (_folder / 'astro_seed.py').is_file():
        sys.path.insert(0, str(_folder))
        break
import astro_seed  # noqa: E402

STATE = Path('/var/lib/wifi-bridge')
SEED_MAGIC = astro_seed.MARKER_V1
SEED_OFFSET, SEED_SIZE = astro_seed.SLOT_OFFSET, astro_seed.SLOT_SIZE
BLKGETSIZE64 = 0x80081272
NOTICE = 'firstboot-notice.json'


def seed_slot(stream, size):
    """Only the Pi 5 MBR layout and an explicit zone marker authorize slot access."""
    found = astro_seed.read_slot(stream, size)
    return found[1] if found and found[0] == 'pi5' else None


def parse_any(raw, mac):
    from wifi_bridge.config import validate_network
    from wifi_bridge.password import check
    return astro_seed.parse(raw, 'pi5', mac, validate_network, check) if raw else None


def parse_seed(raw, mac):
    """The Wi-Fi part only: (home, country) or None (kept for the version 1 callers)."""
    seed = parse_any(raw, mac)
    return seed['wifi'] if seed else None


def open_seed_device():
    """Never follow a device link or touch a different disk."""
    fd = None
    try:
        fd = os.open('/dev/mmcblk0', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_uid != 0 or (os.major(info.st_rdev), os.minor(info.st_rdev)) != (179, 0):
            raise ValueError('device mismatch')
        size = struct.unpack('Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0]
        stream = os.fdopen(fd, 'r+b', buffering=0); fd = None
        raw = seed_slot(stream, size)
        if raw is None:
            stream.close(); return None, None
        stream.device_size = size
        return stream, raw
    except (OSError, ValueError):
        if fd is not None: os.close(fd)
        return None, None


def clear_seed(stream):
    astro_seed.clear(stream)


def atomic_json(path, value):
    tmp = path.with_name('.' + path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        os.fchmod(f.fileno(), 0o600); json.dump(value, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    sync_state()


def sync_state():
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def seeded_config(mac, seed):
    from wifi_bridge.config import default_config
    config = default_config(mac)
    # No explicit name: the automatic one (astro-bridge-<6 hex, lowercase>) is shared with every other hardware.
    config['hotspot'] = {'enabled': True, 'mac': mac}
    config['interface_roles'] = {mac: 'hotspot'}
    config['connection_mode'] = 'hotspot_shared'
    if seed and seed['wifi']:
        config['home'], config['country'] = seed['wifi']
        config['interface_roles'] = {mac: 'auto'}
        config['hotspot'] = dict(config['hotspot'], enabled=True, mac='')
    return config


def initial_config(mac, raw):
    return seeded_config(mac, parse_any(raw, mac) if raw else None)


def derive_password(config, password, mac, run=None):
    """The three derived values of the bridge password; the clear value is never returned."""
    from wifi_bridge.config import hotspot_ssid, password_hash
    from wifi_bridge.password import hotspot_key
    root = astro_seed.sha512_crypt_hash(password, run or subprocess.run)
    # The key binds the automatic name of the onboard radio; that name then stays the hotspot name on every radio.
    config = dict(config, admin_hash=password_hash(password),
                  hotspot_key=hotspot_key(password, hotspot_ssid(config, mac)), linux_password_default=False)
    return config, root


def new_identity(mac, raw, run=None):
    """The identity transaction: derived values only, never the clear password."""
    seed = parse_any(raw, mac) if raw else None
    config = seeded_config(mac, seed)
    value = {'bootstrap': {'password': '12345678'}, 'config': config}
    notice = list(seed['problems']) if seed else []
    if seed and seed['password']:
        try:
            value['config'], value['console_root'] = derive_password(config, seed['password'], mac, run)
        except Exception:
            notice.append('password')  # The default password stays in use, and the bridge says so.
        seed['password'] = None
    if seed and seed['drivers']:
        value['drivers'] = seed['drivers']
    if notice:
        value['notice'] = sorted(set(notice))
    return value


def read_pending(pending):
    fd = os.open(pending, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError('invalid identity transaction')
        raw_pending = f.read(65537)
    if len(raw_pending) > 65536: raise ValueError('invalid identity transaction')
    value = json.loads(raw_pending)
    if not isinstance(value, dict) or not {'bootstrap','config'} <= set(value) <= {'bootstrap','config','console_root','drivers','notice'}:
        raise ValueError('invalid identity transaction')
    return value


def write_console_root(value):
    """The root hash astro-console-account applies right after this service."""
    if not isinstance(value, str) or not re.fullmatch(r'\$6\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}', value):
        raise ValueError('invalid root hash')
    atomic_json(STATE/'console-root.json', {'version': 1, 'hash': value})


def apply_drivers(stream, pointer, run_clock=time.time):
    """Install the drivers Setup placed after the image; checked against this bridge's catalog."""
    sys.path.insert(0, str(STATE/'app'))
    from wifi_bridge.cameras import catalog, drivers
    clock = {'clock_status': 'unverified', 'corrected_utc': datetime.fromtimestamp(run_clock(), timezone.utc).isoformat()}
    def install(vendor, build, path):
        drivers.install_archive(vendor, build, path, clock, 'setup', drivers=drivers.DRIVERS)
    drivers.DRIVERS.mkdir(mode=0o700, parents=True, exist_ok=True)
    size = getattr(stream, 'device_size', None) or os.fstat(stream.fileno()).st_size
    results = astro_seed.apply_drop(stream, pointer, size, catalog=catalog.catalog(),
                                    arch='arm64', install=install, workdir=str(drivers.DRIVERS))
    atomic_json(drivers.DRIVERS/'setup-results.json', results)
    return results


def main():
    if STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o077: raise ValueError('state ownership/mode')
    sys.path.insert(0, str(STATE/'app'))
    boot, cfg, done, pending = (STATE/name for name in ('bootstrap.json','config.json','identity-ready','identity-pending.json'))
    stream, raw = open_seed_device()
    value = None
    blank = astro_seed.blank_slot()
    try:
        if done.exists():
            if not boot.is_file() or not cfg.is_file() or boot.stat().st_mode & 0o077: raise ValueError('incomplete identity')
        else:
            if pending.exists():
                value = read_pending(pending)
            else:
                if boot.exists() or cfg.exists(): raise ValueError('partial identity requires console recovery')
                deadline = time.monotonic()+30
                radio = Path('/sys/class/net/wlan0/address')
                while not radio.exists() and time.monotonic()<deadline: time.sleep(1)
                iface = radio.parent
                if (iface/'device/driver').resolve(strict=True).name != 'brcmfmac' or (iface/'device/subsystem').resolve(strict=True).name != 'sdio': raise ValueError('wlan0 is not onboard brcmfmac SDIO')
                mac = radio.read_text().strip().lower()
                if not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', mac): raise ValueError('invalid onboard MAC')
                value = new_identity(mac, raw)
                atomic_json(pending, value)
            # The transaction holds derived values only: the clear password leaves the card now.
            if stream and raw != blank:
                clear_seed(stream); raw = blank
            atomic_json(boot, value['bootstrap']); atomic_json(cfg, value['config'])
            if value.get('console_root'): write_console_root(value['console_root'])
            if value.get('notice'): atomic_json(STATE/NOTICE, {'not_applied': value['notice']})
            fd = os.open(done, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
            os.fsync(fd); os.close(fd); sync_state()
        if stream and raw != blank:
            clear_seed(stream)
        if pending.exists():
            value = value or read_pending(pending)
            if value.get('drivers') and stream:
                try:
                    apply_drivers(stream, value['drivers'])
                except Exception:
                    print('Astro first boot: camera drivers not installed', file=sys.stderr)
            pending.unlink(); sync_state()
    finally:
        if stream:
            try:
                # Whatever failed, the password never stays on the card.
                # A Wi-Fi-only zone is kept for the next attempt, as before.
                if astro_seed.holds_password(raw): clear_seed(stream)
            finally:
                stream.close()
    with open('/dev/console','w') as console:
        if value and 'password' in value.get('notice', []):
            console.write('\nAstro Bridge: the password chosen in Setup was not applied. The default password 12345678 is in use; change it in the web interface.\n')
        console.write('\nAstro Bridge: connect using the local welcome screen. Diagnostic console: Alt+F1.\nIf needed, log in locally as root with the bridge password (12345678 until you change it). SSH (port 22) accepts root with the bridge password once it is no longer 12345678, or a public key in /var/lib/wifi-bridge/ssh/authorized_keys.\n\n')
if __name__=='__main__':
    try: main()
    except Exception as e: print('Astro first boot:',e,file=sys.stderr); sys.exit(1)
