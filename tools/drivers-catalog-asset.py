#!/usr/bin/env python3
"""Write drivers-catalog.json, the release asset Astro Bridge Setup reads, from the Core wheel.

The bridge installs camera drivers against the catalog shipped inside its own
application (wifi_bridge/static/drivers-catalog.json). Setup must offer exactly
that catalog, so the release asset is taken from the same Core wheel the images
are built from, byte for byte, never from another copy. The setup manifest
(setup-manifest.py --drivers-catalog) then records its size and SHA-256.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

MEMBER = 'wifi_bridge/static/drivers-catalog.json'
MAX_BYTES = 65536


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate field ' + key)
        value[key] = item
    return value


def check(data):
    """The checks Setup applies before trusting the file (tools/astro-bridge-setup/catalog/src/drivers.rs)."""
    if len(data) > MAX_BYTES:
        raise ValueError('catalog larger than 64 KiB')
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != {'schema_version', 'catalog_version', 'drivers'}:
        raise ValueError('unexpected catalog fields')
    if value['schema_version'] != 1 or not re.fullmatch(r'[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{1,3}', str(value['catalog_version'])):
        raise ValueError('unsupported catalog version')
    if not isinstance(value['drivers'], list) or not value['drivers']:
        raise ValueError('empty catalog')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--wheel', type=Path, required=True, help='Core wheel the images are built from')
    parser.add_argument('--output', type=Path, required=True, help='Path ending in drivers-catalog.json')
    args = parser.parse_args()
    if args.output.name != 'drivers-catalog.json':
        parser.error('The catalog asset is drivers-catalog.json')
    with zipfile.ZipFile(args.wheel) as wheel:
        info = wheel.getinfo(MEMBER)
        if info.file_size > MAX_BYTES:
            raise SystemExit('Catalog larger than 64 KiB in the wheel.')
        data = wheel.read(info)
    value = check(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(json.dumps({'asset': args.output.name, 'catalog_version': value['catalog_version'],
                      'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}))


if __name__ == '__main__':
    sys.exit(main())
