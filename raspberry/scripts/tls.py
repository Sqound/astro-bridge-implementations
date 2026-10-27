#!/usr/bin/python3
"""Fixed OS certificate lifecycle. Never shipped or replaced by app-only OTA."""
import contextlib
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import ipaddress
import math
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time

STATE = '/var/lib/wifi-bridge'
MIN_TIME, MAX_TIME = 1577836800, 4102444800  # Plausibility only, not sync evidence.
AP = '192.168.77.1'


class CertificateError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Certificates:
    # Alternate roots/runners exist only for offline tests. The privileged CLI
    # never accepts path, subject, executable, account or environment overrides.
    def __init__(self, root=Path('/'), run=subprocess.run, now=time.time):
        self.root, self.run, self.now = Path(root), run, now
        self.state = self.root / STATE.lstrip('/')
        self.tls = self.state / 'tls'

    @staticmethod
    def directory(path):
        if path.is_symlink():
            raise ValueError('Unsafe private directory')
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe private directory')

    @staticmethod
    def read(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 32768:
                raise ValueError('Unsafe private file')
            return stream.read(32769)

    @staticmethod
    def sync(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)

    @classmethod
    def atomic(cls, path, value):
        fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(value); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, path); cls.sync(path.parent)
        finally: Path(name).unlink(missing_ok=True)

    def mounted(self):
        label = self.root / 'dev/disk/by-label/ASTRO_DATA'
        if not label.exists(): raise ValueError('Persistent volume unavailable')
        found = False
        for line in (self.root / 'proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            try:
                split = fields.index('-')
                if split < 6 or len(fields) <= split + 3: raise ValueError
            except ValueError: raise ValueError('Invalid mount table') from None
            if fields[4] == STATE:
                source = self.root / fields[split + 2].lstrip('/')
                found = (fields[split + 1] == 'ext4' and 'rw' in fields[5].split(',')
                         and 'rw' in fields[split + 3].split(',') and source.resolve() == label.resolve())
        if not found: raise ValueError('Persistent volume unavailable')
        self.directory(self.state)
        self.directory(self.tls)
        self.directory(self.tls / 'generations')

    def command(self, args):
        # OpenSSL diagnostics can contain sensitive file details. CLI never emits them.
        return self.run(args, check=True, capture_output=True, text=True, timeout=20,
                        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}).stdout.strip()

    @contextlib.contextmanager
    def lock(self, path):
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('Unsafe maintenance lock')
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise CertificateError('HTTPS_BUSY') from None
            yield
        finally: os.close(fd)

    def ota_idle(self):
        path = self.state / 'updates/transaction.json'
        try: value = json.loads(self.read(path))
        except FileNotFoundError: return
        if not isinstance(value, dict) or value.get('phase') not in ('complete', 'rolled_back'):
            raise CertificateError('UPDATE_IN_PROGRESS')

    @contextlib.contextmanager
    def maintenance(self):
        self.mounted()
        self.directory(self.state / 'updates')
        # Same lock as the fixed OTA helper; no race between preparing a journal
        # and committing a certificate. A second TLS operation also fails closed.
        with self.lock(self.state / 'configuration.lock'), self.lock(self.state / 'updates/lock'), self.lock(self.tls / 'operation.lock'):
            self.ota_idle()
            yield

    def hostname(self):
        value = json.loads(self.read(self.state / 'config.json'))
        name = value.get('hostname') if isinstance(value, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
            raise ValueError('Valid bridge hostname required')
        return name + '.local'

    def clock_evidence(self, request):
        now = self.now()
        if not MIN_TIME <= now <= MAX_TIME: raise CertificateError('HTTPS_CLOCK_UNVERIFIED')
        if request:
            # Consumed only by this invocation. Never stored as reusable proof of
            # sync and never changes the clock. Owner must verify UTC independently.
            if (set(request) != {'owner_confirmed_utc'} or type(request['owner_confirmed_utc']) is not int
                    or abs(now - request['owner_confirmed_utc']) > 120):
                raise CertificateError('HTTPS_CLOCK_UNVERIFIED')
            return 'owner-confirmed-current-utc'
        try:
            line = self.command(['/usr/bin/chronyc', '-c', '-n', 'tracking'])
            fields = next(csv.reader([line]))
            numbers = [float(value) for value in fields[3:13]]
            # Local-mode pseudo-reference is not external synchronisation.
            if (len(fields) != 14 or not re.fullmatch('[0-9A-Fa-f]{8}', fields[0])
                    or fields[0].upper() in ('00000000', '7F7F0101')
                    or not 1 <= int(fields[2]) <= 15 or fields[13] != 'Normal'
                    or not all(math.isfinite(value) for value in numbers)
                    or not 0 <= now - numbers[0] <= 3600
                    or abs(numbers[1]) > 1 or not 0 <= numbers[8] <= 1
                    or not 0 <= numbers[7] <= 2):
                raise ValueError
            rows = list(csv.reader(self.command(['/usr/bin/chronyc', '-c', '-n', 'sources']).splitlines()))
            selected = [row for row in rows if len(row) == 10 and row[0] in ('^', '=') and row[1] == '*']
            if len(selected) != 1: raise ValueError
            if not 1 <= int(selected[0][3]) <= 15 or not 1 <= int(selected[0][5], 8) <= 0o377: raise ValueError
            source = ipaddress.ip_address(selected[0][2])
            if ipaddress.ip_address(fields[1]) != source: raise ValueError
            if source.is_loopback or source.is_unspecified or not 0 <= float(selected[0][6]) <= 3600:
                raise ValueError
            return 'chrony-synchronized'
        except (OSError, subprocess.SubprocessError, ValueError, IndexError, StopIteration):
            raise CertificateError('HTTPS_CLOCK_UNVERIFIED') from None

    def record(self):
        try: record = json.loads(self.read(self.tls / 'current.json'))
        except FileNotFoundError: return {'version': 1, 'configured': False, 'enabled': False}
        if (not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != 1
                or type(record.get('configured')) is not bool or type(record.get('enabled')) is not bool
                or record['enabled'] and not record['configured']):
            raise ValueError('Invalid TLS state')
        if record['configured'] and (not isinstance(record.get('generation'), str)
                                    or not re.fullmatch('[0-9a-f]{32}', record['generation'])):
            raise ValueError('Invalid TLS generation')
        return record

    def generation(self, record):
        folder = self.tls / 'generations' / record['generation']
        self.directory(folder)
        for name in ('ca.key', 'ca.pem', 'server.key', 'server.pem', 'metadata.json'):
            self.read(folder / name)
        return folder

    def validate(self, folder, hostname):
        self.command(['/usr/bin/openssl', 'verify', '-CAfile', str(folder / 'ca.pem'),
                      '-purpose', 'sslserver', '-verify_hostname', hostname, str(folder / 'server.pem')])
        self.command(['/usr/bin/openssl', 'verify', '-CAfile', str(folder / 'ca.pem'),
                      '-purpose', 'sslserver', '-verify_ip', AP, str(folder / 'server.pem')])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(folder / 'server.pem', folder / 'server.key')
        # CA key pairing is checked before a renewal can adopt it.
        ca_public = self.command(['/usr/bin/openssl', 'pkey', '-in', str(folder / 'ca.key'), '-pubout'])
        cert_public = self.command(['/usr/bin/openssl', 'x509', '-in', str(folder / 'ca.pem'), '-pubkey', '-noout'])
        if ca_public != cert_public: raise ValueError('CA identity mismatch')

    def status(self):
        self.mounted()
        record = self.record()
        if not record['configured']: return record
        folder = self.generation(record)
        metadata = json.loads(self.read(folder / 'metadata.json'))
        if not isinstance(metadata, dict) or metadata.get('hostname') != self.hostname():
            raise ValueError('TLS hostname changed; disable/reset before setup')
        if (set(metadata) != {'hostname', 'expires', 'clock_evidence'}
                or not isinstance(metadata['expires'], str)
                or metadata['clock_evidence'] not in ('chrony-synchronized', 'owner-confirmed-current-utc')):
            raise ValueError('Invalid TLS metadata')
        self.validate(folder, metadata['hostname'])
        expiry = self.command(['/usr/bin/openssl', 'x509', '-in', str(folder / 'server.pem'), '-enddate', '-noout']).removeprefix('notAfter=')
        if metadata['expires'] != expiry: raise ValueError('Invalid TLS expiry')
        der = ssl.PEM_cert_to_DER_cert(self.read(folder / 'ca.pem').decode())
        return dict(record, hostname=metadata['hostname'], fingerprint_sha256=hashlib.sha256(der).hexdigest(),
                    expires=metadata['expires'], clock_evidence=metadata['clock_evidence'])

    def diagnose(self):
        """Public certificate facts, even when unusable. Never read private keys."""
        self.mounted()
        result = {'configured': False, 'enabled': False, 'state': 'unconfigured',
                  'hostname': None, 'fingerprint_sha256': None, 'ca_expires': None,
                  'leaf_expires': None, 'issuance_clock_evidence': None, 'current_clock': 'unverified'}
        try:
            pointer = self.tls / 'current.json'
            result['configured'] = pointer.exists() or pointer.is_symlink()
            record = self.record()
            result.update(configured=record['configured'], enabled=record['enabled'])
            if not record['configured']: return result
            result['state'] = 'invalid'
            folder = self.tls / 'generations' / record['generation']
            if not folder.is_dir(): return result
            self.directory(folder)
            ca = self.read(folder / 'ca.pem')
            def dates(name):
                lines = self.command(['/usr/bin/openssl', 'x509', '-in', str(folder / name), '-startdate', '-enddate', '-noout']).splitlines()
                if len(lines) != 2 or not lines[0].startswith('notBefore=') or not lines[1].startswith('notAfter='):
                    raise ValueError('Invalid certificate dates')
                return [ssl.cert_time_to_seconds(line.split('=', 1)[1]) for line in lines]
            ca_dates = dates('ca.pem')
            result['fingerprint_sha256'] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca.decode())).hexdigest()
            result['ca_expires'] = datetime.fromtimestamp(ca_dates[1], timezone.utc).isoformat()
            # CA identity remains available for old-trust removal if leaf/metadata fails.
            self.read(folder / 'server.pem')
            leaf_dates = dates('server.pem')
            result['leaf_expires'] = datetime.fromtimestamp(leaf_dates[1], timezone.utc).isoformat()
            metadata = json.loads(self.read(folder / 'metadata.json'))
            if not isinstance(metadata, dict): return result
            hostname = metadata.get('hostname')
            if not isinstance(hostname, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.local', hostname): return result
            result['hostname'] = hostname
            if metadata.get('clock_evidence') in ('chrony-synchronized', 'owner-confirmed-current-utc'):
                result['issuance_clock_evidence'] = metadata['clock_evidence']
            try: self.clock_evidence({}); result['current_clock'] = 'synchronized'
            except ValueError: pass
            if hostname != self.hostname(): result['state'] = 'hostname_mismatch'; return result
            now = self.now()
            if now < max(ca_dates[0], leaf_dates[0]): result['state'] = 'not_yet_valid'; return result
            if now >= ca_dates[1]: result['state'] = 'ca_expired'; return result
            if now >= leaf_dates[1]: result['state'] = 'leaf_expired'; return result
            for flag, target in (('-verify_hostname', hostname), ('-verify_ip', AP)):
                self.command(['/usr/bin/openssl', 'verify', '-CAfile', str(folder / 'ca.pem'),
                              '-purpose', 'sslserver', flag, target, str(folder / 'server.pem')])
            result['state'] = 'valid'  # Public chain only; listener validates key/readiness.
        except (OSError, ValueError, subprocess.SubprocessError):
            result['state'] = 'invalid'
        return result

    def prune(self, keep):
        # Fixed private subtree only, never a caller-provided directory.
        for path in (self.tls / 'generations').iterdir():
            if path.name not in keep and (re.fullmatch('[0-9a-f]{32}', path.name) or path.name.startswith('.prepare-')):
                if path.is_symlink(): path.unlink()
                elif path.is_dir(): shutil.rmtree(path)
        self.sync(self.tls / 'generations')

    def prepare(self, request, renew=False):
        with self.maintenance():
            evidence = self.clock_evidence(request)
            name = self.hostname()
            previous = self.record()
            if previous['configured'] != renew:
                raise ValueError('Existing CA requires explicit reset; renewal requires existing identity')
            self.prune({previous.get('generation')})
            temporary = Path(tempfile.mkdtemp(prefix='.prepare-', dir=self.tls / 'generations'))
            try:
                if renew:
                    old = self.generation(previous)
                    metadata = json.loads(self.read(old / 'metadata.json'))
                    if not isinstance(metadata, dict) or metadata.get('hostname') != name: raise ValueError('Hostname change requires reset and renewed trust')
                    for filename in ('ca.key', 'ca.pem'):
                        self.atomic(temporary / filename, self.read(old / filename))
                    # Expired CA cannot be silently replaced during renewal.
                    self.command(['/usr/bin/openssl', 'x509', '-in', str(temporary / 'ca.pem'), '-checkend', '7776000', '-noout'])
                else:
                    self.command(['/usr/bin/openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', str(temporary / 'ca.key')])
                    config = ('[req]\ndistinguished_name=dn\nx509_extensions=ca\nprompt=no\n[dn]\nCN=Astro Bridge device CA\n[ca]\n'
                              'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n'
                              'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n'
                              f'nameConstraints=critical,permitted;DNS:{name},permitted;IP:{AP}/255.255.255.255\n')
                    self.atomic(temporary / 'ca.conf', config.encode())
                    self.command(['/usr/bin/openssl', 'req', '-new', '-x509', '-sha256', '-days', '730',
                                  '-key', str(temporary / 'ca.key'), '-config', str(temporary / 'ca.conf'), '-out', str(temporary / 'ca.pem')])
                self.command(['/usr/bin/openssl', 'genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', str(temporary / 'server.key')])
                self.command(['/usr/bin/openssl', 'req', '-new', '-sha256', '-key', str(temporary / 'server.key'),
                              '-subj', '/CN=Astro Bridge', '-out', str(temporary / 'server.csr')])
                extensions = ('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n'
                              'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' + f'subjectAltName=DNS:{name},IP:{AP}\n')
                self.atomic(temporary / 'leaf.conf', extensions.encode())
                self.command(['/usr/bin/openssl', 'x509', '-req', '-sha256', '-days', '90', '-in', str(temporary / 'server.csr'),
                              '-CA', str(temporary / 'ca.pem'), '-CAkey', str(temporary / 'ca.key'), '-set_serial', '0x' + secrets.token_hex(16),
                              '-extfile', str(temporary / 'leaf.conf'), '-out', str(temporary / 'server.pem')])
                for path in temporary.iterdir():
                    path.chmod(0o600)
                    with path.open('rb') as stream: os.fsync(stream.fileno())
                self.validate(temporary, name)
                expires = self.command(['/usr/bin/openssl', 'x509', '-in', str(temporary / 'server.pem'), '-enddate', '-noout']).removeprefix('notAfter=')
                self.atomic(temporary / 'metadata.json', json.dumps({'hostname': name, 'expires': expires, 'clock_evidence': evidence}).encode())
                self.sync(temporary)
                generation = secrets.token_hex(16)
                final = temporary.parent / generation
                os.rename(temporary, final); self.sync(final.parent)
                record = {'version': 1, 'configured': True, 'enabled': True, 'generation': generation}
                # A root console may edit config outside the supported save lock.
                if self.hostname() != name: raise ValueError('Hostname changed during certificate preparation')
                der = ssl.PEM_cert_to_DER_cert(self.read(final / 'ca.pem').decode())
                result = dict(record, hostname=name, fingerprint_sha256=hashlib.sha256(der).hexdigest(),
                              expires=expires, clock_evidence=evidence)
                # Publish only after the complete identity has been validated/fsynced.
                try:
                    self.atomic(self.tls / 'current.json', json.dumps(record).encode())
                except Exception:
                    if previous['configured']:
                        self.atomic(self.tls / 'current.json', json.dumps(previous).encode())
                    else:
                        (self.tls / 'current.json').unlink(missing_ok=True); self.sync(self.tls)
                    raise
                return result
            finally:
                if temporary.exists(): shutil.rmtree(temporary)

    def enable(self):
        with self.maintenance():
            result = self.status()  # Chain, current dates, private files and exact hostname.
            if not result['configured']: raise ValueError('Certificate setup required')
            previous = self.record()
            record = dict(previous, enabled=True)
            try:
                self.atomic(self.tls / 'current.json', json.dumps(record).encode())
            except Exception:
                self.atomic(self.tls / 'current.json', json.dumps(previous).encode())
                raise
            return dict(result, enabled=True)

    def deactivate(self, reset=False):
        with self.maintenance():
            # Reset is also the explicit recovery path for corrupt/expired TLS.
            record = {'version': 1, 'configured': False, 'enabled': False} if reset else self.record()
            record['enabled'] = False
            self.atomic(self.tls / 'current.json', json.dumps(record).encode())
            if reset: self.prune(set())
            return {'configured': record['configured'], 'enabled': False}


def read_request(stream, timeout=5):
    """Bound bytes AND pipe wait; a partial/unclosed stdin cannot pin a helper."""
    descriptor = stream.fileno()
    deadline, value = time.monotonic() + timeout, bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
            raise ValueError('Certificate request timed out')
        chunk = os.read(descriptor, 1025 - len(value))
        if not chunk: break
        value.extend(chunk)
        if len(value) > 1024: raise ValueError('Oversized request')
    request = json.loads(value)
    if not isinstance(request, dict): raise ValueError('Object required')
    return request


def main():
    os.umask(0o077)
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in ('setup', 'renew', 'enable', 'disable', 'reset', 'status', 'fingerprint', 'diagnose'):
        raise SystemExit('Fixed root-only certificate action required')
    try:
        helper = Certificates()
        action = sys.argv[1]
        if action in ('setup', 'renew'):
            # No paths/subjects supplied by callers. Empty JSON requires positive sync.
            request = read_request(sys.stdin.buffer)
            result = helper.prepare(request, renew=action == 'renew')
        elif action == 'enable': result = helper.enable()
        elif action in ('disable', 'reset'): result = helper.deactivate(action == 'reset')
        elif action in ('diagnose', 'fingerprint'): result = helper.diagnose()
        else: result = helper.status()
        if action == 'fingerprint': result = {'fingerprint_sha256': result.get('fingerprint_sha256'), 'configured': result['configured']}
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'error': {'code': error.code if isinstance(error, CertificateError) else 'HTTPS_OPERATION_FAILED'}}))
        raise SystemExit('Certificate operation failed; check private storage, current UTC and maintenance state') from None


if __name__ == '__main__': main()
