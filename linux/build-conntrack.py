#!/usr/bin/env python3
"""Build pinned Netfilter tools into a private prefix; preserve host package files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
SOURCES=(('libnetfilter_conntrack','1.1.1','769d3eaf57fa4fbdb05dd12873b6cb9a5be7844d8937e222b647381d44284820'),
         ('conntrack-tools','1.4.9','c15afe488a8d408c9d6d61e97dbd19f3c591942f62c13df6453a961ca4231cae'))


def main():
    p=argparse.ArgumentParser();p.add_argument('--prefix',type=Path,required=True);args=p.parse_args()
    prefix=args.prefix.absolute()
    if (prefix/'sbin/conntrack').exists():raise SystemExit('Conntrack prefix already contains an executable')
    with tempfile.TemporaryDirectory(prefix='astro-conntrack-') as folder:
        root=Path(folder)
        env=dict(os.environ,PKG_CONFIG_PATH=str(prefix/'lib/pkgconfig'),CPPFLAGS='-I'+str(prefix/'include'),LDFLAGS='-L'+str(prefix/'lib')+' -Wl,-rpath,'+str(prefix/'lib'),LD_LIBRARY_PATH=str(prefix/'lib'))
        for name,version,checksum in SOURCES:
            stem=name+'-'+version;url='https://www.netfilter.org/pub/'+name+'/'+stem+'.tar.xz';archive=root/(stem+'.tar.xz')
            with urllib.request.urlopen(url,timeout=60) as response:data=response.read(8*1024*1024+1)
            if len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest()!=checksum:raise ValueError('Netfilter source checksum mismatch')
            archive.write_bytes(data)
            with tarfile.open(archive) as source:
                for item in source:
                    parts=Path(item.name).parts
                    if not parts or parts[0]!=stem or '..' in parts or item.name.startswith('/') or not (item.isfile() or item.isdir()):raise ValueError('Unexpected Netfilter source member')
                source.extractall(root)  # Members were bounded to regular files/directories above (Python 3.11 compatible).
            project=root/stem
            flags=['--prefix='+str(prefix)]
            if name=='conntrack-tools':flags+=['--disable-conntrackd','--disable-cthelper','--disable-cttimeout']
            subprocess.run(['./configure',*flags],cwd=project,env=env,check=True)
            subprocess.run(['make','-j2'],cwd=project,env=env,check=True)
            subprocess.run(['make','install'],cwd=project,env=env,check=True)
            notices=prefix/'share/doc'/name;notices.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(project/'COPYING',notices/'COPYING');shutil.copyfile(archive,notices/archive.name)
            (notices/'SOURCE.json').write_text(json.dumps({'url':url,'sha256':checksum,'configure':flags},indent=2)+'\n')
        subprocess.run([str(prefix/'sbin/conntrack'),'--version'],check=True)


if __name__=='__main__':main()
