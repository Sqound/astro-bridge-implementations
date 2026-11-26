#!/usr/bin/env python3
"""Build the public Raspberry Pi 5 image from the pinned Core wheel and public inputs.

Every input is public and pinned by SHA-256 in public-inputs.json: the official
Alpine Raspberry Pi base image and signed Alpine packages from the official
CDN. The application comes from the Core wheel pinned by core.lock.json.

No camera manufacturer file is part of the image: no library, no licence text,
no access rule. Every manufacturer, ZWO included, is downloaded on request from
its own site (Astro Bridge Setup, or Install on the bridge's Cameras page) and
installed with its own root-only rule. This recipe downloads nothing from them.

No diagnostic SSH key or Wi-Fi profile is added. The output is a regular file;
no disk device is ever opened.

Needs Python 3.11+, xz, mtools and e2fsprogs (mke2fs with -d, debugfs, e2fsck).
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\.apk')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def fetch(url, target, expected):
    """Download once into the work cache; a cached file must still match."""
    if target.is_file() and digest(target) == expected:
        return target
    if not url.startswith('https://dl-cdn.alpinelinux.org/alpine/'):
        raise ValueError('Only the official Alpine CDN is an input: ' + url)
    partial = target.with_suffix(target.suffix + '.partial')
    request = urllib.request.Request(url, headers={'User-Agent': 'astro-bridge-image-builder'})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    if digest(partial) != expected:
        partial.unlink()
        raise ValueError('Pinned input changed upstream (refresh public-inputs.json after review): ' + url)
    partial.replace(target)
    return target


def application(wheel, lock_path, target):
    """Write the wheel's wifi_bridge tree as the deterministic app archive prepare.py expects."""
    lock = json.loads(lock_path.read_text())
    if wheel.name != lock['asset'] or digest(wheel) != lock['sha256']:
        raise ValueError('Core wheel differs from core.lock.json')
    with zipfile.ZipFile(wheel) as archive, tarfile.open(target, 'w', format=tarfile.PAX_FORMAT) as output:
        names = sorted(n for n in archive.namelist() if n.startswith('wifi_bridge/') and not n.endswith('/'))
        if 'wifi_bridge/build.py' not in names:
            raise ValueError('Core wheel has no build identity')
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe wheel member')
            data = archive.read(name)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime, info.uid, info.gid = len(data), 0o644, 0, 0, 0
            output.addfile(info, io.BytesIO(data))
        build = re.search(r'BUILD_ID = "([0-9a-f]{64})"', archive.read('wifi_bridge/build.py').decode())
    if not build:
        raise ValueError('Invalid Core build identity')
    return lock['version'], build[1]


def packages(lock, work):
    base = f"https://dl-cdn.alpinelinux.org/alpine/{lock['alpine']}"
    folders = {}
    for group, items in lock['packages'].items():
        folder = work / 'apks' / group
        folder.mkdir(parents=True, exist_ok=True)
        for item in items:
            if not NAME.fullmatch(item['name']) or item['repository'] not in ('main', 'community'):
                raise ValueError('Invalid package pin: ' + item['name'])
            fetch(f"{base}/{item['repository']}/{lock['architecture']}/{item['name']}", folder / item['name'], item['sha256'])
        folders[group] = folder
    manifest = ''.join(f"{item['sha256']}  {item['name']}\n" for item in lock['packages']['runtime'])
    (folders['runtime'] / 'astro-packages.sha256').write_text(manifest)
    return folders


