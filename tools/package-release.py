#!/usr/bin/env python3
"""Assemble local implementation source/optional SD assets; never upload or tag."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import zipfile
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'dist');p.add_argument('--pi-candidate',type=Path);p.add_argument('--wheel',type=Path);p.add_argument('--ota-stage',type=Path);args=p.parse_args()
    version=(ROOT/'VERSION').read_text().strip();lock=json.loads((ROOT/'core.lock.json').read_text());manifest=json.loads((ROOT/'PUBLIC-SOURCE.json').read_text())
    args.output.mkdir(parents=True,exist_ok=True)
    name=f'astro_bridge_implementations-{version}.tar.gz';target=args.output/name
    with target.open('wb') as stream,gzip.GzipFile(fileobj=stream,mode='wb',mtime=0,filename='') as compressed,tarfile.open(fileobj=compressed,mode='w') as archive:
        for name in sorted([*manifest['files'],'PUBLIC-SOURCE.json']):
            path=ROOT/name
            if path.is_symlink():raise ValueError('Source symlink')
            data=path.read_bytes();item=tarfile.TarInfo(f'astro_bridge_implementations-{version}/'+name);item.size=len(data);item.mode=0o755 if path.stat().st_mode & 0o111 else 0o644;archive.addfile(item,io.BytesIO(data))
    artifacts={target.name:digest(target)}
    installer=args.output/'install-docker.py'
    installer.write_text((ROOT/'tools/install-docker.py').read_text().replace('SOURCE_SHA256 = None', 'SOURCE_SHA256 = '+repr(digest(target)))
        +'\n'+''.join('# '+line+'\n' for line in (ROOT/'LICENSE').read_text().splitlines()))
    wrapper=args.output/'install-docker.sh'
    wrapper.write_text((ROOT/'tools/install-docker.sh').read_text().replace('__DOCKER_INSTALLER_SHA256__',digest(installer)))
    artifacts.update({installer.name:digest(installer),wrapper.name:digest(wrapper)})
    if args.pi_candidate:
        if not args.wheel or digest(args.wheel)!=lock['sha256']:raise ValueError('Matching core wheel required')
        with zipfile.ZipFile(args.wheel) as archive:build=re.search(r'[0-9a-f]{64}',archive.read('wifi_bridge/build.py').decode())[0]
        candidate=json.loads((args.pi_candidate/'alpine-standard-manifest.json').read_text())
        if candidate['app_build_id']!=build:raise ValueError('SD image contains a different core build')
        image=args.pi_candidate/candidate['asset']
        if image.is_symlink() or image.parent.resolve()!=args.pi_candidate.resolve() or digest(image)!=candidate['sha256']:raise ValueError('SD candidate checksum mismatch')
        for name in (image.name,'alpine-standard-manifest.json','ALPINE-STANDARD-SHA256SUMS','source.json'):
            source=args.pi_candidate/name;destination=args.output/name
            if source.resolve()!=destination.resolve():shutil.copyfile(source,destination)
            artifacts[name]=digest(destination)
    if args.ota_stage:
        if not args.wheel or digest(args.wheel)!=lock['sha256']:raise ValueError('Matching core wheel required for OTA')
        stage_lock=json.loads((args.ota_stage/'core.lock.json').read_text())
        if stage_lock['sha256']!=lock['sha256']:raise ValueError('OTA stage uses a different core')
        subprocess.run([sys.executable,str(args.ota_stage/'tools/package-release.py')],check=True)
        record=json.loads((args.ota_stage/'dist/ota-manifest-linux-alpine-armhf.json').read_text())
        for name in ('ota-manifest-linux-alpine-armhf.json',record['asset']):
            source=args.ota_stage/'dist'/name;destination=args.output/name
            if name==record['asset'] and digest(source)!=record['sha256']:raise ValueError('OTA archive checksum mismatch')
            shutil.copyfile(source,destination);artifacts[name]=digest(destination)
    (args.output/'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name,value in sorted(artifacts.items())))
    (args.output/'release.json').write_text(json.dumps({'version':version,'core':lock,'artifacts':artifacts,'physical_validation':'see doc/reference/validation.md'},indent=2)+'\n')
    print(json.dumps(artifacts,indent=2))


if __name__=='__main__':main()
