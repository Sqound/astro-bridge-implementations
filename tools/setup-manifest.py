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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('entries', nargs='+', type=entry, help='TARGET=FILE, e.g. pi-zero-w=dist/astro-bridge-1.1.0-alpine-armhf-standard.img.xz')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len({e['target'] for e in args.entries}) != len(args.entries):
        parser.error('Each target may appear once')
    args.output.write_text(json.dumps({'format': 1, 'entries': args.entries}, indent=2) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
