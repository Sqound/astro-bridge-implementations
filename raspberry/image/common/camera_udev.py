#!/usr/bin/env python3
"""udev links of every camera manufacturer in the driver catalog, for both image recipes.

An installed driver writes its root-only rule (exact models only) on the data
partition: /var/lib/wifi-bridge/camera-drivers/<folder>/70-astro-bridge-<folder>.rules
(wifi_bridge/cameras/drivers.py). The read-only image carries one link per
manufacturer to that place. A link whose rule is absent does nothing, so a
manufacturer stays without any device access until its driver is installed.

Usage: camera_udev.py CATALOG OVERLAY_ROOT
"""
import json
from pathlib import Path
import re
import sys

DRIVERS = '/var/lib/wifi-bridge/camera-drivers'


def folder_name(vendor):
    # Same rule as wifi_bridge/cameras/drivers.py: ZWO keeps its historical folder.
    return 'zwo' if vendor == 'zwo_asi' else vendor.replace('_', '-')


def links(catalog):
    """{link name: target} for every manufacturer of the catalog."""
    result = {}
    for entry in catalog['drivers']:
        if not isinstance(entry.get('id'), str) or not re.fullmatch('[a-z][a-z0-9_]{0,31}', entry['id']):
            raise ValueError('invalid driver id')
        folder = folder_name(entry['id'])
        name = '70-astro-bridge-' + folder + '.rules'
        result[name] = DRIVERS + '/' + folder + '/' + name
    return result


def install(catalog_path, overlay):
    rules = Path(overlay) / 'etc/udev/rules.d'
    rules.mkdir(parents=True, exist_ok=True)
    created = []
    for name, target in sorted(links(json.loads(Path(catalog_path).read_text())).items()):
        link = rules / name
        if link.is_symlink() or link.exists():
            if link.is_symlink() and str(link.readlink()) == target:
                continue
            raise ValueError('existing udev rule: ' + name)
        link.symlink_to(target)
        created.append(name)
    return created


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    install(sys.argv[1], sys.argv[2])
