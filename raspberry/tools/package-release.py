"""Reproducible platform-specific app-only artifacts; no publication or OS update."""
import argparse
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
from wifi_bridge.ota import NESTED_FILES, allowed_file, asset_names, source_identity
from wifi_bridge.privacy import public_artifact, private_path


def package(root=ROOT, destination=None):
    destination = destination or root / 'dist'
    destination.mkdir(exist_ok=True)
    value = re.search(r'__version__ = "([^"]+)"', (root / 'wifi_bridge/__init__.py').read_text()).group(1)
    files = {}
    for path in sorted((root / 'wifi_bridge').rglob('*')):
        relative = path.relative_to(root / 'wifi_bridge')
        if private_path(str(relative)): raise ValueError('Private key files are excluded from OTA.')
        if path.is_symlink(): raise ValueError('Symlink in application source.')
        if '__pycache__' in relative.parts or path.is_dir(): continue
        if not path.is_file() or not allowed_file(relative.as_posix(), True):
            raise ValueError('Unexpected file in application source.')
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Oversized application source file.')
        data = path.read_bytes()
        public_artifact(str(relative), data)
        files['wifi_bridge/' + relative.as_posix()] = data
    if (root/'plugins').is_dir():
        from wifi_bridge.plugins import SCHEMA_VERSION, validate_registry
        source=validate_registry([json.loads(path.read_text()) for path in sorted((root/'plugins').glob('*.json'))])
        bundled=json.loads(files['wifi_bridge/static/plugins.json'])
        if bundled.get('schema_version')!=SCHEMA_VERSION or validate_registry(bundled['plugins'])!=source:raise ValueError('Plugin catalog is stale; run tools/build-plugins.py')
    if not all('wifi_bridge/' + name in files for name in NESTED_FILES):
        raise ValueError('Missing nested application files.')
    from wifi_bridge.cameras.registry import load_registry
    expected_tables = {'wifi_bridge/cameras/tables/' + table['id'] + '.json':
                       (json.dumps(table, indent=2) + '\n').encode()
                       for table in load_registry(root / 'cameras').descriptors()}
    bundled_tables = {name: content for name, content in files.items()
                      if name.startswith('wifi_bridge/cameras/tables/')}
    if bundled_tables != expected_tables:
        raise ValueError('Camera tables are stale; run tools/build-cameras.py')
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
        manifest = {'format': 3, 'platform': platform, 'version': value,
                    'asset': archive_name, 'sha256': hashlib.sha256(payload).hexdigest(),
                    'build_id': identity, 'scope': 'application-only', 'minimum_updater': 3}
        (destination / manifest_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        results.append(archive_name)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path)
    print('\n'.join(package(destination=parser.parse_args().output_dir)))
