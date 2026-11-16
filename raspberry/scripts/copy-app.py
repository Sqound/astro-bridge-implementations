#!/usr/bin/env python3
"""Copy application code/subpackages and static assets, never runtime caches."""
import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def copy_app(source, target):
    from wifi_bridge.privacy import public_artifact
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_dir() or source == target or source in target.parents:
        raise ValueError('Invalid application copy paths')
    tables = source / 'cameras' / 'tables'
    if tables.is_symlink():
        raise ValueError('Application source must not contain symlinks')
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o755)
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if '__pycache__' in relative.parts or any(part.startswith('.') for part in relative.parts): continue
        camera_table = relative.parts[:2] == ('cameras', 'tables') and len(relative.parts) == 3 and path.suffix == '.json'
        if not (path.suffix == '.py' or relative.parts[0] == 'static' or camera_table): continue
        if path.is_symlink(): raise ValueError('Application source must not contain symlinks')
        if not path.is_file(): continue
        public_artifact(str(relative), path.read_bytes())
        if camera_table:
            # Admit only a validated descriptor, never arbitrary runtime JSON.
            import json
            from wifi_bridge.cameras.registry import MAX_MANIFEST_BYTES, _unique_object, validate_manifest
            with path.open('rb') as stream:
                data = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(data) > MAX_MANIFEST_BYTES or validate_manifest(json.loads(data, object_pairs_hook=_unique_object))['id'] + '.json' != path.name:
                raise ValueError('Invalid bundled camera table')
        destination = target / relative
        if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents if parent != target):
            raise ValueError('Application destination must not contain symlinks')
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent = destination.parent
        while parent != target:
            parent.chmod(0o755)
            parent = parent.parent
        shutil.copyfile(path, destination)
        destination.chmod(0o644)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    copy_app(args.source, args.target)


if __name__ == '__main__': main()
