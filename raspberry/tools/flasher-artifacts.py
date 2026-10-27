"""Record a built launcher; never build, flash, sign or publish it."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def architecture(machine):
    value={'arm64':'ARM64','aarch64':'ARM64','x86_64':'X64','amd64':'X64','armv7l':'ARM'}.get(machine.lower())
    if not value:raise ValueError('Unsupported actual launcher architecture.')
    return value


def inventory(distributions):
    packages=[];notices={};total=0
    for distribution in distributions:
        metadata=distribution.metadata;name=metadata['Name'];version=distribution.version
        if not name or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+',version):raise ValueError('Unsafe package identity.')
        packages.append({'name':name,'version':version,'license':metadata.get('License-Expression') or metadata.get('License') or 'not recorded'})
        for entry in distribution.files or ():
            relative=Path(str(entry))
            # Only public wheel license/notice files, never site paths/config/code.
            if relative.is_absolute() or '..'in relative.parts:continue
            if not any(part.endswith('.dist-info') for part in relative.parts):continue
            if not any(word in relative.name.upper() for word in ('LICENSE','COPYING','NOTICE')):continue
            path=Path(distribution.locate_file(entry))
            if path.is_symlink() or not path.is_file():continue
            if path.stat().st_size>2*1024*1024:raise ValueError('Oversized wheel notice.')
            data=path.read_bytes();total+=len(data)
            if total>10*1024*1024 or len(notices)>=100:raise ValueError('Too many wheel notices.')
            key=f'{name}-{version}/'+relative.as_posix()
            notices[key]=data
    return sorted(packages,key=lambda item:item['name'].lower()),notices


def record(output, runner_os, runner_arch, *, machine=None, distributions=None):
    actual=machine or platform.machine()
    if architecture(actual)!=runner_arch:raise ValueError('Runner label differs from actual executable architecture.')
    expected={'Darwin':'macOS','Windows':'Windows','Linux':'Linux'}.get(platform.system())
    if expected!=runner_os:raise ValueError('Runner OS differs from actual builder OS.')
    target=output/f'flasher-{runner_os}-{runner_arch}'
    if target.exists():raise ValueError('Use a new dedicated launcher artifact directory.')
    suffix='.exe' if runner_os=='Windows' else ''
    binary=output/('astro-bridge-flasher'+suffix)
    if binary.is_symlink() or not binary.is_file():raise ValueError('Built launcher missing.')
    name=f'astro-bridge-flasher-{runner_os}-{runner_arch}'
    app=output/(name+'.app.zip')
    if runner_os=='macOS' and (app.is_symlink() or not app.is_file()):raise ValueError('Matching macOS app archive missing.')
    packages,notices=inventory(importlib.metadata.distributions() if distributions is None else distributions)
    versions={item['name'].lower():item['version'] for item in packages}
    if versions.get('pyinstaller')!='6.16.0' or versions.get('certifi')!='2026.10.19':raise ValueError('Launcher dependency pins differ.')
    version=re.search(r'__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"',(ROOT/'wifi_bridge/__init__.py').read_text())[1]
    target.mkdir()
    shutil.copy2(binary,target/(name+suffix))
    if runner_os=='macOS':shutil.copy2(app,target/app.name)
    with zipfile.ZipFile(target/f'FLASHER-WHEEL-NOTICES-{runner_os}-{runner_arch}.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path,data in sorted(notices.items()):archive.writestr(path,data)
        archive.writestr('BOUNDARY.txt','Declared installed wheel notices only. Bundled Python/Tcl/Tk, OS libraries, bootloader and vendor engine notices require generated-artifact review before distribution. This is not a complete license clearance.\n')
    commit=os.environ.get('GITHUB_SHA')
    if commit and not re.fullmatch(r'[0-9a-f]{40,64}',commit):raise ValueError('Invalid CI source identity.')
    value={'source_commit':commit,'format':1,'version':version,'os':runner_os,'architecture':runner_arch,'actual_machine':actual,
           'python':platform.python_version(),'packages':packages,'wheel_notices':sorted(notices),
           'launcher_source_sha256':sha(ROOT/'tools/astro-bridge-flasher.py'),
           'assets':{p.name:sha(p) for p in sorted(target.iterdir())},
           'signing':'No Developer ID/notarization/Windows publisher trust is attested.',
           'validation':'Build/CLI smoke only; no GUI, removable-media or physical write acceptance.'}
    (target/f'FLASHER-PROVENANCE-{runner_os}-{runner_arch}.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    (target/f'FLASHER-SHA256SUMS-{runner_os}-{runner_arch}').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in sorted(target.iterdir()) if p.is_file()))
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--os',required=True,choices=('Linux','Windows','macOS'));parser.add_argument('--arch',required=True,choices=('X64','ARM64','ARM'))
    parser.add_argument('--directory',type=Path,default=ROOT/'dist');args=parser.parse_args()
    print(record(args.directory,args.os,args.arch))