def gphoto_packages(lock, work, fetcher=None):
    """The pinned gPhoto2 packages (gphoto-packages.lock.json): official Alpine CDN, SHA-256 checked.

    The lock records each package's digest, not its repository: main is tried,
    then community. A file is kept only when its digest matches the lock.
    """
    fetcher = fetcher or fetch
    pins = json.loads((HERE / 'gphoto-packages.lock.json').read_text())['selected']
    base = f"https://dl-cdn.alpinelinux.org/alpine/{lock['alpine']}"
    folder = work / 'apks' / 'gphoto'
    folder.mkdir(parents=True, exist_ok=True)
    for name, meta in pins.items():
        if not NAME.fullmatch(name) or not re.fullmatch('[0-9a-f]{64}', meta['sha256']):
            raise ValueError('Invalid package pin: ' + name)
        for repository in ('main', 'community'):
            try:
                fetcher(f"{base}/{repository}/{lock['architecture']}/{name}", folder / name, meta['sha256'])
                break
            except (OSError, ValueError):
                (folder / (name + '.partial')).unlink(missing_ok=True)
        else:
            raise ValueError('Pinned gPhoto2 package unavailable in main and community: ' + name)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--core-lock', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True, help='Tree holding image/alpine/openrc')
    parser.add_argument('--work', type=Path, required=True, help='New or cached work directory')
    parser.add_argument('--output', type=Path, required=True, help='New directory for the image and its manifest')
    parser.add_argument('--splash-binary', type=Path, help='Static ARM64 renderer from image/splash/build-renderer.py')
    parser.add_argument('--tools-prefix', type=Path, default=Path('/usr'), help='Prefix of mtools and e2fsprogs')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing an existing output directory')
    lock = json.loads((HERE / 'public-inputs.json').read_text())
    if lock.get('format') != 1:
        raise SystemExit('Unsupported inputs lock')
    args.work.mkdir(parents=True, exist_ok=True)
    version, build = application(args.wheel, args.core_lock, args.work / 'app.tar')
    splash = []
    if args.splash_binary:
        renderer = importlib.util.spec_from_file_location('renderer', HERE.parent / 'splash/build-renderer.py')
        module = importlib.util.module_from_spec(renderer)
        renderer.loader.exec_module(module)
        module.static_aarch64(args.splash_binary)
        splash = ['--splash-binary', str(args.splash_binary)]
    folders = packages(lock, args.work)
    gphoto = gphoto_packages(lock, args.work)
    runtime = folders['runtime'] / 'astro-packages.sha256'
    prepared = args.work / 'prepared'
    shutil.rmtree(prepared, ignore_errors=True)
    subprocess.run([sys.executable, str(HERE / 'prepare.py'), '--public',
                    '--app', str(args.work / 'app.tar'), '--app-sha256', digest(args.work / 'app.tar'), '--build-id', build,
                    '--apks', str(folders['runtime']), '--manifest-sha256', digest(runtime),
                    '--package-count', str(len(lock['packages']['runtime'])),
                    '--display-apks', str(folders['display']), '--camera-apks', str(folders['camera']),
                    '--gphoto-apks', str(gphoto),
                    '--output', str(prepared), '--project', str(args.project), *splash], check=True)
    base_gz = fetch(lock['base']['url'], args.work / Path(lock['base']['url']).name, lock['base']['sha256'])
    base = args.work / 'base.img'
    with gzip.open(base_gz) as source, base.open('wb') as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
    if digest(base) != lock['base']['image_sha256']:
        raise SystemExit('Unexpected decompressed base image')
    args.output.mkdir(parents=True)
    image = args.work / f'astro-bridge-{version}-alpine-aarch64-pi5.img'
    image.unlink(missing_ok=True)
    subprocess.run([sys.executable, str(HERE / 'assemble.py'), '--base', str(base), '--overlay', str(prepared / 'astro-bridge.apkovl.tar.gz'),
                    '--data', str(prepared / 'data'), '--output', str(image), '--mtools', str(args.tools_prefix),
                    '--e2fsprogs', str(args.tools_prefix), '--wifi-seed'], check=True)
    asset = args.output / (image.name + '.xz')
    with image.open('rb') as source, lzma.open(asset, 'wb', preset=9) as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
    record = {'asset': asset.name, 'sha256': digest(asset), 'size': asset.stat().st_size, 'image_sha256': digest(image),
              'app_build_id': build, 'splash_renderer_sha256': digest(args.splash_binary) if args.splash_binary else None, 'core_version': version, 'base': lock['base'],
              'packages': {group: len(items) for group, items in lock['packages'].items()},
              # What this image reads at first boot (image/common/astro_seed.py) and carries for cameras.
              'first_boot': {'zone': 2, 'wifi_seed': True, 'password_seed': True, 'driver_drop': True},
              'cameras': {'service': True, 'arch': 'arm64', 'gphoto2': 'installed, off'}}
    (args.output / 'pi5-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    (args.output / 'PI5-SHA256SUMS').write_text(f"{record['sha256']}  {asset.name}\n")
    image.unlink()
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
