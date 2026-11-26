#!/usr/bin/env python3
"""Write astro-bridge-setup-manifest.json (format 1) for the files of one release."""
import argparse
import hashlib
import json
from pathlib import Path
import re

TARGETS = {'pi-zero-w', 'pi-3', 'pi-4', 'pi-5', 'live-usb-x86_64', 'live-usb-aarch64', 'docker'}


def entry(value):
    target, _, name = value.partition('=')
    path = Path(name)
    if target not in TARGETS or not path.is_file():
        raise argparse.ArgumentTypeError('Use TARGET=FILE with a known target and an existing file')
    if target == 'docker' and not path.name.endswith('.sh'):
        raise argparse.ArgumentTypeError('Docker entries must be the .sh installer')
    if target != 'docker' and not re.search(r'\.img(?:\.xz)?$', path.name):
        raise argparse.ArgumentTypeError('Image entries must end in .img or .img.xz')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return {'target': target, 'asset': path.name, 'sha256': digest.hexdigest(), 'size': path.stat().st_size}


# What each published image reads at first boot (image/common/astro_seed.py) and its
# camera support. Format 2 only; format 1 stays published for older Setup releases.
CAPABILITIES = {
    'pi-5': ({'zone': 2, 'wifi_seed': True, 'password_seed': True, 'driver_drop': True},
             {'service': True, 'arch': 'arm64'}),
    'pi-zero-w': ({'zone': 2, 'wifi_seed': True, 'password_seed': True, 'driver_drop': True}, {'service': False}),
}
NO_ZONE = ({'zone': 0, 'wifi_seed': False, 'password_seed': False, 'driver_drop': False}, {'service': False})


def manifest_2(entries, catalog):
    value = {'format': 2, 'entries': []}
    for item in entries:
        first_boot, cameras = CAPABILITIES.get(item['target'], NO_ZONE)
        if catalog is None:
            first_boot = dict(first_boot, driver_drop=False)  # No catalog published: no driver line.
        value['entries'].append(dict(item, first_boot=first_boot, cameras=cameras))
    if catalog is not None:
        data = catalog.read_bytes()
        json.loads(data)
        value['drivers_catalog'] = {'asset': catalog.name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('entries', nargs='+', type=entry, help='TARGET=FILE, e.g. pi-zero-w=dist/astro-bridge-1.2.0-alpine-armhf-standard.img.xz')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--output-2', type=Path, help='Also write astro-bridge-setup-manifest-2.json (format 2)')
    parser.add_argument('--drivers-catalog', type=Path, help='drivers-catalog.json published with the release')
    args = parser.parse_args()
    if len({e['target'] for e in args.entries}) != len(args.entries):
        parser.error('Each target may appear once')
    if args.drivers_catalog and args.drivers_catalog.name != 'drivers-catalog.json':
        parser.error('The catalog asset is drivers-catalog.json')
    args.output.write_text(json.dumps({'format': 1, 'entries': args.entries}, indent=2) + '\n')
    print(args.output)
    if args.output_2:
        args.output_2.write_text(json.dumps(manifest_2(args.entries, args.drivers_catalog), indent=2) + '\n')
        print(args.output_2)


if __name__ == '__main__':
    main()
