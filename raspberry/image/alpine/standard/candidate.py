"""Stamp/inspect an assembled standard candidate; never format or select a disk."""
import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from wifi_bridge.ota import source_identity


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def version():
    return re.search(r'__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"', (ROOT/'wifi_bridge/__init__.py').read_text())[1]


def stamp(app):
    # Same flat source allowlist/hash as application OTA, never copy host bytecode.
    files = {'wifi_bridge/'+str(p.relative_to(app)):p.read_bytes() for p in app.rglob('*')
             if p.is_file() and (len(p.relative_to(app).parts)==1 and p.suffix=='.py'
               or len(p.relative_to(app).parts)==2 and p.parent.name=='static')}
    identity=source_identity(files)
    (app/'build.py').write_text(f'BUILD_ID = "{identity}"\n')
    return identity


def create_snapshot():
    # Colima shares the checkout filesystem, not macOS's default /var/folders.
    # Only this newly allocated private directory is ever bound/cleaned up.
    base=ROOT/'dist'
    if base.is_symlink() or (base.exists() and not base.is_dir()):raise ValueError('Snapshot parent must be a real dist directory.')
    base.mkdir(mode=0o755,exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='.astro-standard-source-',dir=base))


def prepare(output, snapshot):
    if output.is_symlink() or output.exists() or (ROOT/'dist').is_symlink() or output.parent.resolve()!=(ROOT/'dist').resolve():
        raise ValueError('Use a new dedicated child of the repository dist directory.')
    if snapshot.is_symlink() or not snapshot.is_dir() or any(snapshot.iterdir()):raise ValueError('Use an empty private staging directory.')
    output.parent.mkdir(parents=True, exist_ok=True)
    if __import__('shutil').disk_usage(output.parent).free < 8*1024**3:raise ValueError('At least 8 GiB host free space required before building.')
    if not (ROOT/'.git').exists():
        contract=json.loads((ROOT/'STAGED-SOURCE.json').read_text())
        files={}
        for name,expected in contract['files'].items():
            path=Path(name)
            if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe staged input')
            source=ROOT/path
            if source.is_symlink():raise ValueError('Staged input symlink')
            content=source.read_bytes()
            if hashlib.sha256(content).hexdigest()!=expected:raise ValueError('Staged source changed: '+name)
            target=snapshot/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
            if source.stat().st_mode & 0o111:target.chmod(0o755)
            files[name]=content
        lock=json.loads(files['core.lock.json'])
        commit='core-sha256:'+lock['sha256']
        dirty=False
    else:
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        dirty=bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,text=True))
        prefixes=('wifi_bridge','image/alpine/standard','image/alpine/openrc','image/alpine/persistence.py',
                  'scripts/tls.py','scripts/install-push-runtime.py','systemd/wifi-bridge-http.service')
        names=subprocess.check_output(['git','ls-tree','-r','--name-only',commit,'--',*prefixes],cwd=ROOT,text=True).splitlines()
        files={}
        for name in names:
            path=Path(name)
            if path.is_absolute() or '..' in path.parts or '__pycache__' in path.parts:raise ValueError('Unsafe tracked build input.')
            entry=subprocess.check_output(['git','ls-tree',commit,'--',name],cwd=ROOT,text=True)
            if not entry.startswith('100644 ') and not entry.startswith('100755 '):raise ValueError('Only regular tracked source files are allowed.')
            content=subprocess.check_output(['git','show',commit+':'+name],cwd=ROOT)
            target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
            files[name]=content
    if 'image/alpine/standard/candidate.py'not in files or 'image/alpine/standard/inputs.json'not in files:
        raise ValueError('Integrate the reviewed standard recipe before building its committed snapshot.')
    inputs=json.loads(files['image/alpine/standard/inputs.json'])
    selected_version=re.search(r'__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"',files['wifi_bridge/__init__.py'].decode())[1]
    output.mkdir(mode=0o755)
    value={'format':1,'version':selected_version,'platform':'linux-alpine-armhf-standard',
           'inputs':inputs,'source_commit':commit,'working_tree_dirty':dirty,
           'source_snapshot':'verified staged artifact contract or committed tracked allowlist',
           'recipe_identity':source_identity(files),
           'hardware_validation':{'physical_boot':False,'radios':False,'phone_trust':False,'ota_rollback':False},
           'reproducibility':'Recorded input/package closure; ephemeral APKINDEX signing and timestamps prevent a byte-reproducibility claim.'}
    (output/'source.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def guard_loop(device, backing, offset, size, *, run=subprocess.check_output):
    if not re.fullmatch(r'/dev/loop[0-9]+',device):raise ValueError('Only an owned image-backed loop is allowed.')
    info=json.loads(run(['losetup','--json','--output','NAME,BACK-FILE,OFFSET,SIZELIMIT',device],text=True))['loopdevices']
    if (len(info)!=1 or info[0]['name']!=device or Path(info[0]['back-file']).resolve()!=backing.resolve()
            or int(info[0]['offset'])!=offset or int(info[0]['sizelimit'])!=size):
        raise ValueError('Loop ownership/geometry changed; refusing formatting or teardown.')


def initialize_data(path, device, backing, offset, size, *, run=subprocess.check_output):
    """Initialize only the exact mounted, owned DATA loop; runtime stays strict."""
    guard_loop(device,backing,offset,size,run=run)
    source=run(['findmnt','--noheadings','--output','SOURCE','--mountpoint',str(path)],text=True).strip()
    if source!=device or path.is_symlink():raise ValueError('DATA mount ownership changed.')
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if os.fstat(descriptor).st_uid!=os.geteuid():raise ValueError('Unexpected DATA root owner.')
        os.fchmod(descriptor,0o700);os.fsync(descriptor)
        info=os.fstat(descriptor)
        if info.st_mode&0o7777!=0o700:raise ValueError('DATA root is not private.')
        return {'data_root_mode':'0700','data_root_uid':info.st_uid,'data_root_gid':info.st_gid}
    finally:os.close(descriptor)


def partition_header(header, inputs):
    if len(header)!=512 or header[510:]!=b'\x55\xaa':raise ValueError('Invalid MBR.')
    for index,offset,size,types in ((0,inputs['boot_offset'],inputs['boot_bytes'],{0x0b,0x0c}),
                                  (1,inputs['data_offset'],inputs['image_bytes']-inputs['data_offset'],{0x83})):
        entry=header[446+index*16:462+index*16]
        start,length=struct.unpack_from('<II',entry,8)
        if start*512!=offset or length*512!=size or entry[4] not in types or entry[0]!=(0x80 if index==0 else 0):
            raise ValueError('Unexpected standard partition geometry.')


def cpio_files(path):
    # Bounded newc reader: inspect initramfs bytes without extracting symlinks/paths.
    with gzip.open(path,'rb') as stream:data=stream.read(128*1024*1024+1)
    if len(data)>128*1024*1024:raise ValueError('Oversized initramfs.')
    result={};position=0;complete=False
    while position+110<=len(data):
        head=data[position:position+110]
        if head[:6]not in (b'070701',b'070702'):raise ValueError('Unsupported initramfs CPIO format.')
        size=int(head[54:62],16);namesize=int(head[94:102],16)
        if not 1<=namesize<=4096:raise ValueError('Invalid CPIO name.')
        name=data[position+110:position+110+namesize-1].decode()
        position=(position+110+namesize+3)&~3
        if name=='TRAILER!!!':
            complete=True;break
        if size>len(data)-position:raise ValueError('Truncated CPIO entry.')
        if name.startswith('./'):name=name[2:]
        if name.startswith('/') or '..' in Path(name).parts or name in result:raise ValueError('Unsafe/duplicate CPIO name.')
        result[name]=data[position:position+size];position=(position+size+3)&~3
    if not result or not complete:raise ValueError('Empty/truncated initramfs.')
    return result


def overlay(boot):
    candidates=list(boot.glob('*.apkovl.tar.gz'))
    if len(candidates)!=1:raise ValueError('Expected one appliance overlay.')
    with tarfile.open(candidates[0]) as archive:
        members={}
        for member in archive.getmembers():
            name=member.name.removeprefix('./')
            if name in members or name.startswith('/') or '..'in Path(name).parts or any(part.startswith('._') for part in Path(name).parts):
                raise ValueError('Unsafe/duplicate or host metadata overlay entry.')
            members[name]=member
        if members['.'].mode!=0o755:raise ValueError('Overlay root must be traversable.')
        for name in ('etc/wifi-bridge','etc/NetworkManager/system-connections','var/lib/wifi-bridge'):
            if members[name].mode!=0o700:raise ValueError('Private overlay directory mode changed.')
        if members['etc/wifi-bridge/bootstrap.json'].mode!=0o600:raise ValueError('Private bootstrap mode changed.')
        if any('ssh_host_' in name or name.endswith(('.rsa','.key')) or '/identity/' in name or '/tls/' in name or '__pycache__' in name for name in members):
            raise ValueError('Precloned identity/private key or host bytecode in overlay.')
        def read(name):return archive.extractfile(members[name]).read()
        if json.loads(read('etc/wifi-bridge/bootstrap.json'))!={'password':'12345678'}:raise ValueError('Unexpected owner credentials.')
        world=read('etc/apk/world').decode().splitlines()
        files={name.removeprefix('opt/wifi-bridge/'):read(name) for name,m in members.items()
               if name.startswith('opt/wifi-bridge/wifi_bridge/') and m.isfile()}
        identity=source_identity(files)
        if files.get('wifi_bridge/build.py')!=f'BUILD_ID = "{identity}"\n'.encode():raise ValueError('Unstamped/mismatched application identity.')
        for name in ('updater-version','tls-helper-version'):
            if read('etc/wifi-bridge/'+name).strip()!=b'2':raise ValueError('Unsupported OS helper version.')
        link=members.get('usr/sbin/rfkill')
        if not link or not link.issym() or link.linkname!='/bin/busybox':raise ValueError('Explicit rfkill BusyBox link missing.')
        for name in ('sshd','wifi-bridge','astro-bridge-provision','crond','chronyd','avahi-daemon','networkmanager'):
            if 'etc/runlevels/default/'+name not in members:raise ValueError('Missing boot service: '+name)
        if b'rc_need="astro-bridge-provision"'not in read('etc/conf.d/sshd'):raise ValueError('SSH provision ordering missing.')
        return world,identity


def root_file(root, name):
    """Resolve package symlinks inside its target root, never on the builder OS."""
    pending=list(PurePosixPath(name).parts);resolved=[];links=0
    while pending:
        part=pending.pop(0)
        if part in ('/','.'):continue
        if part=='..':
            if not resolved:raise ValueError('Package symlink escapes root.')
            resolved.pop();continue
        entry=root.joinpath(*resolved,part)
        if entry.is_symlink():
            links+=1
            if links>40:raise ValueError('Package symlink loop.')
            target=PurePosixPath(os.readlink(entry))
            if target.is_absolute():resolved=[]
            pending=list(target.parts)+pending
        else:resolved.append(part)
    return root.joinpath(*resolved)


def firmware_payload(root, name, kernel_config):
    for selected in (name,name+'.zst'):
        path=root_file(root,selected)
        if not path.is_file():continue
        size=path.stat().st_size
        if not 4<=size<=4*1024*1024:raise ValueError('Invalid actual firmware payload size: '+selected)
        with path.open('rb') as stream:magic=stream.read(4)
        compressed=selected.endswith('.zst')
        if compressed:
            lines=kernel_config.splitlines()
            if 'CONFIG_FW_LOADER_COMPRESS=y'not in lines or 'CONFIG_FW_LOADER_COMPRESS_ZSTD=y'not in lines:
                raise ValueError('Actual kernel cannot load Zstandard firmware.')
            if magic!=b'\x28\xb5\x2f\xfd':raise ValueError('Invalid Zstandard firmware frame: '+selected)
        elif magic==b'\x28\xb5\x2f\xfd':raise ValueError('Compressed firmware has no loader suffix.')
        return {'selected':selected,'resolved':path.relative_to(root).as_posix(),
                'compression':'zstd'if compressed else 'none','bytes':size,'sha256':digest(path)}
    raise ValueError('Missing actual package payload: '+name)


def inspect_boot(boot, package_root, packages, module_listing, key_files):
    world,identity=overlay(boot)
    required=('bootcode.bin','start.elf','fixup.dat','config.txt','cmdline.txt','boot/vmlinuz-rpi',
              'boot/initramfs-rpi','boot/modloop-rpi','bcm2835-rpi-zero-w.dtb','.alpine-release')
    for name in required:
        if not (boot/name).is_file():raise ValueError('Missing firmware boot file: '+name)
    config=(boot/'config.txt').read_text()
    if not all(value in config.splitlines() for value in ('kernel=boot/vmlinuz-rpi','initramfs boot/initramfs-rpi','arm_64bit=0','include usercfg.txt')):raise ValueError('Unexpected original Pi Zero firmware configuration.')
    initramfs=cpio_files(boot/'boot/initramfs-rpi')
    if not key_files:raise ValueError('No APK trust evidence.')
    for key in key_files:
        if initramfs.get('etc/apk/keys/'+key.name)!=key.read_bytes():raise ValueError('APK trust key missing from actual initramfs: '+key.name)
    configurations=list((boot/'boot').glob('config-*-rpi'))
    if len(configurations)!=1:raise ValueError('Expected one recorded rpi kernel configuration.')
    kernel=configurations[0].name.removeprefix('config-')
    for name in ('brcmfmac.ko','rtw88_8821cu.ko'):
        if not any(kernel+'/'in line and name in line for line in module_listing.splitlines()):raise ValueError('Missing matching Wi-Fi module: '+name)
    for name in ('usr/bin/nmcli','usr/bin/chronyc','usr/bin/openssl','sbin/fsck.ext4','bin/busybox'):
        if not root_file(package_root,name).is_file():raise ValueError('Missing actual package payload: '+name)
    kernel_config=configurations[0].read_text()
    firmware={name:firmware_payload(package_root,'lib/firmware/'+name,kernel_config) for name in (
        'brcm/brcmfmac43430-sdio.bin',
        'brcm/brcmfmac43430-sdio.raspberrypi,model-zero-w.bin',
        'brcm/brcmfmac43430-sdio.raspberrypi,model-zero-w.txt',
        'brcm/brcmfmac43430-sdio.raspberrypi,model-zero-w.clm_blob',
        'rtw88/rtw8821c_fw.bin')}
    # apk --no-scripts closure extraction does not run BusyBox's link post-install.
    if b'rfkill\0'not in root_file(package_root,'bin/busybox').read_bytes():raise ValueError('Target BusyBox lacks the rfkill applet.')
    if '/etc/periodic/daily'not in root_file(package_root,'etc/crontabs/root').read_text():raise ValueError('Stock daily periodic scheduling missing.')
    names={p['name'] for p in packages}
    if not set(world)<=names:raise ValueError('World dependency closure is incomplete.')
    for p in packages:
        if p.get('arch')not in ('armhf','noarch') or not p.get('version') or not p.get('license'):raise ValueError('Incomplete package provenance.')
    packages=[{key:p[key] for key in ('name','version','arch','license','origin','url','file-size') if key in p} for p in packages]
    return {'app_build_id':identity,'world':world,'kernel_release':kernel,'alpine_release':(boot/'.alpine-release').read_text().strip(),
            'boot_files':{name:digest(boot/name) for name in (*required,configurations[0].relative_to(boot).as_posix())},
            'firmware':firmware,'packages':packages,
            'trust_keys':{p.name:digest(p) for p in key_files},
            'checks':{'world_packages_present':True,'initramfs_public_keys':True,'modules_firmware':True,
                      'rootmode_helpers_services':True,'rfkill_busybox_applet':True},
            'execution_boundary':'No target ARM binaries executed; no firmware boot or physical radio/trust acceptance.'}


def finish(image, output):
    value=json.loads((output/'source.json').read_text());inputs=value['inputs']
    filesystems=json.loads((output/'filesystems.json').read_text())
    if filesystems!={'data_root_mode':'0700','data_root_uid':0,'data_root_gid':0}:raise ValueError('Private root-owned DATA initialization evidence missing.')
    with image.open('rb') as stream:partition_header(stream.read(512),inputs)
    if image.stat().st_size!=inputs['image_bytes']:raise ValueError('Raw size mismatch.')
    name=f'astro-bridge-{value["version"]}-alpine-armhf-standard.img.xz'
    target=output/name
    value.update(json.loads((output/'assembled.json').read_text()),
                 filesystems=filesystems,
                 container=json.loads((output/'container.json').read_text()),
                 builder_packages=json.loads((output/'builder-packages.json').read_text()),
                 package_lock=json.loads((output/'package-lock.json').read_text()),
                 kernel_packages=json.loads((output/'kernel-packages.json').read_text()),
                 mkimage_patched_sha256=(output/'mkimage-patched.sha256').read_text().split()[0],
                 asset=name)
    with image.open('rb') as source,lzma.open(target.with_suffix('.partial'),'wb',preset=3) as dest:
        while data:=source.read(1024*1024):dest.write(data)
    target.with_suffix('.partial').replace(target)
    value.update(sha256=digest(target),raw_sha256=digest(image),raw_bytes=image.stat().st_size)
    (output/'alpine-standard-manifest.json').write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
    (output/'ALPINE-STANDARD-SHA256SUMS').write_text(value['sha256']+'  '+name+'\n')
    verify_image(target, value)


def verify_image(image, value):
    with image.open('rb') as stream:magic=stream.read(6)
    if not image.name.endswith('.img.xz') or magic!=b'\xfd7zXZ\x00':raise ValueError('Expected actual .img.xz data.')
    if digest(image)!=value['sha256']:raise ValueError('Compressed checksum mismatch.')
    raw=hashlib.sha256();size=0
    with lzma.open(image,'rb') as stream:
        header=stream.read(512);partition_header(header,value['inputs']);raw.update(header);size+=len(header)
        while data:=stream.read(1024*1024):
            raw.update(data);size+=len(data)
            if size>value['inputs']['image_bytes']:raise ValueError('Oversized raw candidate.')
    if size!=value['raw_bytes'] or raw.hexdigest()!=value['raw_sha256']:raise ValueError('Raw candidate identity mismatch.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('stamp').add_argument('path',type=Path)
    prep=sub.add_parser('prepare');prep.add_argument('path',type=Path);prep.add_argument('snapshot',type=Path)
    sub.add_parser('version');sub.add_parser('stage')
    guard=sub.add_parser('guard-loop');guard.add_argument('device');guard.add_argument('backing',type=Path);guard.add_argument('offset',type=int);guard.add_argument('size',type=int)
    data=sub.add_parser('init-data');data.add_argument('path',type=Path);data.add_argument('device');data.add_argument('backing',type=Path);data.add_argument('offset',type=int);data.add_argument('size',type=int);data.add_argument('output',type=Path)
    finish_parser=sub.add_parser('finish');finish_parser.add_argument('image',type=Path);finish_parser.add_argument('output',type=Path)
    verify=sub.add_parser('verify-image');verify.add_argument('image',type=Path);verify.add_argument('manifest',type=Path)
    inspect=sub.add_parser('inspect');inspect.add_argument('boot',type=Path);inspect.add_argument('package_root',type=Path);inspect.add_argument('packages',type=Path);inspect.add_argument('listing',type=Path);inspect.add_argument('keys',type=Path);inspect.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.action=='version':print(version())
    elif args.action=='stage':print(create_snapshot())
    elif args.action=='prepare':prepare(args.path,args.snapshot)
    elif args.action=='stamp':print(stamp(args.path))
    elif args.action=='guard-loop':guard_loop(args.device,args.backing,args.offset,args.size)
    elif args.action=='init-data':args.output.write_text(json.dumps(initialize_data(args.path,args.device,args.backing,args.offset,args.size),indent=2)+'\n')
    elif args.action=='finish':finish(args.image,args.output)
    elif args.action=='verify-image':verify_image(args.image,json.loads(args.manifest.read_text()))
    else:args.output.write_text(json.dumps(inspect_boot(args.boot,args.package_root,json.loads(args.packages.read_text()),args.listing.read_text(),list(args.keys.glob('*.pub'))),indent=2)+'\n')
