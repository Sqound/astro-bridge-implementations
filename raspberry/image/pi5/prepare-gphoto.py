#!/usr/bin/env python3
"""Pi 5 bundled camera recipe extended with signed Alpine APKs."""
import argparse, base64, binascii, hashlib, json, pathlib, shutil, struct, subprocess, tarfile
HERE=pathlib.Path(__file__).resolve().parent
AVAHI_INITIAL_CONFIG='[server]\nhost-name=astro-bridge\nallow-interfaces=lo\nuse-ipv6=no\n[wide-area]\nenable-wide-area=no\n[publish]\npublish-workstation=no\n[reflector]\nenable-reflector=no\n'

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while b:=f.read(1024*1024): h.update(b)
 return h.hexdigest()

def install_wired_profile(overlay):
 wired=overlay/'etc/NetworkManager/system-connections/wifi-bridge-wired.nmconnection'
 wired.parent.mkdir(parents=True, exist_ok=True)
 shutil.copyfile(HERE/'wifi-bridge-wired.nmconnection',wired); wired.chmod(0o600)

def install_network_boot(overlay):
 sysctl=overlay/'etc/sysctl.d/80-wifi-bridge.conf'; sysctl.parent.mkdir(parents=True,exist_ok=True)
 sysctl.write_text('net.ipv4.ip_forward=1\n'); sysctl.chmod(0o644)
 rule=overlay/'etc/udev/rules.d/39-astro-realtek-switch.rules'; rule.parent.mkdir(parents=True,exist_ok=True)
 shutil.copyfile(HERE/rule.name,rule); rule.chmod(0o644)
 service=overlay/'etc/init.d/astro-target-udev'; shutil.copyfile(HERE/'astro-target-udev',service); service.chmod(0o755)

def diagnostic_key(path):
 # Only one OpenSSH Ed25519 public key, no options, comments, or private material.
 data=path.read_bytes()
 try:
  lines=data.decode('ascii').splitlines()
  if len(lines)!=1: raise ValueError()
  fields=lines[0].split()
  if len(fields)!=2 or fields[0]!='ssh-ed25519': raise ValueError()
  blob=base64.b64decode(fields[1],validate=True)
  if base64.b64encode(blob).decode()!=fields[1]: raise ValueError()
  if len(blob)!=51 or struct.unpack('>I',blob[:4])[0]!=11 or blob[4:15]!=b'ssh-ed25519' or struct.unpack('>I',blob[15:19])[0]!=32: raise ValueError()
 except (UnicodeError,ValueError,binascii.Error,struct.error):
  raise ValueError('expected exactly one OpenSSH Ed25519 public key without comment') from None
 return 'restrict,pty ssh-ed25519 '+fields[1]+'\n'

def install_diagnostic_ssh(overlay,key):
 ssh=overlay/'etc/ssh'; ssh.mkdir(parents=True,exist_ok=True)
 (ssh/'astro-diagnostic-authorized-keys').write_text(key); (ssh/'astro-diagnostic-authorized-keys').chmod(0o600)
 shutil.copyfile(HERE/'diagnostic-sshd_config',ssh/'astro-diagnostic-sshd_config'); (ssh/'astro-diagnostic-sshd_config').chmod(0o600)
 service=overlay/'etc/init.d/astro-diagnostic-ssh'; shutil.copyfile(HERE/'astro-diagnostic-ssh',service); service.chmod(0o755)
 (overlay/'etc/runlevels/default/astro-diagnostic-ssh').symlink_to('/etc/init.d/astro-diagnostic-ssh')

