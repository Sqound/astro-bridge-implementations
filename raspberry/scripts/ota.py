"""GitHub release discovery and transactional application updates (standard library).

The updater is installed separately from the application so a broken application
cannot prevent boot recovery. OS/kernel upgrades require a new SD image.
"""
import argparse
import fcntl
import hashlib
import io
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time
import tempfile
from contextlib import contextmanager
import urllib.error
import urllib.request

REPOSITORY = 'Sqound/astro-bridge-implementations'
API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
STATE = Path('/var/lib/wifi-bridge/updates')
ALPINE = Path('/etc/alpine-release').exists()
APP = Path('/var/lib/wifi-bridge/app/wifi_bridge') if ALPINE else Path('/opt/wifi-bridge/wifi_bridge')
PLATFORM = 'linux-alpine-armhf' if ALPINE else 'linux-raspios-armhf'
UPDATER_VERSION = 2
ACTIVE_PHASES = ('prepared', 'applying', 'verifying')
READY_MODES = ('setup', 'online', 'waiting_telescope', 'waiting_service')
VERSION = re.compile(r'^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')


def version(value):
    match = VERSION.fullmatch(value or '')
    if not match:
        raise ValueError('Only stable major.minor.patch releases are supported.')
    return tuple(map(int, match.groups()))


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic(path, value):
    atomic_bytes(path, json.dumps(value).encode())


def source_identity(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        if name != 'wifi_bridge/build.py':
            digest.update(name.encode() + b'\0' + content + b'\0')
    return digest.hexdigest()


def tree_identity(path):
    return source_identity({'wifi_bridge/' + str(p.relative_to(path)): p.read_bytes()
                            for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts})


def asset_names(platform, value):
    if platform == 'linux-raspios-armhf':
        return 'ota-manifest.json', f'astro-bridge-{value}-ota.tar.gz'
    if platform == 'linux-alpine-armhf':
        return f'ota-manifest-{platform}.json', f'astro-bridge-{value}-{platform}-ota.tar.gz'
    raise ValueError('Unsupported update platform.')


def read(path, default):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict): raise ValueError('Invalid updater record.')
        return value
    except FileNotFoundError:
        return default


