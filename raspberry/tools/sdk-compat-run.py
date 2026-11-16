#!/usr/bin/env python3
"""Disposable CI metadata/probe runner; never installs or retains vendor bytes."""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


elf = module('elf_inspect', 'elf-inspect.py')
probe = module('sdk_probe', 'sdk-probe.py')


archive_io = module('asi_archive', 'asi_archive.py')
check_url, download, copy_bounded = archive_io.check_url, archive_io.download, archive_io.copy_bounded


def select_library(archive, nested_name, member_name, target):
    archive_io.select_members(archive, nested_name, {member_name: (target, elf.MAX_BYTES)},
                              target.with_name('nested.tar.bz2'))


def symbols_required(info, providers):
    exports = {}
    for provider in providers:
        for entry in provider['exports']:
            exports.setdefault(entry['name'], set()).add(entry['version'])
    missing, versions = [], []
    for version, symbols in info['undefined_by_version'].items():
        for symbol in symbols:
            row = dict(symbol, version=version)
            if symbol['name'] not in exports:
                missing.append(dict(row, shim='requires semantic review; no safe shim established'))
            elif version != 'unversioned' and version not in exports[symbol['name']]:
                versions.append(row)
    return {'missing_by_name': missing, 'version_not_exported': versions,
            'interpretation': 'Static candidates only; musl version handling and actual load verdict must be reviewed.'}


def system_providers():
    providers, skipped, paths = [], [], {}
    for directory in ('/lib', '/usr/lib'):
        for path in pathlib.Path(directory).glob('*.so*'):
            if path.is_file():
                paths.setdefault(path.resolve(), set()).add(path.name)
    if len(paths) > 512:
        raise ValueError('provider_limit')
    for path in sorted(paths):
        try:
            info = elf.inspect_path(path)
            providers.append(dict(path=str(path), aliases=sorted(paths[path]), **info))
        except (OSError, elf.ELFError):
            skipped.append(path.name)
    return providers, skipped


def provider_scope(info, providers, interpreter):
    by_name = {name: p for p in providers for name in p['aliases']}
    pending = list(info['needed']) + list(interpreter['needed']) + ['libgcompat.so.0']
    selected, missing = {}, set()
    while pending:
        name = pending.pop()
        provider = by_name.get(name)
        if provider is None:
            missing.add(name)
        elif provider['path'] not in selected:
            selected[provider['path']] = provider
            pending.extend(provider['needed'])
    return list(selected.values()), sorted(missing)


def run(arch):
    if (sys.platform != 'linux' or os.environ.get('SDK_PROBE_CONTAINER') != '1'
            or pathlib.Path('/dev/bus/usb').exists()):
        raise ValueError('disposable_no_usb_container_required')
    expected = {'armv6': ('arm1176', 40), 'arm64': ('cortex-a53', 183)}[arch]
    if os.environ.get('QEMU_CPU') != expected[0]:
        raise ValueError('explicit_qemu_cpu_required')
    manifest = json.loads((ROOT / 'tools/sdk/manifest.json').read_text())
    vendor = manifest['zwo']
    if vendor['enabled'] is not True or manifest['qhy']['enabled'] is not False:
        raise ValueError('unreviewed_vendor_gate')
    with tempfile.TemporaryDirectory(prefix='sdk-compat-') as directory:
        temp = pathlib.Path(directory)
        identity = download(vendor['url'], temp / 'sdk.zip', vendor['sha256'])
        library = temp / 'libASICamera2.so'
        select_library(temp / 'sdk.zip', vendor['nested'], vendor['libraries'][arch], library)
        info = elf.inspect_path(library)
        if info['machine_id'] != expected[1]:
            raise ValueError('wrong_elf_machine')
        providers, skipped = system_providers()
        scope, absent = provider_scope(info, providers, elf.inspect_path(sys.executable))
        result = {'schema_version': 1, 'arch': arch, 'qemu_cpu': expected[0],
                  'sdk': identity, 'elf': info,
                  'providers': [{'path': p['path'], 'needed': p['needed']} for p in providers],
                  'uninspected_providers': skipped, 'missing_needed': absent,
                  'analyzed_scope': [p['path'] for p in scope],
                  'symbols': symbols_required(info, scope),
                  'probe': probe.probe(library, 'zwo'),
                  'qhy': {'status': 'not_executed', 'reason': manifest['qhy']['reason']}}
        # Full exports are unnecessary log volume; undefined symbols remain complete.
        result['elf'].pop('exports')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arch', choices=('armv6', 'arm64'))
    args = parser.parse_args()
    try:
        result = run(args.arch)
        print(json.dumps(result, sort_keys=True))
        return 0 if any(r.get('ok') is True for r in result['probe']['results']) else 1
    except Exception as exc:
        # Neither redirect URLs nor vendor payloads nor unbounded exception text.
        code = str(exc) if type(exc) is ValueError and re.fullmatch('[a-z_]{1,64}', str(exc)) else 'probe_setup_failed'
        print(json.dumps({'schema_version': 1, 'error': code, 'exception_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
