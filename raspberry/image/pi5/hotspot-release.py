#!/usr/bin/python3
"""Release only the recorded, bridge-owned stopped AP."""
import json
from pathlib import Path
import re
import subprocess
import sys

STATE=Path('/var/lib/wifi-bridge')

def call(args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=8)

def release():
    marker=STATE/'hotspot.json'
    if not marker.exists(): return
    record=json.loads(marker.read_text())
    name=record['interface']
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name):
        raise ValueError('invalid hotspot interface')
    # Never discard the record while either service could still own an AP.
    for unit in ('wifi-bridge-ap','wifi-bridge-dhcp'):
        if call(['rc-service',unit,'status'],check=False).returncode != 3:
            raise ValueError('hotspot unit not confirmed stopped')
    radio=Path('/sys/class/net')/name
    recorded_mac=record.get('mac')
    if recorded_mac is not None and (not isinstance(recorded_mac, str) or
            not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', recorded_mac)):
        raise ValueError('invalid recorded radio MAC')
    # An absent radio, or a name reused for a different radio, must never be
    # mutated. Old USB markers lack a durable identity and are equally ambiguous.
    if not radio.exists():
        marker.unlink(); return
    actual=(radio/'address').read_text().strip().lower()
    if not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', actual): raise ValueError('invalid radio MAC')
    if recorded_mac and recorded_mac != actual:
        marker.unlink(); return
    onboard = (name == 'wlan0' and (radio/'device/driver').resolve(strict=True).name == 'brcmfmac' and
               (radio/'device/subsystem').resolve(strict=True).name == 'sdio')
    if not recorded_mac and not onboard:
        marker.unlink(); return
    if not onboard:
        hostapd = dict(line.split('=', 1) for line in (STATE/'hostapd.conf').read_text().splitlines() if '=' in line)
        if hostapd.get('interface') != name: raise ValueError('invalid hotspot ownership')
    addresses=call(['ip','-o','-4','address','show','dev',name]).stdout
    if re.search(r'\binet\s+192\.168\.77\.1/24(?:\s|$)',addresses):
        call(['ip','address','del','192.168.77.1/24','dev',name])
    call(['nmcli','device','set',name,'managed','yes'])
    marker.unlink()

if __name__=='__main__':
    try: release()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Astro Bridge hotspot release:',error,file=sys.stderr); sys.exit(1)