def fetch(url, limit):
    if not url.startswith('https://'):
        raise ValueError('An update download requires HTTPS.')
    request = urllib.request.Request(url, headers={'User-Agent': 'Astro-Bridge-Updater', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        # GitHub release assets redirect to GitHub's HTTPS object storage.
        if not response.url.startswith('https://'):
            raise ValueError('An update download requires HTTPS.')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Update download exceeds its size limit.')
    return data


def release(current):
    data = json.loads(fetch(API, 2 * 1024 * 1024))
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('The latest release is not stable.')
    tag = data['tag_name']
    version(tag)
    assets = {item['name']: item['browser_download_url'] for item in data['assets']}
    manifest_name, expected = asset_names(PLATFORM, tag.lstrip('v'))
    for name in (manifest_name, expected):
        if assets.get(name) != f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}':
            raise ValueError('The release is missing a valid update asset: ' + name)
    return {'release_id': data['id'], 'version': tag.lstrip('v'), 'available': version(tag) > version(current),
            'manifest_url': assets[manifest_name], 'archive_url': assets[expected],
            'release_url': f'https://github.com/{REPOSITORY}/releases/tag/{tag}'}


class BudgetReader:
    def __init__(self, source):
        self.source, self.remaining = source, 32 * 1024 * 1024

    def read(self, size=-1):
        size = min(size if size >= 0 else self.remaining + 1, self.remaining + 1)
        data = self.source.read(size)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError('Decompressed update exceeds its size limit.')
        return data


@contextmanager
def budget_archive(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as decompressed:
        with tarfile.open(fileobj=BudgetReader(decompressed), mode='r|') as archive:
            yield archive


def unpack(data, manifest, destination, platform=None):
    platform = platform or PLATFORM
    if not isinstance(manifest, dict): raise ValueError('Invalid update manifest.')
    if (type(manifest.get('format')) is not int or manifest.get('format') not in (1, 2) or manifest.get('platform') != platform
            or type(manifest.get('minimum_updater', 1)) is not int
            or not 1 <= manifest.get('minimum_updater', 1) <= UPDATER_VERSION
            or (platform == 'linux-alpine-armhf' and (manifest.get('format') != 2 or manifest.get('minimum_updater') != 2))
            or manifest.get('scope', 'application-only') != 'application-only'):
        raise ValueError('This update targets an unsupported platform or format.')
    version(manifest.get('version'))
    if hashlib.sha256(data).hexdigest() != manifest.get('sha256'):
        raise ValueError('Update checksum mismatch.')
    with budget_archive(data) as archive:
        entries = []
        names = set()
        total = 0
        for entry in archive:
            path = PurePosixPath(entry.name)
            if (not entry.isfile() or path.is_absolute() or '..' in path.parts or
                    not path.parts or path.parts[0] != 'wifi_bridge' or
                    len(path.parts) < 2 or entry.name in names):
                raise ValueError('Unsafe or duplicate update archive entry.')
            if len(path.parts) > 3 or len(path.parts) > 2 and path.parts[1] != 'static':
                raise ValueError('Unexpected application directory.')
            if len(path.parts) == 2 and path.suffix != '.py':
                raise ValueError('Unexpected application file.')
            if len(path.parts) == 3 and (path.suffix not in {'.html', '.js', '.css', '.json', '.svg', '.png', '.ico', '.webmanifest'} or not re.fullmatch(r'[A-Za-z0-9_.-]+', path.name)):
                raise ValueError('Unexpected static asset.')
            if str(path) != entry.name:
                raise ValueError('Noncanonical archive path.')
            names.add(entry.name)
            entries.append(entry)
            total += entry.size
            if total > 30 * 1024 * 1024 or len(names) > 512:
                raise ValueError('Unpacked update exceeds its size limit.')
        required = {'wifi_bridge/__init__.py', 'wifi_bridge/__main__.py', 'wifi_bridge/static/index.html'}
        if not required <= names:
            raise ValueError('Incomplete application update.')
        if manifest.get('minimum_updater') == 2:
            required |= {'wifi_bridge/build.py', 'wifi_bridge/static/i18n.js', 'wifi_bridge/static/events.js'}
            if not required <= names:
                raise ValueError('Incomplete application update.')
    with budget_archive(data) as archive:
        for entry in archive:
            target = destination / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as source, target.open('wb') as out:
                shutil.copyfileobj(source, out)
            target.chmod(0o644)
    init = (destination / 'wifi_bridge/__init__.py').read_text()
    if not re.search(r'__version__\s*=\s*[\"\']' + re.escape(manifest['version']) + r'[\"\']', init):
        raise ValueError('The archive version differs from its manifest.')
    if manifest.get('build_id') is not None:
        build_id = manifest['build_id']
        if not isinstance(build_id, str) or not re.fullmatch(r'[0-9a-f]{64}', build_id) or tree_identity(destination / 'wifi_bridge') != build_id:
            raise ValueError('Application build identity mismatch.')
        if (destination / 'wifi_bridge/build.py').read_text() != f'BUILD_ID = "{build_id}"\n':
            raise ValueError('Invalid loaded build identity.')
    elif manifest.get('minimum_updater') == 2:
        raise ValueError('Missing build identity.')



def health():
    with urllib.request.urlopen('http://127.0.0.1/api/update-health', timeout=5) as response:
        return json.load(response)


def service(action):
    command = ['rc-service', 'wifi-bridge', action] if ALPINE else ['systemctl', action, 'wifi-bridge']
    subprocess.run(command, check=True, timeout=75)


class RecoveryRequired(ValueError):
    pass


def rollback():
    """Restore files only. Safe in an app pre-start hook; never controls services."""
    try:
        journal = read(STATE / 'transaction.json', {})
    except (OSError, ValueError):
        raise RecoveryRequired('Invalid transaction; console recovery required.') from None
    if (STATE / 'transaction.json').exists() and journal.get('phase') not in (*ACTIVE_PHASES, 'complete', 'rolled_back'):
        raise RecoveryRequired('Invalid transaction; console recovery required.')
    if journal.get('phase') not in ACTIVE_PHASES:
        return False
    backup = APP.parent / 'wifi_bridge.previous'
    if backup.exists():
        if APP.exists():
            shutil.rmtree(APP)
        backup.replace(APP); sync_directory(APP.parent)
    elif not APP.exists() or (journal.get('previous_build') and tree_identity(APP) != journal['previous_build']):
        raise RecoveryRequired('Previous application is unavailable; console recovery required.')
    if journal.get('snapshot_ready'):
        if journal.get('config_existed'):
            atomic_bytes(STATE.parent / 'config.json', (STATE / 'config-backup.json').read_bytes())
        else:
            (STATE.parent / 'config.json').unlink(missing_ok=True)
            sync_directory(STATE.parent)
    elif 'snapshot_ready' not in journal and (STATE / 'config-backup.json').exists():
        # Recover existing updater-v1 journals as well.
        atomic_bytes(STATE.parent / 'config.json', (STATE / 'config-backup.json').read_bytes())
    atomic(STATE / 'transaction.json', dict(journal, phase='rolled_back', finished=time.time()))
    atomic(STATE / 'status.json', {'state': 'rolled_back', 'message': 'Update failed; previous application files restored.'})
    return True


class UpdateRolledBack(ValueError):
    pass


def install(candidate, local=None):
    manifest, archive = local if local else (json.loads(fetch(candidate['manifest_url'], 32768)), None)
    if manifest.get('version') != candidate['version'] or version(candidate['version']) <= version(current_version()):
        raise ValueError('Updates require a strictly newer matching application version.')
    if archive is None:
        archive = fetch(candidate['archive_url'], 20 * 1024 * 1024)
    stage = APP.parent / 'wifi_bridge.stage'
    if stage.exists(): shutil.rmtree(stage)
    stage.mkdir(mode=0o700)
    unpack(archive, manifest, stage)
    subprocess.run(['/usr/bin/python3', '-m', 'compileall', '-q', str(stage)], check=True, timeout=90)
    if shutil.disk_usage(APP.parent).free < 100 * 1024 * 1024:
        raise ValueError('At least 100 MB of free storage is required.')
    # Flush staged contents before committing the journal/directory switch.
    for path in stage.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream: os.fsync(stream.fileno())
    for path in sorted((p for p in stage.rglob('*') if p.is_dir()), reverse=True): sync_directory(path)
    sync_directory(stage)
    backup = APP.parent / 'wifi_bridge.previous'
    if backup.exists(): shutil.rmtree(backup)
    (STATE / 'config-backup.json').unlink(missing_ok=True)
    journal = {'phase': 'prepared', 'version': candidate['version'], 'build_id': manifest.get('build_id'),
               'previous_build': tree_identity(APP), 'started': time.time(), 'snapshot_ready': False}
    atomic(STATE / 'transaction.json', journal)
    # Mutations are now blocked by the prepared journal. Recheck the app-owned
    # gate after downloading/staging, so an intervening connection is not stopped.
    try: readiness = health()
    except (OSError, ValueError): readiness = {}
    if not idle_health(readiness):
        rollback()
        atomic(STATE / 'status.json', dict(candidate, state='deferred', message='Installation deferred by the application maintenance gate.'))
        return
    try:
        journal['phase'] = 'applying'; atomic(STATE / 'transaction.json', journal)
        service('stop')
        config = STATE.parent / 'config.json'
        if config.exists(): atomic_bytes(STATE / 'config-backup.json', config.read_bytes())
        journal.update(snapshot_ready=True, config_existed=config.exists())
        atomic(STATE / 'transaction.json', journal)
        APP.replace(backup); sync_directory(APP.parent)
        (stage / 'wifi_bridge').replace(APP); sync_directory(APP.parent)
        journal['phase'] = 'verifying'; atomic(STATE / 'transaction.json', journal)
        service('start')
        deadline, successes = time.monotonic() + 180, 0
        while time.monotonic() < deadline:
            try:
                data = health()
                good = (data.get('version') == candidate['version'] and data.get('mode') in READY_MODES
                        and data.get('management_ready') is True
                        and (not manifest.get('build_id') or data.get('build_id') == manifest['build_id']))
                successes = successes + 1 if good else 0
                if successes >= 3:
                    atomic(STATE / 'transaction.json', dict(journal, phase='complete'))
                    atomic(STATE / 'status.json', {'state': 'installed', 'version': candidate['version'],
                           'build_id': manifest.get('build_id'), 'checked_at': time.time()})
                    return
            except (OSError, ValueError): successes = 0
            time.sleep(5)
        raise ValueError('Post-update service/network verification failed.')
    except Exception:
        service_ready = True
        try: service('stop')
        except Exception: service_ready = False
        rollback()  # A stop failure must never bypass recovery.
        try: service('start')
        except Exception: service_ready = False
        status = read(STATE / 'status.json', {})
        atomic(STATE / 'status.json', dict(status, rollback_service_ready=service_ready))
        raise UpdateRolledBack('Update failed; previous files restored.') from None


def current_version():
    text = (APP / '__init__.py').read_text()
    return re.search(r'__version__\s*=\s*[\"\']([^\"\']+)', text).group(1)


def prepare_state():
    if ALPINE and not os.path.ismount(STATE.parent):
        raise ValueError('Alpine updates require the persistent data mount.')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Update state must be root-private.')


@contextmanager
def worker_lock():
    prepare_state()
    descriptor = os.open(STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally: os.close(descriptor)


def idle_health(data):
    return (isinstance(data, dict) and data.get('management_ready') is True
            and data.get('telescopes_connected') is False
            and data.get('update_safe') is True
            and data.get('mode') in READY_MODES)


def local_file(path, limit):
    info = path.parent.lstat()
    if path.parent.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Local import directory must be root-private.')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError('Local import file must be private and bounded.')
        return stream.read(limit + 1)


def queue_local(expected):
    if not expected or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('A trusted owner-supplied SHA256 is required.')
    prepare_state()
    descriptor = os.open(STATE / 'import-local.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        updater = '/usr/libexec/astro-bridge-ota' if ALPINE else '/usr/local/lib/astro-bridge/ota.py'
        subprocess.Popen(['/usr/bin/python3', updater, 'import-local', '--sha256', expected],
                         stdin=subprocess.DEVNULL, stdout=descriptor, stderr=descriptor, start_new_session=True)
    finally: os.close(descriptor)


def local_candidate(expected):
    if not expected or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('A trusted owner-supplied SHA256 is required.')
    folder = STATE / 'import'
    manifest = json.loads(local_file(folder / 'manifest.json', 32768))
    archive = local_file(folder / 'archive.tar.gz', 20 * 1024 * 1024)
    if len(archive) > 20 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != expected or manifest.get('sha256') != expected:
        raise ValueError('Owner-approved local archive checksum mismatch.')
    return {'version': manifest['version'], 'source': 'owner-local'}, (manifest, archive)


def require_root():
    if os.geteuid() != 0:
        raise SystemExit('The OS updater requires root.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check', 'install', 'recover', 'import-local', 'queue-local'])
    parser.add_argument('--sha256')
    args = parser.parse_args()
    require_root()
    if args.command == 'queue-local':
        queue_local(args.sha256)
        return
    with worker_lock() as acquired:
        if not acquired:
            # The live install worker holds this through service start/health.
            # Its pre-start recovery must not deadlock or undo that transaction.
            return
        try:
            rollback()
            if args.command == 'recover': return
            if args.command == 'import-local':
                candidate, local = local_candidate(args.sha256)
                atomic(STATE / 'status.json', dict(candidate, state='installing'))
                install(candidate, local)
                return
            candidate = release(current_version())
            atomic(STATE / 'status.json', dict(candidate, state='available' if candidate['available'] else 'current', checked_at=time.time()))
            automatic = read(STATE / 'preferences.json', {}).get('automatic') is True
            if args.command == 'install':
                if read(STATE / 'request.json', {}).get('release_id') != candidate['release_id']:
                    raise ValueError('The release changed. Check for updates again.')
            if candidate['available'] and (args.command == 'install' or automatic):
                try: readiness = health()
                except (OSError, ValueError): readiness = {}
                if not idle_health(readiness):
                    atomic(STATE / 'status.json', dict(candidate, state='deferred', message='Installation deferred by the application maintenance gate.'))
                    return
                atomic(STATE / 'status.json', dict(candidate, state='installing'))
                install(candidate)
        except UpdateRolledBack:
            raise SystemExit(1) from None
        except RecoveryRequired:
            atomic(STATE / 'status.json', {'state': 'recovery_required', 'message': 'Interrupted update requires console recovery.'})
            raise SystemExit(1) from None
        except Exception:
            # Neither downloaded input nor OS exceptions enter the UI/log output.
            atomic(STATE / 'status.json', {'state': 'error', 'message': 'Update unavailable or verification failed; check repository access, platform and local prerequisites.', 'checked_at': time.time()})
            raise SystemExit(1) from None


if __name__ == '__main__':
    main()
