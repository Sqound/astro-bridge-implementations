#!/usr/bin/python3
"""Fixed diskless identity/time tasks; independent of the OTA application tree."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

STATE = '/var/lib/wifi-bridge'
KEYS = ('ssh_host_ed25519_key', 'ssh_host_rsa_key', 'ssh_host_ecdsa_key')
MIN_TIME, MAX_TIME = 1577836800, 4102444800  # 2020 through 2100; never proof of sync.


class Persistence:
    # Root injection is for offline tests only. CLI accepts fixed action names,
    # never a root/path/account supplied by a caller or environment variable.
    def __init__(self, root=Path('/'), run=subprocess.run, now=time.time,
                 set_time=lambda value: time.clock_settime(time.CLOCK_REALTIME, value)):
        self.root, self.run, self.now, self.set_time = Path(root), run, now, set_time
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
                self.command(['/usr/sbin/chpasswd'], input='bridge:' + self.password(bootstrap['password']) + '\n')
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
                self.command(['/usr/sbin/chpasswd'], input='bridge:' + password + '\n')
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

    def boot(self):
        with self.locked():
            self.account()
            self.host_keys()
            self.restore_time()
            self.checkpoint_time()


def main():
    if os.getuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('boot', 'password', 'save-password', 'checkpoint'):
        raise SystemExit('Fixed root-only persistence action required')
    helper = Persistence()
    try:
        if sys.argv[1] == 'boot':
            helper.boot()
        elif sys.argv[1] == 'password':
            helper.change_password()
        elif sys.argv[1] == 'checkpoint':
            helper.checkpoint_time()
        else:
            with helper.locked():
                helper.save_password()
    except Exception:
        # OS command errors may contain password/hash/key material. Never log them.
        raise SystemExit('Astro Bridge persistence task failed; check private storage and console access') from None


if __name__ == '__main__':
    main()
