#!/usr/bin/env python3
"""Pin, fetch and stage the exact core wheel; never import a sibling checkout."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verified(wheel,lock=None):
    lock=lock or json.loads((ROOT/'core.lock.json').read_text())
    if wheel.name!=lock['asset'] or digest(wheel)!=lock['sha256']:raise ValueError('Core wheel differs from core.lock.json')
    with zipfile.ZipFile(wheel) as archive:
        expected=f'astro_bridge_core-{lock["version"]}.dist-info/METADATA'
        metadata=archive.read(expected).decode()
        if f'\nVersion: {lock["version"]}\n' not in metadata or '\nName: astro-bridge-core\n' not in metadata:raise ValueError('Core package metadata mismatch')
    return lock


def stage(wheel,destination):
    lock=verified(wheel)
    if destination.is_symlink() or destination.exists():raise ValueError('Use a new staging directory')
    destination.mkdir(parents=True)
    # Fixed deployment recipes + extracted application, not a tracked fork of core.
    shutil.copytree(ROOT/'raspberry',destination,dirs_exist_ok=True)
    shutil.copyfile(ROOT/'LICENSE',destination/'LICENSE')
    names=set();total=0
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            name=PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or stat.S_ISLNK(info.external_attr>>16) or info.filename in names:raise ValueError('Unsafe wheel member')
            names.add(info.filename);total+=info.file_size
            if total>32*1024*1024:raise ValueError('Oversized core')
            if not name.parts or name.parts[0]!='wifi_bridge':continue
            if info.is_dir():continue
            if len(name.parts)>3:raise ValueError('Unexpected core tree')
            path=destination/info.filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.read(info));path.chmod(0o644)
    if not (destination/'wifi_bridge/__main__.py').is_file():raise ValueError('Incomplete core')
    # Packaging the Pi update uses the standalone deployment updater's validation.
    target=destination/'tools/package-release.py'
    target.write_text(target.read_text().replace('from wifi_bridge.ota import asset_names, source_identity','import importlib.util\nspec=importlib.util.spec_from_file_location("platform_ota",ROOT/"scripts/ota.py")\nota=importlib.util.module_from_spec(spec);spec.loader.exec_module(ota)\nasset_names,source_identity=ota.asset_names,ota.source_identity'))
    (destination/'core.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
    inputs={p.relative_to(destination).as_posix():digest(p) for p in sorted(destination.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
    (destination/'STAGED-SOURCE.json').write_text(json.dumps({'format':1,'files':inputs},indent=2)+'\n')
    return lock


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    pin=sub.add_parser('pin');pin.add_argument('wheel',type=Path);pin.add_argument('--version',required=True);pin.add_argument('--url',required=True);pin.add_argument('--source',type=Path,required=True);pin.add_argument('--source-url')
    fetch=sub.add_parser('fetch');fetch.add_argument('--output',type=Path,default=ROOT/'build/core');fetch.add_argument('--source',action='store_true')
    check=sub.add_parser('verify');check.add_argument('wheel',type=Path)
    prepare=sub.add_parser('stage');prepare.add_argument('wheel',type=Path);prepare.add_argument('destination',type=Path)
    args=parser.parse_args()
    if args.action=='pin':
        value={'format':1,'package':'astro-bridge-core','version':args.version,'asset':args.wheel.name,'sha256':digest(args.wheel),'url':args.url}
        value.update(source_asset=args.source.name,source_sha256=digest(args.source),source_url=args.source_url or args.url.rsplit('/',1)[0]+'/'+args.source.name)
        if not args.url.startswith('https://') or not value['source_url'].startswith('https://'):raise ValueError('Use HTTPS release URLs')
        verified(args.wheel,value)
        with tempfile.NamedTemporaryFile(dir=ROOT,mode='w',delete=False) as stream:
            json.dump(value,stream,indent=2);stream.write('\n');temporary=Path(stream.name)
        temporary.replace(ROOT/'core.lock.json')
    elif args.action=='fetch':
        lock=json.loads((ROOT/'core.lock.json').read_text());args.output.mkdir(parents=True,exist_ok=True)
        asset,checksum,url=(lock['source_asset'],lock['source_sha256'],lock['source_url']) if args.source else (lock['asset'],lock['sha256'],lock['url'])
        if Path(asset).name!=asset:raise ValueError('Invalid asset filename')
        target=args.output/asset
        if target.exists():
            if digest(target)!=checksum:raise ValueError('Cached artifact checksum mismatch')
        else:
            if not url.startswith('https://'):raise ValueError('Invalid artifact URL')
            with urllib.request.urlopen(url,timeout=60) as response:data=response.read(32*1024*1024+1)
            if len(data)>32*1024*1024 or hashlib.sha256(data).hexdigest()!=checksum:raise ValueError('Downloaded artifact checksum mismatch')
            with tempfile.NamedTemporaryFile(dir=args.output,delete=False) as stream:stream.write(data);temporary=Path(stream.name)
            temporary.replace(target)
        if not args.source:verified(target)
        print(target)
    elif args.action=='verify':print(json.dumps(verified(args.wheel),indent=2))
    else:print(json.dumps(stage(args.wheel,args.destination),indent=2))


if __name__=='__main__':main()
