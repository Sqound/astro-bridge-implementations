"""Prepare version-matched notices for the shared macOS PyInstaller spec."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import re
import runpy
import ssl
import sysconfig
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 12 * 1024 * 1024


def version(root=ROOT):
    match = re.search(r'^__version__ = "((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"$',
                      (root / 'wifi_bridge/__init__.py').read_text(), re.M)
    if not match:
        raise ValueError('Canonical application version missing.')
    return match[1]


def runtime_inputs():
    # The same discovery used by PyInstaller's tkinter hook; Tcl() does not load
    # Tk or open a GUI. Inspect scripts instead of starting the flash launcher.
    from PyInstaller.utils.hooks.tcl_tk import tcltk_info
    if not tcltk_info.available:
        raise ValueError('Actual Tcl/Tk runtime unavailable.')
    versions = {'python': platform.python_version()}
    for name, directory, script in [('tcl', tcltk_info.tcl_data_dir, 'init.tcl'),
                                    ('tk', tcltk_info.tk_data_dir, 'tk.tcl')]:
        path = Path(directory) / script
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError('Actual Tcl/Tk release script missing.')
        match = re.search(r'(?im)^package\s+require\s+-exact\s+' + name + r'\s+(\d+\.\d+\.\d+)\s*$', path.read_text())
        if not match:
            raise ValueError('Exact Tcl/Tk release cannot be identified.')
        versions[name] = match[1]
    candidates = [Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt',
                  Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.executable).parent / 'LICENSE.txt']
    license_file = next((p for p in candidates if p.is_file() and p.stat().st_size <= MAX_FILE), None)
    if license_file is None:
        raise ValueError('Actual Python runtime license missing.')
    return versions, license_file.read_bytes()


def upstream_terms(name, release):
    if name not in ('tcl', 'tk') or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', release):
        raise ValueError('Invalid Tcl/Tk release.')
    return f'https://raw.githubusercontent.com/tcltk/{name}/core-{release.replace(".", "-")}/license.terms'


def download_terms(url):
    import certifi
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('Unexpected notice-source redirect.')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(
        context=ssl.create_default_context(cafile=certifi.where())))
    with opener.open(url, timeout=15) as response:
        if response.status != 200 or response.url != url:
            raise ValueError('Unexpected notice-source response.')
        data = response.read(65537)
    if not 100 <= len(data) <= 65536 or b'The authors hereby grant permission' not in data:
        raise ValueError('Missing or invalid release notice.')
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(directory, *, runtime=None, distributions=None, fetch=download_terms):
    if directory.exists() or directory.is_symlink():
        raise ValueError('Use a fresh notice directory.')
    versions, python_license = runtime if runtime is not None else runtime_inputs()
    artifacts = runpy.run_path(str(ROOT / 'tools/flasher-artifacts.py'))
    packages, wheels = artifacts['inventory'](importlib.metadata.distributions() if distributions is None else distributions)
    files = {'Python-LICENSE.txt': python_license}
    files.update({'wheels/' + key: value for key, value in wheels.items()})
    sources = {}
    for name in ('tcl', 'tk'):
        url = upstream_terms(name, versions[name])
        data = fetch(url)
        files[f'{name}-{versions[name]}-license.terms'] = data
        sources[name] = {'version': versions[name], 'url': url, 'sha256': digest(data)}
    if not python_license or sum(map(len, files.values())) > MAX_TOTAL:
        raise ValueError('Missing license or notice size budget exceeded.')
    manifest = {'format': 1, 'version': version(), 'runtime': versions,
                'sources': sources, 'packages': packages,
                'files': {key: {'bytes': len(data), 'sha256': digest(data)} for key, data in sorted(files.items())},
                'boundary': 'Version-matched Python/Tcl/Tk and installed wheel notices; individual-file terms, other system libraries, firmware and corresponding-source obligations still require redistribution review.'}
    directory.mkdir(mode=0o700, parents=True)
    for name, data in files.items():
        if len(data) > MAX_FILE:
            raise ValueError('Oversized notice.')
        target = directory / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(data)
    (directory / 'NOTICE-PROVENANCE.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def validated_datas(directory):
    directory = directory.resolve(strict=True)
    manifest_path = directory / 'NOTICE-PROVENANCE.json'
    if not manifest_path.is_file() or manifest_path.stat().st_size > MAX_FILE:
        raise ValueError('Prepared notice provenance missing.')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 1 or manifest.get('version') != version() or not isinstance(manifest.get('files'), dict):
        raise ValueError('Notice provenance version mismatch.')
    actual, _ = runtime_inputs()
    if manifest.get('runtime') != actual:
        raise ValueError('Notice runtime differs from actual builder.')
    if len(manifest['files']) > 110:
        raise ValueError('Notice count budget exceeded.')
    total = 0
    for name, expected in manifest['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or path.as_posix() != name:
            raise ValueError('Invalid notice path.')
        target = directory / path
        if any(parent.is_symlink() for parent in (target, *target.parents)) or not target.is_file() or target.stat().st_size > MAX_FILE:
            raise ValueError('Notice file missing or unsafe.')
        data = target.read_bytes()
        total += len(data)
        if not isinstance(expected, dict) or expected != {'bytes': len(data), 'sha256': digest(data)} or total > MAX_TOTAL:
            raise ValueError('Notice hash/size mismatch.')
    expected_files = set(manifest['files']) | {'NOTICE-PROVENANCE.json'}
    if {path.relative_to(directory).as_posix() for path in directory.rglob('*') if path.is_file()} != expected_files:
        raise ValueError('Unexpected notice payload.')
    return [(str(directory), 'THIRD-PARTY-NOTICES')]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-notices', required=True, type=Path)
    args = parser.parse_args()
    prepare(args.prepare_notices)
    print('Prepared version-matched macOS bundle notices.')