def main():
 p=argparse.ArgumentParser()
 for n in ('output','app','inputs','project','splash-binary'): p.add_argument('--'+n,required=True,type=pathlib.Path)
 p.add_argument('--app-sha256',required=True); p.add_argument('--build-id',required=True)
 p.add_argument('--diagnostic-ssh-public-key',type=pathlib.Path)
 a=p.parse_args()
 key=diagnostic_key(a.diagnostic_ssh_public_key) if a.diagnostic_ssh_public_key else None
 if len(a.app_sha256)!=64 or sha(a.app)!=a.app_sha256: raise ValueError('app digest mismatch')
 lock=json.loads((HERE/'gphoto-packages.lock.json').read_text())
 for group,items in lock.items():
  for name,meta in items.items():
   if sha(a.inputs/group/name)!=meta['sha256']: raise ValueError('APK digest mismatch: '+name)
 subprocess.run(['python3',str(HERE/'prepare-bundle.py'),str(a.output)],check=True)
 dest=a.output/'prepared'; appdir=dest/'data/state/app/wifi_bridge'; shutil.rmtree(appdir)
 with tarfile.open(a.app) as archive:
  seen=set()
  for entry in archive:
   path=pathlib.PurePosixPath(entry.name)
   if not entry.isfile() or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='wifi_bridge' or entry.name in seen: raise ValueError('unsafe app archive')
   seen.add(entry.name); target=dest/'data/state/app'/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(archive.extractfile(entry).read()); target.chmod(0o644)
 if (appdir/'build.py').read_text().strip()!=f'BUILD_ID = "{a.build_id}"': raise ValueError('build ID mismatch')
 cache=dest/'data/cache'; gphoto=cache/'gphoto'; gphoto.mkdir(mode=0o700)
 for name in lock['selected']: shutil.copyfile(a.inputs/'selected'/name,gphoto/name)
 runtime_manifest=cache/'astro-packages.sha256'
 with runtime_manifest.open('a') as f:
  for name,meta in lock['runtime-extra'].items():
   shutil.copyfile(a.inputs/'runtime-extra'/name,cache/name); f.write(f"{meta['sha256']}  {name}\n")
 overlay=dest/'overlay'; etc=overlay/'etc/wifi-bridge'
 binary=a.splash_binary.read_bytes()
 if binary[:7]!=b'\x7fELF\x02\x01\x01' or int.from_bytes(binary[18:20],'little')!=183: raise ValueError('splash binary must be aarch64 ELF')
 for src,relative,mode in ((a.splash_binary,'usr/local/sbin/astro-graphic-splash',0o755),(HERE.parent/'splash/graphic-splash-supervisor','usr/local/sbin/astro-graphic-splash-supervisor',0o755),(HERE.parent/'splash/boot-splash.ppm','usr/share/astro-bridge/boot-splash.ppm',0o644)):
  target=overlay/relative; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,target); target.chmod(mode)
 install_wired_profile(overlay)
 install_network_boot(overlay)
 if key is not None: install_diagnostic_ssh(overlay,key)
 avahi=overlay/'etc/avahi'; avahi.mkdir(parents=True, exist_ok=True)
 (avahi/'avahi-daemon.conf').write_text(AVAHI_INITIAL_CONFIG)
 (avahi/'avahi-daemon.conf').chmod(0o644)
 for name in ('firstboot.py', 'display.py', 'guard.py', 'hotspot-release.py', 'astro-display-splash-worker'):
  shutil.copyfile(HERE/name, etc/name); (etc/name).chmod(0o755)
 (etc/'pi5-indi-gphoto-test').write_text('private Pi 5 INDI gPhoto test\n'); (etc/'pi5-indi-gphoto-test').chmod(0o600)
 (etc/'gphoto-packages.sha256').write_text(''.join(f"{meta['sha256']}  {name}\n" for name,meta in lock['selected'].items())); (etc/'gphoto-packages.sha256').chmod(0o600)
 for name in ('astro-display-packages','astro-display-splash','astro-display-welcome','astro-camera-packages','astro-gphoto-packages'):
  dst=overlay/'etc/init.d'/name; shutil.copyfile(HERE/name,dst); dst.chmod(0o755)
 (overlay/'etc/runlevels/default/astro-gphoto-packages').symlink_to('/etc/init.d/astro-gphoto-packages')
 with tarfile.open(dest/'astro-bridge.apkovl.tar.gz','w:gz',format=tarfile.PAX_FORMAT) as out:
  for item in sorted(overlay.rglob('*')):
   info=out.gettarinfo(str(item),arcname=item.relative_to(overlay).as_posix()); info.uid=info.gid=0; info.uname=info.gname='root'
   if info.isfile():
    with item.open('rb') as f: out.addfile(info,f)
   else: out.addfile(info)
 print('prepared',dest)
if __name__=='__main__': main()
