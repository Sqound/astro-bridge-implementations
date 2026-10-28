#!/usr/bin/env python3
"""Build and smoke-test a native launcher on its actual OS/CPU; never flash media."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--wheel',type=Path,required=True);args=parser.parse_args()
    core=load('core',ROOT/'tools/core-artifact.py')
    source=ROOT/'build/flasher-source';core.stage(args.wheel,source)
    version=(ROOT/'VERSION').read_text().strip();system=platform.system();machine=platform.machine().lower()
    arch={'amd64':'x64','x86_64':'x64','arm64':'arm64','aarch64':'arm64'}.get(machine)
    if system not in ('Linux','Windows','Darwin') or not arch:raise ValueError('Unsupported native build platform')
    name=f'astro-bridge-flasher-{version}-{dict(Linux="linux",Windows="windows",Darwin="macos")[system]}-{arch}'
    notices=ROOT/'build/flasher-notices';helper=load('notices',source/'tools/flasher-macos.py');helper.prepare(notices)
    shutil.copyfile(ROOT/'LICENSE',notices/'Astro-Bridge-MIT-LICENSE.txt')
    openssl=re.search(r'OpenSSL ([0-9]+\.[0-9]+\.[0-9]+[a-z]?)',ssl.OPENSSL_VERSION)
    if not openssl:raise ValueError('Unidentified bundled OpenSSL runtime')
    release=openssl[1];tag='openssl-'+release if release.startswith('3.') else 'OpenSSL_'+release.replace('.','_')
    url='https://raw.githubusercontent.com/openssl/openssl/'+tag+'/LICENSE.txt'
    with urllib.request.urlopen(url,timeout=30) as response:data=response.read(65537)
    if not 100<len(data)<65536:raise ValueError('Invalid OpenSSL notice')
    (notices/'OpenSSL-LICENSE.txt').write_bytes(data)
    provenance=json.loads((notices/'NOTICE-PROVENANCE.json').read_text())
    provenance['files']={p.relative_to(notices).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)} for p in notices.rglob('*') if p.is_file() and p.name!='NOTICE-PROVENANCE.json'}
    provenance['openssl']={'version':release,'license_url':url}
    (notices/'NOTICE-PROVENANCE.json').write_text(json.dumps(provenance,indent=2)+'\n')
    output=ROOT/'build/flasher-output';env=dict(os.environ,ASTRO_FLASHER_NOTICES=str(notices))
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--distpath',str(output),'--workpath',str(ROOT/'build/flasher-work')]
    if system=='Darwin':command += [str(source/'tools/flasher-macos.spec')]
    else:command += ['--onedir','--name','AstroBridgeFlasher','--specpath',str(ROOT/'build'),'--add-data',str(notices)+os.pathsep+'THIRD-PARTY-NOTICES',str(source/'tools/astro-bridge-flasher.py')]
    subprocess.run(command,env=env,check=True)
    bundle=output/('Astro Bridge Flasher.app' if system=='Darwin' else 'AstroBridgeFlasher')
    executable=bundle/('Contents/MacOS/Astro Bridge Flasher' if system=='Darwin' else 'AstroBridgeFlasher'+('.exe' if system=='Windows' else ''))
    subprocess.run([str(executable),'--help'],check=True,timeout=30)
    subprocess.run([str(executable),'--self-test'],check=True,timeout=30)
    fixture=ROOT/'build/verification-only.img';fixture.write_bytes(b'synthetic verification fixture\n')
    subprocess.run([str(executable),'--image',str(fixture),'--sha256',sha(fixture),'--verify-only'],check=True,timeout=30)
    if system=='Linux':
        # Retain the distribution notices for system libraries actually bundled.
        listing=subprocess.check_output(['/sbin/ldconfig','-p'],text=True)
        libraries={line.split()[0]:line.rsplit(' => ',1)[1] for line in listing.splitlines() if ' => ' in line}
        for binary in bundle.rglob('*.so*'):
            original=libraries.get(binary.name)
            if not original:continue
            result=subprocess.run(['dpkg-query','-S',original],capture_output=True,text=True)
            if result.returncode:continue
            package=result.stdout.split(': ',1)[0].split(':')[0]
            notice=Path('/usr/share/doc')/package/'copyright'
            if notice.is_file():
                dest=bundle/'THIRD-PARTY-NOTICES/system-libraries';dest.mkdir(parents=True,exist_ok=True);shutil.copyfile(notice,dest/(package+'-copyright'))
    package=bundle
    if system=='Darwin':
        package=output/name;package.mkdir();shutil.move(str(bundle),str(package/bundle.name))
    shutil.copyfile(ROOT/'LICENSE',package/'LICENSE.txt')
    (package/'README.txt').write_text('Astro Bridge Flasher '+version+'\nExtract the complete folder before starting the launcher.\nThe official Raspberry Pi Imager writing engine downloads and verifies automatically.\nMedia writing always requires explicit drive selection and confirmation.\nmacOS/Windows publisher signing and notarization are not provided by this build.\n')
    assets=ROOT/'dist/flashers';assets.mkdir(parents=True,exist_ok=True)
    if system=='Linux':
        archive=assets/(name+'.tar.gz')
        def clean(info):info.uid=info.gid=0;info.uname=info.gname='';return info
        with tarfile.open(archive,'w:gz') as target:target.add(bundle,arcname=name,filter=clean)
    elif system=='Darwin':
        archive=assets/(name+'.app.zip');subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(package),str(archive)],check=True)
    else:
        archive=assets/(name+'.zip')
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as target:
            for path in bundle.rglob('*'):
                if path.is_file():target.write(path,str(Path(name)/path.relative_to(bundle)))
    record={'version':version,'os':system,'architecture':arch,'source_commit':os.environ.get('GITHUB_SHA'),'asset':archive.name,'sha256':sha(archive),'smoke':['help','GUI/TLS runtime','local checksum verification'],'physical_media_written':False,'launcher_source_sha256':sha(source/'tools/astro-bridge-flasher.py'),'builder_source_sha256':sha(Path(__file__)),'signing':'Unsigned; no publisher trust or notarization attested','core':json.loads((ROOT/'core.lock.json').read_text())}
    (assets/(name+'.json')).write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))


if __name__=='__main__':main()
