#!/usr/bin/env python3
"""Plan or deploy the pinned Astro Bridge Docker release and required dependencies."""
# SPDX-License-Identifier: MIT
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

VERSION = '1.0.1'
SOURCE_SHA256 = None  # Embedded by the release builder.


def download(url, limit=32*1024*1024):
    with urllib.request.urlopen(url,timeout=30) as response:
        if not response.url.startswith('https://'):raise ValueError('HTTPS download required')
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('Download exceeds size limit')
    return data


def unpack(data, destination):
    expected='astro_bridge_implementations-'+VERSION
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
        members=archive.getmembers();total=0
        if len(members)>4096:raise ValueError('Too many source files')
        for member in members:
            path=PurePosixPath(member.name);total+=member.size
            if (path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=expected
                    or not member.isfile() or total>32*1024*1024):raise ValueError('Unsafe release source')
        destination.mkdir(parents=True,mode=0o700)
        for member in members:
            relative=PurePosixPath(member.name).relative_to(expected)
            target=destination.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read());target.chmod(0o755 if member.mode&0o111 else 0o644)


def run(command, **kwargs):
    print('+ '+' '.join(map(str,command)),flush=True)
    return subprocess.run(list(map(str,command)),check=True,**kwargs)


def install_engine():
    system=platform.system()
    if system=='Linux':
        if os.geteuid()!=0:raise ValueError('Installing Docker dependencies requires sudo on Linux')
        release={}
        for line in Path('/etc/os-release').read_text().splitlines():
            key,sep,value=line.partition('=')
            if sep:release[key]=value.strip('"\'')
        distro=release.get('ID');codename=release.get('VERSION_CODENAME','')
        if distro not in ('debian','ubuntu') or not re.fullmatch('[a-z][a-z0-9-]+',codename):
            raise ValueError('Automatic engine setup supports Debian/Ubuntu; install Docker Engine and Compose manually on other distributions')
        run(['apt-get','update']);run(['apt-get','install','-y','ca-certificates','gnupg'])
        key=download('https://download.docker.com/linux/'+distro+'/gpg',65536)
        checked=subprocess.run(['gpg','--batch','--show-keys','--with-colons'],input=key,capture_output=True,check=True)
        if b'9DC858229FC7DD38854AE2D88D81803C0EBFCD88' not in checked.stdout:raise ValueError('Unexpected Docker repository signing key')
        keypath=Path('/etc/apt/keyrings/astro-bridge-docker.asc');keypath.parent.mkdir(parents=True,exist_ok=True);keypath.write_bytes(key);keypath.chmod(0o644)
        architecture=subprocess.check_output(['dpkg','--print-architecture'],text=True).strip()
        if architecture not in ('amd64','arm64','armhf'):raise ValueError('Unsupported Docker host architecture')
        source=f'Types: deb\nURIs: https://download.docker.com/linux/{distro}\nSuites: {codename}\nComponents: stable\nArchitectures: {architecture}\nSigned-By: {keypath}\n'
        path=Path('/etc/apt/sources.list.d/astro-bridge-docker.sources')
        if path.exists() and path.read_text()!=source:raise ValueError('Existing Docker repository differs; review it manually')
        path.write_text(source)
        run(['apt-get','update']);run(['apt-get','install','-y','docker-ce','docker-ce-cli','containerd.io','docker-buildx-plugin','docker-compose-plugin'])
        run(['systemctl','enable','--now','docker'])
    elif system=='Windows' and shutil.which('winget'):
        run(['winget','install','--exact','--id','Docker.DockerDesktop','--source','winget'])
        raise ValueError('Docker Desktop was installed. Open it and complete setup (a reboot may be required), then run this installer again')
    elif system=='Darwin' and shutil.which('brew'):
        run(['brew','install','--cask','docker'])
        run(['open','-a','Docker'])
        raise ValueError('Docker Desktop was installed. Complete its first-run setup, then run this installer again')
    else:
        raise ValueError('Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and rerun this installer')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Install missing dependencies and start the reviewed deployment')
    parser.add_argument('--mode',choices=('demo','linux'),default='demo')
    parser.add_argument('--prefix',type=Path,default=Path.home()/('.local/share/astro-bridge-docker-'+VERSION))
    parser.add_argument('--project',default='astro-bridge')
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--source',type=Path,help='Verified offline implementation source archive')
    parser.add_argument('--sha256',help='Expected checksum for an offline source archive')
    args=parser.parse_args();prefix=args.prefix.expanduser().absolute()
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,47}',args.project) or not 1024<=args.port<=65535:parser.error('Invalid project or demo port')
    if args.mode=='linux' and platform.system()!='Linux':parser.error('Real Wi-Fi control requires Linux; use --mode demo on macOS/Windows')
    print('Astro Bridge '+VERSION+' Docker deployment: '+args.mode)
    print('Install Docker Engine/Compose if missing; Docker Desktop uses the OS package manager when available.')
    if args.mode=='linux':print('Dedicated Linux host: install NetworkManager, AP/DHCP services and networking dependencies; enable forwarding.')
    print('Download and verify the pinned deployment source/core, build its image, and start Compose with persistent state.')
    print('Deployment directory: '+str(prefix))
    if not args.apply:print('Plan only. Run again with --apply after reviewing these changes.');return
    if prefix.exists() or prefix.is_symlink():parser.error('Use a new deployment prefix; existing installations are preserved')
    if args.mode=='linux' and os.geteuid()!=0:parser.error('Use sudo for real Linux networking setup')
    expected=args.sha256 if args.source else SOURCE_SHA256
    if not expected or not re.fullmatch('[0-9a-fA-F]{64}',expected):parser.error('Use the release installer, or provide --source and --sha256')
    if args.sha256 and not args.source:parser.error('--sha256 requires --source')
    if args.source and args.source.stat().st_size>32*1024*1024:parser.error('Source archive exceeds size limit')
    data=args.source.read_bytes() if args.source else download(f'https://github.com/Sqound/astro-bridge-implementations/releases/download/v{VERSION}/astro_bridge_implementations-{VERSION}.tar.gz')
    if hashlib.sha256(data).hexdigest()!=expected.lower():parser.error('Deployment source checksum mismatch; no application was installed')
    if not shutil.which('docker') or subprocess.run(['docker','compose','version'],capture_output=True).returncode:
        install_engine()
    if subprocess.run(['docker','info'],capture_output=True).returncode:
        if platform.system()=='Linux' and os.geteuid()==0:run(['systemctl','start','docker'])
        if subprocess.run(['docker','info'],capture_output=True).returncode:parser.error('Docker is not ready. Start Docker Desktop/Engine and check permissions, then rerun')
    unpack(data,prefix)
    run([sys.executable,prefix/'tools/core-artifact.py','fetch','--output',prefix/'build/core'])
    lock=json.loads((prefix/'core.lock.json').read_text());wheel=prefix/'build/core'/lock['asset']
    if args.mode=='linux':run([sys.executable,prefix/'linux/host-install.py','--mode','docker','--apply','--wheel',wheel])
    run([sys.executable,prefix/'docker/build.py','--wheel',wheel,'--target',args.mode])
    compose=prefix/'docker'/('compose.demo.yaml' if args.mode=='demo' else 'compose.linux.yaml')
    env=dict(os.environ,ASTRO_BRIDGE_PORT=str(args.port))
    run(['docker','compose','-p',args.project,'-f',compose,'up','-d'],env=env)
    if args.mode=='demo':print(f'Open http://127.0.0.1:{args.port}; demo password: demo-telescope-2026')
    else:print('Open the host IP or hotspot. Initial private access code: sudo cat /etc/wifi-bridge/bootstrap.json')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as error:raise SystemExit(str(error))
