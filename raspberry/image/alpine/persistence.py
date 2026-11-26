#!/usr/bin/python3
"""Fixed diskless identity/time tasks; independent of the OTA application tree."""
import contextlib
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time

# The SSH rule shared with the Raspberry Pi 5 image: /usr/libexec in the image,
# image/common in the source tree.
for _folder in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1] / 'common'):
    if (_folder / 'astro_ssh_rule.py').is_file():
        sys.path.insert(0, str(_folder))
        break
import astro_ssh_rule  # noqa: E402
import astro_seed  # noqa: E402  (same folder: /usr/libexec in the image, image/common in the tree)

STATE = '/var/lib/wifi-bridge'
SSH_DROP_IN = '/etc/ssh/sshd_config.d/astro-bridge.conf'
KEYS = ('ssh_host_ed25519_key', 'ssh_host_rsa_key', 'ssh_host_ecdsa_key')
MIN_TIME, MAX_TIME = 1577836800, 4102444800  # 2020 through 2100; never proof of sync.
APP = '/opt/wifi-bridge'  # The application of this image (the data copy may be a newer OTA).
BLKGETSIZE64 = 0x80081272
SEED_KEYS = {'config', 'bridge_hash', 'drivers', 'notice'}


class Persistence:
    # Root injection is for offline tests only. CLI accepts fixed action names,
    # never a root/path/account supplied by a caller or environment variable.
    def __init__(self, root=Path('/'), run=subprocess.run, now=time.time,
                 set_time=lambda value: time.clock_settime(time.CLOCK_REALTIME, value), seed_device=None):
        self.root, self.run, self.now, self.set_time = Path(root), run, now, set_time
        self.seed_device = seed_device or self.card_seed
        self.crypt = astro_ssh_rule.sha512_crypt
        self.state = self.path(STATE)
        self.identity = self.state / 'identity'

    def path(self, name):
        return self.root / name.lstrip('/')

    def mounted(self):
        """Refuse a RAM fallback or a wrong/missing/read-only data mount."""
        label = self.path('/dev/disk/by-label/ASTRO_DATA')
        if not label.exists():
            raise RuntimeError('Persistent volume unavailable')
        for line in self.path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            try:
                separator = fields.index('-')
                if separator < 6 or len(fields) <= separator + 3:
                    raise ValueError
            except ValueError:
                raise RuntimeError('Invalid mount table') from None
            if fields[4] == STATE:
                source = self.path(fields[separator + 2])
                if (fields[separator + 1] == 'ext4' and 'rw' in fields[5].split(',')
                        and 'rw' in fields[separator + 3].split(',')
                        and source.resolve() == label.resolve()):
                    self.private_directory(self.state)
                    return
        raise RuntimeError('Persistent volume unavailable')

    @staticmethod
    def private_directory(path):
        if path.is_symlink():
            raise RuntimeError('Unsafe private directory')
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Unsafe private directory')

    @staticmethod
    def read_private(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError('Unsafe private file')
            if info.st_size > 32768:
                raise RuntimeError('Oversized private file')
            return stream.read()

    @staticmethod
    def sync_directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @classmethod
    def atomic(cls, path, content, mode=0o600):
        fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path)
            cls.sync_directory(path.parent)
        finally:
            Path(name).unlink(missing_ok=True)

    @contextlib.contextmanager
    def locked(self):
        self.mounted()
        self.private_directory(self.identity)
        fd = os.open(self.identity / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError('Unsafe identity lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def command(self, args, **kwargs):
        return self.run(args, check=True, capture_output=True, text=True, **kwargs)

    @staticmethod
    def valid_hash(value):
        if (not isinstance(value, str) or not 1 <= len(value) <= 1024
                or any(ord(c) < 33 or ord(c) > 126 or c == ':' for c in value)
                or not value.startswith(('$', '!', '*'))):
            raise ValueError('Invalid fixed-account hash')
        return value

    def shadow_hash(self):
        lines = self.path('/etc/shadow').read_text().splitlines()
        entries = [line.split(':') for line in lines if line.startswith('bridge:')]
        if len(entries) != 1 or len(entries[0]) != 9:
            raise ValueError('Missing fixed account')
        return self.valid_hash(entries[0][1])

    def save_password(self):
        data = json.dumps({'version': 1, 'hash': self.shadow_hash()}).encode()
        self.atomic(self.identity / 'bridge-password.json', data)

    def stored_hash(self):
        data = json.loads(self.read_private(self.identity / 'bridge-password.json'))
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('Invalid fixed-account state')
        return self.valid_hash(data.get('hash'))

    def set_hash(self, value):
        self.command(['/usr/sbin/chpasswd', '-e'], input='bridge:' + value + '\n')

    def account(self):
        saved = self.identity / 'bridge-password.json'
        # Validate stored state before creating or changing any account.
        value = self.stored_hash() if saved.exists() or saved.is_symlink() else None
        exists = any(line.startswith('bridge:') for line in self.path('/etc/passwd').read_text().splitlines())
        if not exists:
            self.command(['/usr/sbin/adduser', '-D', '-h', '/home/bridge', '-s', '/bin/ash', '-G', 'wheel', 'bridge'])
            if value is None:
                bootstrap = json.loads(self.read_private(self.path('/etc/wifi-bridge/bootstrap.json')))
                self.command(['/usr/sbin/chpasswd', '-c', 'SHA512'], input='bridge:' + self.password(bootstrap['password']) + '\n')
        if value is not None:
            self.set_hash(value)
        else:
            self.save_password()  # Adopt existing credentials, including locked accounts.
        sudoers = self.path('/etc/sudoers.d/bridge')
        self.atomic(sudoers, b'bridge ALL=(ALL:ALL) ALL\n', 0o440)

    @staticmethod
    def password(value):
        if not isinstance(value, str) or not 8 <= len(value) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Invalid password request')
        return value

    def change_password(self):
        pending = self.state / 'linux-password.pending'
        try:
            with self.locked():
                password = self.password(self.read_private(pending).decode())
                self.stored_hash()  # Provisioning must have completed successfully.
                old_record = self.read_private(self.identity / 'bridge-password.json')
                old_hash = self.shadow_hash()
                # SHA-512 crypt: the hash scheme the SSH rule can check.
                self.command(['/usr/sbin/chpasswd', '-c', 'SHA512'], input='bridge:' + password + '\n')
                try:
                    self.save_password()
                except Exception:
                    try:
                        self.atomic(self.identity / 'bridge-password.json', old_record)
                    finally:
                        self.set_hash(old_hash)
                    raise
        finally:
            pending.unlink(missing_ok=True)
        # The password is changed and kept: SSH follows it. Never fails the change.
        try:
            self.ssh_access()
            if self.root == Path('/'):
                self.command(['/sbin/rc-service', '--ifstarted', 'sshd', 'reload'])
        except Exception:
            pass

    def ssh_access(self):
        """Same rule as the Pi 5 image: SSH accepts bridge's password only once it is not the initial one.

        Written before sshd starts at every boot, and after each password change.
        In any doubt password login stays off. Returns what SSH now accepts.
        """
        try:
            allowed = astro_ssh_rule.password_changed(self.shadow_hash(), astro_ssh_rule.initial_password(
                self.path('/etc/wifi-bridge/bootstrap.json')), self.crypt)
        except Exception:
            allowed = False
        drop_in = self.path(SSH_DROP_IN)
        drop_in.parent.mkdir(parents=True, exist_ok=True)
        lines = ['PermitRootLogin no', 'AllowUsers bridge', 'PubkeyAuthentication yes',
                 'PasswordAuthentication ' + ('yes' if allowed else 'no'), 'KbdInteractiveAuthentication no',
                 'PermitEmptyPasswords no', 'MaxAuthTries 3', 'LoginGraceTime 30', '']
        self.atomic(drop_in, '\n'.join(lines).encode(), 0o644)
        astro_ssh_rule.write_state(self.path('/run/astro-ssh'), allowed)
        return allowed

    def host_keys(self):
        destination = self.identity / 'ssh'
        runtime = self.path('/etc/ssh')
        if not destination.exists() and not destination.is_symlink():
            staging = Path(tempfile.mkdtemp(prefix='.ssh-', dir=self.identity))
            try:
                for name in KEYS:
                    source = runtime / name
                    if source.exists() or source.is_symlink():
                        self.atomic(staging / name, self.read_private(source))
                if not any((staging / name).exists() for name in KEYS):
                    self.command(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(staging / KEYS[0])])
                # Validate private keys before publishing a complete identity directory.
                for name in KEYS:
                    if (staging / name).exists():
                        public = self.command(['/usr/bin/ssh-keygen', '-y', '-f', str(staging / name)]).stdout
                        self.atomic(staging / (name + '.pub'), public.encode(), 0o644)
                self.sync_directory(staging)
                os.rename(staging, destination); self.sync_directory(self.identity)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        self.private_directory(destination)
        present = [name for name in KEYS if (destination / name).exists()]
        if not present:
            raise RuntimeError('Persistent SSH identity incomplete')
        public_keys = {}
        for name in present:
            self.read_private(destination / name)
            public_keys[name] = self.command(['/usr/bin/ssh-keygen', '-y', '-f', str(destination / name)]).stdout.encode()
        # Repair missing sidecars and validate every key before changing RAM links.
        for name, public in public_keys.items():
            self.atomic(destination / (name + '.pub'), public, 0o644)
        for name in present:
            for filename in (name, name + '.pub'):
                target = runtime / filename
                temporary = runtime / ('.' + filename + '.persistent')
                temporary.unlink(missing_ok=True)
                # Absolute runtime paths; offline test roots still resolve correctly.
                temporary.symlink_to(destination / filename)
                os.replace(temporary, target)
        # sshd_disable_keygen prevents absent algorithms from being regenerated in RAM.

    def restore_time(self):
        path = self.state / 'clock-lower-bound'
        try:
            value = int(self.read_private(path))
            now = self.now()
            if MIN_TIME <= value <= MAX_TIME and now < value:
                self.set_time(value)
        except (OSError, ValueError, RuntimeError):
            pass  # A bad checkpoint cannot block identity/network recovery.

    def checkpoint_time(self):
        self.mounted()
        value = int(self.now())
        if MIN_TIME <= value <= MAX_TIME:
            self.atomic(self.state / 'clock-lower-bound', str(value).encode())

    def close_ssh(self):
        drop_in = self.path(SSH_DROP_IN)
        drop_in.parent.mkdir(parents=True, exist_ok=True)
        self.atomic(drop_in, b'PermitRootLogin no\nAllowUsers bridge\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n', 0o644)
        astro_ssh_rule.write_state(self.path('/run/astro-ssh'), False)

    # --- first-boot zone written by Astro Bridge Setup (image/common/astro_seed.py) ---

    @staticmethod
    def card_seed():
        """(stream, raw zone, card size) of the Pi Zero W card, or (None, None, 0). Never another disk."""
        fd = None
        try:
            fd = os.open('/dev/mmcblk0', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(fd)
            if not stat.S_ISBLK(info.st_mode) or info.st_uid != 0 or (os.major(info.st_rdev), os.minor(info.st_rdev)) != (179, 0):
                raise ValueError('device mismatch')
            size = struct.unpack('Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0]
            stream = os.fdopen(fd, 'r+b', buffering=0)
            fd = None
            found = astro_seed.read_slot(stream, size)
            if not found or found[0] != 'zero-w':
                stream.close()
                return None, None, 0
            return stream, found[1], size
        except (OSError, ValueError):
            if fd is not None:
                os.close(fd)
            return None, None, 0

    def app_modules(self):
        app = str(self.path(APP))
        if os.path.isdir(app) and app not in sys.path:
            sys.path.insert(0, app)
        from wifi_bridge import config, password
        return config, password

    def seed_identity(self, raw):
        """Derived values for the first boot: never the clear password. None when the zone asks nothing."""
        config, password = self.app_modules()
        mac = self.path('/sys/class/net/wlan0/address').read_text().strip().lower()
        if not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', mac):
            raise RuntimeError('Invalid onboard radio address')
        seed = astro_seed.parse(raw, 'zero-w', mac, config.validate_network, password.check)
        if seed is None:
            return None
        value = config.default_config(mac)
        if seed['wifi']:
            # Same as the Pi 5: the radio joins the private network and keeps the hotspot as fallback.
            value['home'], value['country'] = seed['wifi']
            value['interface_roles'] = {mac: 'auto'}
            value['hotspot'] = dict(value.get('hotspot', {}), enabled=True, mac='')
        result = {'config': value, 'bridge_hash': None, 'drivers': seed['drivers'], 'notice': list(seed['problems'])}
        clear, seed['password'] = seed['password'], None
        if clear:
            try:
                derived = dict(value, admin_hash=config.password_hash(clear),
                               hotspot_key=password.hotspot_key(clear, config.hotspot_ssid(value, mac)),
                               linux_password_default=False)
                result['bridge_hash'] = astro_seed.sha512_crypt_hash(clear, self.run)
                result['config'] = derived
            except Exception:
                result['notice'].append('password')  # The default password stays, and the bridge says so.
            finally:
                clear = None
        result['notice'] = sorted(set(result['notice']))
        return result

    def apply_drivers(self, stream, pointer, size):
        """Camera drivers placed by Setup, checked against this bridge's own catalog."""
        self.app_modules()
        from wifi_bridge.cameras import catalog, drivers
        folder = self.state / 'camera-drivers'
        folder.mkdir(mode=0o700, exist_ok=True)
        clock = {'clock_status': 'unverified', 'corrected_utc': datetime.fromtimestamp(self.now(), timezone.utc).isoformat()}

        def install(vendor, build, path):
            drivers.install_archive(vendor, build, path, clock, 'setup', drivers=folder)

        results = astro_seed.apply_drop(stream, pointer, size, catalog=catalog.catalog(), arch='armv6',
                                        install=install, workdir=str(folder))
        self.atomic(folder / 'setup-results.json', json.dumps(results).encode())
        return results

    def first_boot_seed(self):
        """Apply the zone once, before the account exists: the zone is erased as soon as its derived
        values are durable, even when part of it was refused."""
        pending = self.identity / 'seed-pending.json'
        stream, raw, size = self.seed_device()
        written = raw is not None and not astro_seed.is_blank(raw)
        try:
            value = None
            if pending.exists() or pending.is_symlink():
                value = json.loads(self.read_private(pending))
                if not isinstance(value, dict) or set(value) != SEED_KEYS:
                    raise RuntimeError('Invalid first-boot transaction')
            elif written:
                first = not (self.state / 'config.json').exists() and not (self.identity / 'bridge-password.json').exists()
                try:
                    value = self.seed_identity(raw) if first else None
                    if value is not None:
                        self.atomic(pending, json.dumps(value).encode())
                except Exception:
                    # Nothing applied: the bridge keeps the default password and says so.
                    value = None
                    self.atomic(self.state / 'firstboot-notice.json', json.dumps({'not_applied': ['all']}).encode())
            if written:
                astro_seed.clear(stream)
                written = False
            if value is None:
                return None
            if value['bridge_hash']:
                self.atomic(self.identity / 'bridge-password.json',
                            json.dumps({'version': 1, 'hash': self.valid_hash(value['bridge_hash'])}).encode())
            if not (self.state / 'config.json').exists():
                self.atomic(self.state / 'config.json', json.dumps(value['config']).encode())
            if value['notice']:
                self.atomic(self.state / 'firstboot-notice.json', json.dumps({'not_applied': value['notice']}).encode())
            if value['drivers'] and stream is not None:
                try:
                    self.apply_drivers(stream, value['drivers'], size)
                except Exception:
                    pass  # Drivers never block the identity or the network.
            pending.unlink()
            self.sync_directory(self.identity)
            return value
        finally:
            if stream is not None:
                try:
                    if written and astro_seed.holds_password(raw):
                        astro_seed.clear(stream)  # Whatever failed, the password never stays on the card.
                finally:
                    stream.close()

    def lock_root(self):
        # The console administrator is "bridge" (sudo); root never keeps the
        # empty password of a diskless Alpine root file system.
        self.command(['/usr/sbin/chpasswd', '-e'], input='root:!\n')

    def boot(self):
        self.lock_root()
        try:
            with self.locked():
                try:
                    self.first_boot_seed()
                except Exception:
                    pass  # A refused zone leaves the default password; it never blocks the account.
                self.account()
                self.host_keys()
                self.ssh_access()
                self.restore_time()
                self.checkpoint_time()
        except BaseException:
            # No proved account state: SSH must not accept a password.
            try:
                self.close_ssh()
            except Exception:
                pass
            raise


def main():
    if os.getuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('boot', 'password', 'save-password', 'checkpoint', 'ssh'):
        raise SystemExit('Fixed root-only persistence action required')
    helper = Persistence()
    try:
        if sys.argv[1] == 'boot':
            helper.boot()
        elif sys.argv[1] == 'password':
            helper.change_password()
        elif sys.argv[1] == 'checkpoint':
            helper.checkpoint_time()
        elif sys.argv[1] == 'ssh':
            helper.ssh_access()  # Persistent-root images: the same rule, without the data volume.
        else:
            with helper.locked():
                helper.save_password()
    except Exception:
        # OS command errors may contain password/hash/key material. Never log them.
        raise SystemExit('Astro Bridge persistence task failed; check private storage and console access') from None


if __name__ == '__main__':
    main()
