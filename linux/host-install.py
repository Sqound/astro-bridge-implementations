#!/usr/bin/env python3
"""Prepare a dedicated Debian/Ubuntu Linux host; inspect the plan before --apply."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PACKAGES=['network-manager','hostapd','dnsmasq-base','avahi-daemon','nftables','conntrack','iw','rfkill','iproute2','openssl','chrony','python3','python3-venv']


def install_file(source,target,mode=0o644):
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);target.chmod(mode)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--mode',choices=('docker','native'),default='docker');p.add_argument('--wheel',type=Path);p.add_argument('--allow-host',action='append',default=[]);args=p.parse_args()
    print('Install host packages: '+', '.join(PACKAGES))
    if args.mode=='native':print('Build pinned Netfilter tools and libraries in the app prefix; install compiler/development prerequisites.')
    print('Create app-owned AP/DHCP units, private state and bootstrap; enable IPv4 forwarding.')
    print('Existing NetworkManager profiles and host mDNS identity are preserved.')
    if not args.apply:print('Review the plan, then run again with --apply on a dedicated Linux host.');return
    if sys.platform!='linux' or os.geteuid()!=0 or not Path('/run/systemd/system').is_dir() or not shutil.which('apt-get'):p.error('Root on a dedicated Debian/Ubuntu systemd host is required')
    if args.mode=='native' and args.wheel is None:p.error('--wheel is required for native mode')
    if args.wheel:
        import importlib.util
        spec=importlib.util.spec_from_file_location('core',ROOT/'tools/core-artifact.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core);core.verified(args.wheel)
    state=Path('/var/lib/wifi-bridge');state.mkdir(mode=0o700,parents=True,exist_ok=True)
    if state.is_symlink() or state.stat().st_mode & 0o077:p.error('Private state must be a root-owned mode 0700 directory')
    subprocess.run(['apt-get','update'],check=True)
    build_packages=['build-essential','pkg-config','libmnl-dev','libnetfilter-conntrack-dev','libnfnetlink-dev','libtirpc-dev','bison','flex'] if args.mode=='native' else []
    subprocess.run(['apt-get','install','-y','--no-install-recommends',*PACKAGES,*build_packages],check=True)
    if args.mode=='native':
        import re
        output=subprocess.check_output(['nft','--version'],text=True)
        match=re.search(r'v([0-9]+)\.([0-9]+)\.([0-9]+)',output)
        if not match or tuple(map(int,match.groups()))<(1,1,3):p.error('Native mode requires nftables >=1.1.3; use the Docker Linux integration on this host.')
    config=Path('/etc/wifi-bridge');config.mkdir(mode=0o700,exist_ok=True)
    bootstrap=config/'bootstrap.json'
    if not bootstrap.exists():
        fd=os.open(bootstrap,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:json.dump({'password':secrets.token_urlsafe(12)},stream)
    for name in ('wifi-bridge-ap.service','wifi-bridge-dhcp.service'):
        content=(ROOT/'raspberry/systemd'/name).read_text().replace(' wifi-bridge.service','').replace('PartOf=wifi-bridge.service\n','')
        target=Path('/etc/systemd/system')/name;target.write_text(content);target.chmod(0o644)
    Path('/etc/sysctl.d/80-astro-bridge.conf').write_text('net.ipv4.ip_forward=1\n')
    subprocess.run(['sysctl','-w','net.ipv4.ip_forward=1'],check=True)
    (config/'tls-helper-version').write_text('2\n')
    if args.mode=='native':
        import venv
        prefix=Path('/opt/astro-bridge-1.0.1')
        if prefix.exists():p.error('Installation prefix already exists; stage an upgrade separately')
        subprocess.run([sys.executable,str(ROOT/'linux/build-conntrack.py'),'--prefix',str(prefix)],check=True)
        venv.EnvBuilder(with_pip=True,system_site_packages=False).create(prefix)
        subprocess.run([str(prefix/'bin/python'),'-m','pip','install','--no-index','--no-deps',str(args.wheel.resolve())],check=True)
        subprocess.run([str(prefix/'bin/pip'),'install','py-vapid==1.9.6','http-ece==1.2.1','cryptography>=46,<51'],check=True)
        install_file(ROOT/'linux/tls.py',Path('/usr/libexec/astro-bridge-tls'),0o755)
        # systemd quoting is fixed; only validated hostnames enter the argv list.
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host) for host in args.allow_host):p.error('Invalid allowed hostname')
        hosts=''.join(' --allow-host '+host for host in ['localhost','127.0.0.1',*args.allow_host])
        unit='[Unit]\nDescription=Astro Bridge native\nAfter=NetworkManager.service avahi-daemon.service\nWants=NetworkManager.service avahi-daemon.service\n[Service]\nEnvironment=ASTRO_BRIDGE_PLATFORM=native\nEnvironment=ASTRO_BRIDGE_CONNTRACK=/opt/astro-bridge-1.0.1/sbin/conntrack\nUMask=0077\nExecStart='+str(prefix/'bin/python')+' -m wifi_bridge'+hosts+'\nExecStopPost=-/bin/systemctl stop wifi-bridge-ap wifi-bridge-dhcp\nRestart=on-failure\nTimeoutStopSec=60\n[Install]\nWantedBy=multi-user.target\n'
        Path('/etc/systemd/system/wifi-bridge.service').write_text(unit)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','NetworkManager','avahi-daemon'],check=True)
    if args.mode=='native':subprocess.run(['systemctl','enable','--now','wifi-bridge'],check=True)
    print('Prepared. Read the initial private password with: sudo cat /etc/wifi-bridge/bootstrap.json')
    print('Use the host IP or hotspot IP. Configure a local DNS/mDNS name separately if desired.')
    if args.mode=='docker':print('Build the linux target, then docker compose -f docker/compose.linux.yaml up -d')


if __name__=='__main__':main()
