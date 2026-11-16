#!/usr/bin/python3
"""Fail closed unless the persistent state and onboard radio are ours."""
import json, os, re, stat, subprocess, sys, time
from pathlib import Path


def owner_shape(config, mac):
    # Settings may reassign onboard Wi-Fi or select a second AP radio.
    # The onboard hardware itself is independently verified in main().
    role = config.get('interface_roles', {}).get(mac)
    if role not in ('auto', 'home', 'hotspot', 'telescope'):
        return False
    return True


def nm_ready(config, mac=None):
    result = subprocess.run(['nmcli','-g','GENERAL.STATE','device','show','wlan0'], capture_output=True, text=True, timeout=3)
    if result.returncode != 0:
        return False
    state = result.stdout.strip().split(' ', 1)[0]
    if state == '30':
        return True
    home = config.get('home', {})
    if (state != '100' or (mac is not None and home.get('mac') != mac) or
            config.get('interface_roles', {}).get(home.get('mac')) not in ('auto', 'home')):
        return False
    connection = subprocess.run(['nmcli','-g','GENERAL.CONNECTION','device','show','wlan0'], capture_output=True, text=True, timeout=3)
    if connection.returncode != 0 or connection.stdout.strip() != 'wifi-bridge-home':
        return False
    link = subprocess.run(['iw', 'dev', 'wlan0', 'link'], capture_output=True, text=True, timeout=3)
    ssid = config.get('home', {}).get('ssid')
    return (link.returncode == 0 and isinstance(ssid, str) and
            any(line.lstrip() == 'SSID: ' + ssid for line in link.stdout.splitlines()) and
            re.search(r'^Connected to [a-fA-F0-9:]{17}\b', link.stdout, re.MULTILINE) is not None)


def main():
    s=Path('/dev/mmcblk0p2').stat(); assert stat.S_ISBLK(s.st_mode)
    entries=[x.split() for x in Path('/proc/self/mountinfo').read_text().splitlines() if x.split()[4]=='/var/lib/wifi-bridge']
    assert len(entries)==1
    f=entries[0]; i=f.index('-'); assert f[2]==f'{os.major(s.st_rdev)}:{os.minor(s.st_rdev)}' and f[3]=='/state' and f[i+1]=='ext4' and 'rw' in f[5].split(',')
    state=Path('/var/lib/wifi-bridge'); assert state.stat().st_uid==0 and not state.stat().st_mode & 0o077
    radio=Path('/sys/class/net/wlan0'); assert (radio/'device/driver').resolve(strict=True).name=='brcmfmac' and (radio/'device/subsystem').resolve(strict=True).name=='sdio'
    config=json.loads((state/'config.json').read_text()); mac=(radio/'address').read_text().strip().lower()
    assert re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}',mac) and owner_shape(config, mac)
    assert (state/'identity-ready').is_file() and (state/'app/wifi_bridge/__main__.py').is_file()
    end=time.monotonic()+30
    while time.monotonic()<end:
        if nm_ready(config, mac): break
        time.sleep(1)
    else: raise ValueError('onboard Wi-Fi not available in NetworkManager')


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print('Astro Bridge guard:',e,file=sys.stderr); sys.exit(1)
