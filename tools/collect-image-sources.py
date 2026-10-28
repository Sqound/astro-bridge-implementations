#!/usr/bin/env python3
"""Archive exact signed-package build recipes and upstream sources for an SD image."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def inside(image, output):
    subprocess.run(['apk','add','--no-cache','alpine-sdk','git','python3'],check=True)
    repository=Path('/work/aports');repository.mkdir(parents=True)
    subprocess.run(['git','init','-q',str(repository)],check=True)
    subprocess.run(['git','-C',str(repository),'remote','add','origin','https://github.com/alpinelinux/aports.git'],check=True)
    def git(*args,**kwargs):return subprocess.run(['git','-C',str(repository),*args],check=True,**kwargs)
    rows=json.loads((image/'source-packages.json').read_text())+json.loads((image/'source-kernel-packages.json').read_text())
    groups={}
    for row in rows:
        origin=row['origin'];commit=row.get('commit','')
        if not re.fullmatch('[A-Za-z0-9+_.-]+',origin) or not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Package lacks exact recipe provenance: '+origin)
        group=groups.setdefault(origin,{'commit':commit,'packages':[]})
        if group['commit']!=commit:raise ValueError('Mixed recipe revisions for '+origin)
        if row not in group['packages']:group['packages'].append(row)
    packages=output/'packages';packages.mkdir(parents=True,exist_ok=True)
    manifest={'format':1,'architecture':'armhf','image_sha256':json.loads((image/'alpine-standard-manifest.json').read_text())['sha256'],'origins':{}}
    for index,(origin,group) in enumerate(sorted(groups.items())):
        commit=group['commit'];print(f'SOURCE {index+1}/{len(groups)} {origin} {commit}',flush=True)
        git('fetch','--depth','1','origin',commit,timeout=180)
        git('checkout','-q','--detach','FETCH_HEAD')
        candidates=[repository/repo/origin for repo in ('main','community','testing') if (repository/repo/origin/'APKBUILD').is_file()]
        if len(candidates)!=1:raise ValueError('Ambiguous/missing recipe: '+origin)
        recipe=candidates[0];env=dict(os.environ,CHOST='armv6-alpine-linux-musleabihf',CARCH='armhf')
        command=['abuild','-F','-f','-d','-P',str(packages),'-s',str(recipe)]
        listing=subprocess.check_output(command+['listpkg'],cwd=recipe,env=env,text=True)
        for row in group['packages']:
            if row['name']+'-'+row['version']+'.apk' not in listing:raise ValueError('Recipe version differs from signed package: '+row['name'])
        previous=set((packages/'src').glob('*.src.tar.gz')) if (packages/'src').exists() else set()
        subprocess.run(command+['fetch','verify','srcpkg'],cwd=recipe,env=env,check=True,timeout=1200)
        created=set((packages/'src').glob('*.src.tar.gz'))-previous
        if len(created)!=1:raise ValueError('Missing source package: '+origin)
        source=created.pop()
        manifest['origins'][origin]={**group,'recipe':recipe.relative_to(repository).as_posix(),'source_asset':source.relative_to(output).as_posix(),'sha256':sha(source)}
        (output/'OS-SOURCE-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
        # Only this job's disposable official checkout; retain the source archive.
        git('clean','-fdx','--',str(recipe.relative_to(repository)))
    for name in ('package-lock.json','source.json','container.json','builder-packages.json'):
        (output/name).write_bytes((image/name).read_bytes())
    (output/'README.txt').write_text('Exact upstream source archives and Alpine APKBUILD/install/patch files for this image.\nEach origin is matched to the signed APK commit and version; abuild verifies upstream checksums.\nUse Alpine abuild and the recorded recipe to rebuild a package; image assembly recipes are in the matching implementation release.\nIndividual source packages keep their upstream licenses.\n')
    print('PASS complete source closure: '+str(len(groups))+' package origins',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image-dir',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--inside',nargs=2,type=Path);args=parser.parse_args()
    if args.inside:inside(*args.inside);return
    if not args.image_dir or not args.output:parser.error('--image-dir and --output required')
    if args.output.exists():parser.error('Use a fresh source output directory')
    args.output.mkdir(parents=True)
    base=json.loads((ROOT/'raspberry/image/alpine/standard/inputs.json').read_text())['container']
    subprocess.run(['docker','run','--rm','-v',str(args.image_dir.resolve())+':/image:ro','-v',str(args.output.resolve())+':/out','-v',str(Path(__file__).resolve())+':/collector.py:ro','--entrypoint','/bin/sh',base,'-c','mkdir -p /work && apk add --no-cache python3 && python3 /collector.py --inside /image /out'],check=True)
    # The image also contains the unmodified MPL-licensed Python VAPID source wheel.
    module=ast.parse((ROOT/'raspberry/scripts/install-push-runtime.py').read_text())
    pins={n.targets[0].id:ast.literal_eval(n.value) for n in module.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('WHEEL','SHA256')}
    with urllib.request.urlopen(pins['WHEEL'],timeout=30) as response:data=response.read(131073)
    if len(data)>131072 or hashlib.sha256(data).hexdigest()!=pins['SHA256']:raise ValueError('VAPID source integrity mismatch')
    wheels=args.output/'python-wheels';wheels.mkdir();(wheels/'py_vapid-1.9.6-py2.py3-none-any.whl').write_bytes(data)
    assets=ROOT/'dist/pi-sources';assets.mkdir(parents=True,exist_ok=True)
    version=(ROOT/'VERSION').read_text().strip();archive=assets/f'astro-bridge-{version}-os-sources.tar.gz'
    def clean(info):info.uid=info.gid=0;info.uname=info.gname='';return info
    with tarfile.open(archive,'w:gz',compresslevel=1) as target:target.add(args.output,arcname='os-sources',filter=clean)
    source_assets=[archive]
    if archive.stat().st_size>1500*1024*1024:
        source_assets=[]
        with archive.open('rb') as stream:
            for index in range(100):
                first=stream.read(1024*1024)
                if not first:break
                part=assets/(archive.name+f'.part{index+1:03d}');source_assets.append(part)
                with part.open('wb') as output:
                    output.write(first);remaining=1499*1024*1024
                    while remaining:
                        block=stream.read(min(1024*1024,remaining))
                        if not block:break
                        output.write(block);remaining-=len(block)
        archive.unlink()
    (assets/'OS-SOURCE-MANIFEST.json').write_bytes((args.output/'OS-SOURCE-MANIFEST.json').read_bytes())
    (assets/'OS-SOURCE-SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in source_assets))
    print('PASS sources ready: '+str(archive))


if __name__=='__main__':main()
