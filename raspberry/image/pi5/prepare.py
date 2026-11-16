#!/usr/bin/env python3
"""Prepare pristine Pi 5 boot overlay and p2 contents from pinned inputs."""
import argparse, hashlib, io, os, pathlib, shutil, subprocess, tarfile
HERE=pathlib.Path(__file__).resolve().parent
APP_SHA='7c56eb09efaa82ff64f90a52085c9fcc3a3e30a04b83f310d22255f80bd9a810'
MANIFEST_SHA='fb5de661ac16bc0a3760f935360126afa39f9afb7990556f1c2ac4d824b332bb'
BUILD_ID='c440e75a052edd8d0139da8c5d16035f23808deb5fe4af04ed80fccaf051c52c'
OWNER_TOOLS={'install-zwo.py':'9175699cd49e81d32d625e17b14a30c0f57d23b926291b7f789711dc1bc6447d','asi_archive.py':'d71353c4e0596400d3bd96f9221fe0723068677aabce7dca4ccc357476a7043e'}
AVAHI_INITIAL_CONFIG='[server]\nhost-name=astro-bridge\nallow-interfaces=lo\nuse-ipv6=no\n[wide-area]\nenable-wide-area=no\n[publish]\npublish-workstation=no\n[reflector]\nenable-reflector=no\n'
DISPLAY_PACKAGES={
 'libpng-1.6.59-r0.apk':'bccd127f8c42bc5c21c018b6b1dc977128f3bad64c3412e541c09e96e8b227a4',
 'libqrencode-4.1.1-r3.apk':'455a7d471300d581613a7ddc77778779fc61f3a55053c733cad741aafa75b884',
 'libqrencode-tools-4.1.1-r3.apk':'42e12739c1e0a1bbdfe809ab1cf340984f566d6fbca6b322a1196a7c696adeb8',
}
CAMERA_PACKAGES={
 'gcompat-1.1.0-r4.apk':'8e6b58c16e3cceaf714d71d3906f2b6b42fa299027519c5f2ec46960ff2776e2',
 'libucontext-1.5.1-r0.apk':'bb8ff2783ebd1488baebd403b048ea62b8d64f166ea402584f01f1b1a6c89d4f',
 'musl-obstack-1.2.3-r2.apk':'08ac3018b48849dce6737f2eca8d925fb82c6c7cd416e70f8f5975ac97e725cc',
}
WORLD='alpine-base\niw\nopenssh\nopenssl\nwpa_supplicant\n'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser()
 for n in ('app','apks','display-apks','camera-apks','owner-tools','output','project'): p.add_argument('--'+n,required=True,type=pathlib.Path)
 p.add_argument('--splash-binary',type=pathlib.Path)
 # Public builds pass the digests of their own pinned inputs (see build-public.py);
 # the defaults reproduce the historical private candidate.
 p.add_argument('--public',action='store_true',help='Public image: inputs pinned by the caller, owner tools from this tree')
 p.add_argument('--app-sha256',default=APP_SHA); p.add_argument('--build-id',default=BUILD_ID)
 p.add_argument('--manifest-sha256',default=MANIFEST_SHA); p.add_argument('--package-count',type=int,default=62)
 a=p.parse_args(); assert sha(a.app)==a.app_sha256 and sha(a.apks/'astro-packages.sha256')==a.manifest_sha256
 if a.output.exists(): raise SystemExit('refusing existing output')
 a.output.mkdir(parents=True); data=a.output/'data'; overlay=a.output/'overlay'; data.mkdir(); overlay.mkdir()
 state=data/'state'; state.mkdir(mode=0o700); cache=data/'cache'; cache.mkdir(mode=0o700)
 app=state/'app'; app.mkdir(mode=0o700)
 with tarfile.open(a.app) as tar:
  seen=set()
  for entry in tar:
   path=pathlib.PurePosixPath(entry.name)
   if not entry.isfile() or path.is_absolute() or '..' in path.parts or path.parts[0]!='wifi_bridge' or entry.name in seen: raise ValueError('unsafe app archive')
   seen.add(entry.name); dst=app.joinpath(*path.parts); dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(tar.extractfile(entry).read()); dst.chmod(0o644)
 assert (app/'wifi_bridge/build.py').read_text().strip()==f'BUILD_ID = "{a.build_id}"'
 manifest=(a.apks/'astro-packages.sha256').read_text(); names=set()
 for line in manifest.splitlines():
  h,name=line.split(); assert name not in names and pathlib.PurePath(name).name==name and name.endswith('.apk') and sha(a.apks/name)==h
  names.add(name); shutil.copyfile(a.apks/name,cache/name)
 assert len(names)==a.package_count
 (cache/'astro-packages.sha256').write_text(manifest)
 display_cache=cache/'display'; display_cache.mkdir(mode=0o700)
 for name,digest in DISPLAY_PACKAGES.items():
  assert sha(a.display_apks/name)==digest
  shutil.copyfile(a.display_apks/name,display_cache/name)
 camera_cache=cache/'camera'; camera_cache.mkdir(mode=0o700)
 for name,digest in CAMERA_PACKAGES.items():
  assert sha(a.camera_apks/name)==digest
  shutil.copyfile(a.camera_apks/name,camera_cache/name)
 def put(path,content,mode=0o644):
  dst=overlay/path; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(content if isinstance(content,bytes) else content.encode()); dst.chmod(mode)
 def copy(path,src,mode=0o644): put(path,src.read_bytes(),mode)
 for name,digest in OWNER_TOOLS.items():
  assert a.public or sha(a.owner_tools/name)==digest
  copy('usr/local/share/astro-bridge/zwo/'+name,a.owner_tools/name,0o755 if name=='install-zwo.py' else 0o644)
 put('etc/wifi-bridge/pi5-asi220-test','Pi 5 attached camera profile\n' if a.public else 'private Pi 5 ASI220 test\n',0o600)
 if a.public: put('etc/avahi/avahi-daemon.conf',AVAHI_INITIAL_CONFIG)
 put('etc/wifi-bridge/camera-packages.sha256',''.join(f'{digest}  {name}\n' for name,digest in CAMERA_PACKAGES.items()),0o600)
 udev=overlay/'etc/udev/rules.d'; udev.mkdir(parents=True,exist_ok=True)
 copy('etc/udev/rules.d/39-astro-realtek-switch.rules',HERE/'39-astro-realtek-switch.rules')
 put('etc/sysctl.d/80-wifi-bridge.conf','net.ipv4.ip_forward=1\n')
 (udev/'70-astro-bridge-asi.rules').symlink_to('/var/lib/wifi-bridge/camera-drivers/zwo-1.41/70-astro-bridge-asi.rules')
 put('etc/wifi-bridge/display-packages.sha256',''.join(f'{digest}  {name}\n' for name,digest in DISPLAY_PACKAGES.items()),0o600)
 put('etc/apk/world',WORLD); put('etc/wifi-bridge/base-world',WORLD,0o600)
 if a.splash_binary: copy('usr/local/sbin/astro-graphic-splash',a.splash_binary,0o755)
 copy('usr/local/sbin/astro-graphic-splash-supervisor',HERE.parent/'splash/graphic-splash-supervisor',0o755)
 copy('usr/share/astro-bridge/boot-splash.ppm',HERE.parent/'splash/boot-splash.ppm')
 put('etc/hostname','astro-bridge\n'); put('etc/hosts','127.0.0.1 localhost astro-bridge\n::1 localhost\n'); put('etc/wifi-bridge/init-system','openrc\n')
 put('etc/modules','brcmfmac\nipv6\n')
 put('etc/NetworkManager/conf.d/80-wifi-bridge.conf','[main]\nplugins=keyfile\nno-auto-default=*\n[device]\nwifi.scan-rand-mac-address=no\n[wifi]\nbackend=wpa_supplicant\n')
 copy('etc/NetworkManager/system-connections/wifi-bridge-wired.nmconnection',HERE/'wifi-bridge-wired.nmconnection',0o600)
 for service in ('astro-console-lock','astro-console-account','astro-data','astro-runtime-packages','astro-firstboot','astro-target-udev','wifi-bridge','astro-display-packages','astro-display-splash','astro-display-welcome','astro-camera-packages'): copy('etc/init.d/'+service,HERE/service,0o755)
 for service in ('wifi-bridge-ap','wifi-bridge-dhcp'): copy('etc/init.d/'+service,a.project/'image/alpine/openrc'/service,0o755)
 copy('etc/wifi-bridge/astro-display-splash-worker',HERE/'astro-display-splash-worker',0o755)
 for file in ('firstboot.py','guard.py','hotspot-release.py','display.py','console-account.py'): copy('etc/wifi-bridge/'+file,HERE/file,0o755)
 for service,pkg in (('dbus','dbus-openrc-'),('networkmanager','networkmanager-openrc-')):
  apk=next((a.apks/n for n in names if n.startswith(pkg)),None); assert apk
  content=subprocess.check_output(['tar','-xOzf',str(apk),'etc/init.d/'+service]); put('etc/init.d/'+service,content,0o755)
 put('etc/conf.d/dbus','rc_need="astro-runtime-packages"\n')
 put('etc/conf.d/networkmanager','rc_need="astro-target-udev dbus"\n')
 for level,services in {'sysinit':['devfs','dmesg','mdev','modloop','hwdrivers'],'boot':['astro-console-lock','astro-display-splash','modules','sysctl','hostname','bootmisc','localmount'],'default':['astro-display-packages','astro-display-welcome','astro-data','astro-runtime-packages','astro-camera-packages','astro-firstboot','astro-console-account','astro-target-udev','dbus','networkmanager','wifi-bridge','syslog'],'shutdown':['mount-ro','killprocs','savecache']}.items():
  d=overlay/'etc/runlevels'/level; d.mkdir(parents=True,exist_ok=True)
  for name in services: (d/name).symlink_to('/etc/init.d/'+name)
 overlayfile=a.output/'astro-bridge.apkovl.tar.gz'
 with tarfile.open(overlayfile,'w:gz',format=tarfile.PAX_FORMAT) as out:
  for item in sorted(overlay.rglob('*')):
   info=out.gettarinfo(str(item),arcname=item.relative_to(overlay).as_posix()); info.uid=info.gid=0; info.uname=info.gname='root'
   if info.isfile():
    with item.open('rb') as f: out.addfile(info,f)
   else: out.addfile(info)
 print('prepared',overlayfile,'and',data)
if __name__=='__main__': main()
