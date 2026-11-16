#!/usr/bin/env python3
"""Assemble a file-only Pi 5 image from independently prepared inputs.

This deliberately does not invent or validate a boot overlay or package closure.
The caller must verify those inputs before shipping the output.
"""
import argparse, hashlib, os, pathlib, re, shutil, struct, subprocess, tempfile

BASE_SHA = 'baba774960c7d3eecacb3322ac642f784b87a2f1eed94d5568c3421eb5b4549a'
P1_OFFSET, P1_SECTORS = 2048, 264192
P2_OFFSET, P2_SECTORS = 266240, 2097152
SECTOR = 512
SEED_OFFSET, SEED_SIZE = 65536, 4096
SEED_MAGIC = b'ASTRO-WIFI-SEED-1\n'

def assert_seed_blank(path):
    with path.open('rb') as f:
        f.seek(SEED_OFFSET)
        if f.read(SEED_SIZE) != bytes(SEED_SIZE):
            raise ValueError('seed range is not blank')

def reserve_seed(stream):
    stream.seek(SEED_OFFSET)
    stream.write(SEED_MAGIC + bytes(SEED_SIZE-len(SEED_MAGIC)))

def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        while b:=f.read(1024*1024): h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    for name in ('base','overlay','data','output','mtools','e2fsprogs'):
        p.add_argument('--'+name,required=True,type=pathlib.Path)
    p.add_argument('--wifi-seed', action='store_true')
    a=p.parse_args()
    if digest(a.base)!=BASE_SHA: raise SystemExit('unexpected official base')
    if a.output.exists(): raise SystemExit('refusing to overwrite image')
    if not a.overlay.is_file() or not a.data.is_dir(): raise SystemExit('missing input')
    original=a.base.read_bytes()[:512]
    p1=struct.unpack_from('<B3sB3sII',original,446)
    if original[510:512]!=b'\x55\xaa' or p1[0]!=128 or p1[4:]!=(P1_OFFSET,P1_SECTORS): raise SystemExit('unexpected p1')
    if any(original[462:510]): raise SystemExit('unexpected additional partition')
    if a.wifi_seed:
        assert_seed_blank(a.base)
    # Output is a regular file, never a block device. One new p2 MBR entry.
    shutil.copyfile(a.base,a.output)
    try:
        with a.output.open('r+b') as f:
            f.truncate((P2_OFFSET+P2_SECTORS)*SECTOR)
            if a.wifi_seed:
                reserve_seed(f)
            f.seek(462)
            f.write(struct.pack('<B3sB3sII',0,b'\xfe\xff\xff',0x83,b'\xfe\xff\xff',P2_OFFSET,P2_SECTORS))
        mcopy=a.mtools/'bin/mcopy'
        subprocess.run([str(mcopy),'-i',f'{a.output}@@{P1_OFFSET*SECTOR}',str(a.overlay),'::/astro-bridge.apkovl.tar.gz'],check=True)
        # A keg-only e2fsprogs (macOS) needs its libraries and configuration; a
        # system installation (--e2fsprogs /usr on Linux) uses its own defaults.
        env=os.environ.copy()
        if (a.e2fsprogs/'lib').is_dir() and a.e2fsprogs!=pathlib.Path('/usr'):
            env['DYLD_LIBRARY_PATH']=str(a.e2fsprogs/'lib')+':/opt/homebrew/opt/gettext/lib'
        if (a.e2fsprogs/'etc/mke2fs.conf').is_file(): env['MKE2FS_CONFIG']=str(a.e2fsprogs/'etc/mke2fs.conf')
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'),prefix='pi5-fs-') as t:
            part=pathlib.Path(t)/'p2.ext4'
            with part.open('wb') as f: f.truncate(P2_SECTORS*SECTOR)
            subprocess.run([str(a.e2fsprogs/'sbin/mke2fs'),'-F','-t','ext4','-L','ASTRO_DATA','-d',str(a.data),str(part)],env=env,check=True)
            # Files generated on macOS otherwise retain the host user's UID/GID.
            commands=[]
            for item in sorted(a.data.rglob('*')):
                path='/' + item.relative_to(a.data).as_posix()
                if not re.fullmatch(r'/[A-Za-z0-9_./+@-]+',path): raise ValueError('unsafe debugfs path: '+path)
                if item.is_symlink(): raise ValueError('symlinks are not permitted in data input')
                commands.extend(['set_inode_field '+path+' uid 0','set_inode_field '+path+' gid 0'])
            command_file=pathlib.Path(t)/'owners.commands'
            command_file.write_text('\n'.join(commands)+'\n')
            subprocess.run([str(a.e2fsprogs/'sbin/debugfs'),'-w','-f',str(command_file),str(part)],env=env,check=True,stdout=subprocess.DEVNULL)
            subprocess.run([str(a.e2fsprogs/'sbin/e2fsck'),'-fn',str(part)],env=env,check=True)
            with a.output.open('r+b') as dst, part.open('rb') as src:
                dst.seek(P2_OFFSET*SECTOR); shutil.copyfileobj(src,dst,1024*1024)
        with a.output.open('rb') as f: mbr=f.read(512)
        if mbr[:462]!=original[:462] or mbr[478:]!=original[478:]: raise RuntimeError('MBR changed outside p2')
        print('image sha256',digest(a.output),'bytes',a.output.stat().st_size)
    except BaseException:
        a.output.unlink(missing_ok=True)
        raise
if __name__=='__main__': main()
