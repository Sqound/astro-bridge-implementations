#!/usr/bin/python3
"""Create a durable identity and optionally consume the reserved Wi-Fi seed."""
import fcntl, json, os, re, stat, struct, sys, time
from pathlib import Path
STATE = Path('/var/lib/wifi-bridge')
SEED_MAGIC = b'ASTRO-WIFI-SEED-1\n'
SEED_OFFSET, SEED_SIZE = 65536, 4096
BLKGETSIZE64 = 0x80081272


def seed_slot(stream, size):
    """Only the expected MBR layout and explicit marker authorize slot access."""
    if size < (266240 + 2097152) * 512:
        return None
    stream.seek(0); mbr = stream.read(512)
    if len(mbr) != 512 or mbr[510:] != b'\x55\xaa' or any(mbr[478:510]):
        return None
    p1 = struct.unpack_from('<B3sB3sII', mbr, 446)
    p2 = struct.unpack_from('<B3sB3sII', mbr, 462)
    if p1[0] != 128 or p1[2] != 6 or p1[4:] != (2048, 264192) or p2[0] != 0 or p2[2] != 0x83 or p2[4:] != (266240, 2097152):
        return None
    stream.seek(SEED_OFFSET); raw = stream.read(SEED_SIZE)
    if len(raw) != SEED_SIZE or not raw.startswith(SEED_MAGIC):
        return None
    return raw


def parse_seed(raw, mac):
    """Reject duplicate keys, trailing bytes, and unvalidated network metadata."""
    from wifi_bridge.config import validate_network
    payload = raw[len(SEED_MAGIC):]
    if not payload.strip(b'\0'):
        return None
    try:
        line, padding = payload.split(b'\n', 1)
        if any(padding): raise ValueError('padding')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value: raise ValueError('duplicate')
                value[key] = item
            return value
        seed = json.loads(line.decode('ascii'), object_pairs_hook=unique)
        if (not isinstance(seed, dict) or set(seed) != {'ssid','psk','hidden','country'} or
                type(seed['hidden']) is not bool or not isinstance(seed['psk'], str) or
                not re.fullmatch('[0-9a-f]{64}', seed['psk']) or not isinstance(seed['country'], str) or
                not re.fullmatch('[A-Z]{2}', seed['country'])):
            raise ValueError('invalid seed')
        home = validate_network({'ssid': seed['ssid'], 'password': seed['psk'], 'hidden': seed['hidden'], 'mac': mac}, {}, {mac})
        return home, seed['country']
    except Exception:
        return None


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
        return stream, raw
    except (OSError, ValueError):
        if fd is not None: os.close(fd)
        return None, None


def clear_seed(stream):
    stream.seek(SEED_OFFSET)
    stream.write(SEED_MAGIC + bytes(SEED_SIZE-len(SEED_MAGIC)))
    stream.flush(); os.fsync(stream.fileno())


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


def initial_config(mac, raw):
    from wifi_bridge.config import default_config
    config = default_config(mac)
    config['hotspot'] = {'enabled': True, 'mac': mac, 'ssid': 'Astro-Bridge-'+mac.replace(':','')[-6:].upper()}
    config['interface_roles'] = {mac: 'hotspot'}
    config['connection_mode'] = 'hotspot_shared'
    seed = parse_seed(raw, mac) if raw else None
    if seed:
        config['home'], config['country'] = seed
        config['interface_roles'] = {mac: 'auto'}
        config['hotspot'] = dict(config['hotspot'], enabled=True, mac='')
    return config


def main():
    if STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o077: raise ValueError('state ownership/mode')
    sys.path.insert(0, str(STATE/'app'))
    boot, cfg, done, pending = (STATE/name for name in ('bootstrap.json','config.json','identity-ready','identity-pending.json'))
    stream, raw = open_seed_device()
    try:
        if done.exists():
            if not boot.is_file() or not cfg.is_file() or boot.stat().st_mode & 0o077: raise ValueError('incomplete identity')
        else:
            if pending.exists():
                fd = os.open(pending, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                with os.fdopen(fd, 'rb') as f:
                    info = os.fstat(f.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                        raise ValueError('invalid identity transaction')
                    raw_pending = f.read(65537)
                if len(raw_pending) > 65536: raise ValueError('invalid identity transaction')
                value = json.loads(raw_pending)
                if not isinstance(value, dict) or set(value) != {'bootstrap','config'}: raise ValueError('invalid identity transaction')
            else:
                if boot.exists() or cfg.exists(): raise ValueError('partial identity requires console recovery')
                deadline = time.monotonic()+30
                radio = Path('/sys/class/net/wlan0/address')
                while not radio.exists() and time.monotonic()<deadline: time.sleep(1)
                iface = radio.parent
                if (iface/'device/driver').resolve(strict=True).name != 'brcmfmac' or (iface/'device/subsystem').resolve(strict=True).name != 'sdio': raise ValueError('wlan0 is not onboard brcmfmac SDIO')
                mac = radio.read_text().strip().lower()
                if not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', mac): raise ValueError('invalid onboard MAC')
                config = initial_config(mac, raw)
                value = {'bootstrap': {'password': '12345678'}, 'config': config}
                atomic_json(pending, value)
            atomic_json(boot, value['bootstrap']); atomic_json(cfg, value['config'])
            fd = os.open(done, os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
            os.fsync(fd); os.close(fd); sync_state()
        if stream and raw != SEED_MAGIC + bytes(SEED_SIZE-len(SEED_MAGIC)):
            clear_seed(stream)
        if pending.exists(): pending.unlink(); sync_state()
    finally:
        if stream: stream.close()
    with open('/dev/console','w') as console:
        console.write('\nAstro Bridge: connect using the local welcome screen. Diagnostic console: Alt+F1.\nIf needed, log in locally as root with the initial password of the guide, then change it: passwd, then rc-service astro-console-account save.\n\n')
if __name__=='__main__':
    try: main()
    except Exception as e: print('Astro first boot:',e,file=sys.stderr); sys.exit(1)
