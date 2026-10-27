"""Reproducible platform-specific app-only artifacts; no publication or OS update."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wifi_bridge.ota import asset_names, source_identity


def package(root=ROOT, destination=None):
    destination = destination or root / 'dist'
    destination.mkdir(exist_ok=True)
    value = re.search(r'__version__ = "([^"]+)"', (root / 'wifi_bridge/__init__.py').read_text()).group(1)
    files = {}
    for path in sorted((root / 'wifi_bridge').rglob('*')):
        relative = path.relative_to(root / 'wifi_bridge')
        if path.is_symlink(): raise ValueError('Symlink in application source.')
        if path.is_file() and (len(relative.parts) == 1 and path.suffix == '.py' or
                               len(relative.parts) == 2 and relative.parts[0] == 'static'):
            files['wifi_bridge/' + str(relative)] = path.read_bytes()
    if (root/'plugins').is_dir():
        from wifi_bridge.plugins import SCHEMA_VERSION, validate_registry
        source=validate_registry([json.loads(path.read_text()) for path in sorted((root/'plugins').glob('*.json'))])
        bundled=json.loads(files['wifi_bridge/static/plugins.json'])
        if bundled.get('schema_version')!=SCHEMA_VERSION or validate_registry(bundled['plugins'])!=source:raise ValueError('Plugin catalog is stale; run tools/build-plugins.py')
    identity = source_identity(files)
    files['wifi_bridge/build.py'] = f'BUILD_ID = "{identity}"\n'.encode()
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', mtime=0, filename='') as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    payload = output.getvalue()
    results = []
    for platform in ('linux-raspios-armhf', 'linux-alpine-armhf'):
        manifest_name, archive_name = asset_names(platform, value)
        (destination / archive_name).write_bytes(payload)
        # Updater1 can read the legacy Pi OS payload: build.py is an allowed .py
        # module; extra manifest fields are ignored. Alpine requires updater2.
        minimum = 2 if platform == 'linux-alpine-armhf' else 1
        manifest = {'format': minimum, 'platform': platform, 'version': value,
                    'asset': archive_name, 'sha256': hashlib.sha256(payload).hexdigest(),
                    'build_id': identity, 'scope': 'application-only', 'minimum_updater': minimum}
        (destination / manifest_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        results.append(archive_name)
    return results


if __name__ == '__main__':
    print('\n'.join(package()))
