#!/usr/bin/python3
"""Private local VT display. Credentials only reach qrencode's stdin and tty7."""
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import signal
import struct
import subprocess
import sys
import termios
import time
import urllib.request
from pathlib import Path

STATE = Path('/var/lib/wifi-bridge')
TTY = Path('/dev/tty7')
READY = Path('/run/astro-display-ready')
OWNED = Path('/run/astro-display-owned')
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def current_vt():
    return Path('/sys/class/tty/tty0/active').read_text().strip()


def console():
    if current_vt() == 'tty7':
        with open('/dev/tty0', 'wb', buffering=0) as vt:
            fcntl.ioctl(vt, 0x5606, 1)  # VT_ACTIVATE


def command(*args):
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, timeout=4).decode()


def wifi_escape(value):
    return re.sub(r'([\\;,:\"])', r'\\\1', value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credentials():
    # Only a configured, currently applied AP is eligible. hostapd stores the
    # derived PSK, so compare it to the effective password without exposing it.
    config_bytes = (STATE / 'config.json').read_bytes()
    config = json.loads(config_bytes)
    hotspot_config = config.get('hotspot', {})
    if hotspot_config.get('enabled') is not True and not (
            hotspot_config.get('enabled') is False and not hotspot_config.get('mac') and
            config.get('connection_mode') in ('private', 'hotspot_shared') and config.get('home', {}).get('mac')):
        raise ValueError('hotspot disabled')
    bootstrap = json.loads((STATE / 'bootstrap.json').read_bytes())
    password = config.get('hotspot_password', bootstrap['password'])
    hostapd = dict(line.split('=', 1) for line in (STATE / 'hostapd.conf').read_text().splitlines() if '=' in line)
    interface = json.loads((STATE / 'hotspot.json').read_text())['interface']
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface) or hostapd['interface'] != interface:
        raise ValueError('interface mismatch')
    ssid = bytes.fromhex(hostapd['ssid2']).decode('utf-8')
    sys.path.insert(0, str(STATE / 'app'))
    from wifi_bridge.config import hotspot_ssid
    mac = Path('/sys/class/net', interface, 'address').read_text().strip().lower()
    if not re.fullmatch(r'(?:[a-f0-9]{2}:){5}[a-f0-9]{2}', mac):
        raise ValueError('invalid radio MAC')
    hotspot = config.get('hotspot', {})
    role = config.get('interface_roles', {}).get(mac)
    configured = (hotspot.get('enabled') is True and role in (None, 'auto', 'hotspot') and
                  (hotspot.get('mac') == mac or config.get('connection_mode') == 'hotspot_shared' and not hotspot.get('mac')) and
                  (config.get('home', {}).get('mac') != mac or role == 'auto'))
    recovery = (hotspot.get('enabled') is False and not hotspot.get('mac') and
                config.get('connection_mode') in ('private', 'hotspot_shared') and role == 'auto' and
                config.get('home', {}).get('mac') == mac)
    if (not (configured or recovery) or ssid != hotspot_ssid(config, mac)):
        raise ValueError('configuration mismatch')
    iw = command('/usr/sbin/iw', 'dev', interface, 'info')
    if not re.search(r'^\s*type AP\s*$', iw, re.MULTILINE):
        raise ValueError('radio is not an AP')
    status = dict(line.split('=', 1) for line in command('/usr/bin/hostapd_cli', '-p', '/run/wifi-bridge-hostapd', '-i', interface, 'status').splitlines() if '=' in line)
    if status.get('state') != 'ENABLED' or status.get('ssid[0]') != ssid:
        raise ValueError('hostapd not active')
    psk = hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()
    if psk != hostapd['wpa_psk']:
        raise ValueError('password not applied')
    if command('/sbin/rc-service', 'wifi-bridge-ap', 'status').find('started') < 0 or command('/sbin/rc-service', 'wifi-bridge-dhcp', 'status').find('started') < 0:
        raise ValueError('AP services unavailable')
    if '192.168.77.1/24' not in command('/sbin/ip', '-4', '-o', 'address', 'show', 'dev', interface).split():
        raise ValueError('AP address unavailable')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open('http://192.168.77.1/api/v1/info', timeout=3) as response:
        data = response.read(16385)
        if len(data) > 16384:
            raise ValueError('management reply too large')
        info = json.loads(data)
        if (response.status != 200 or info.get('product') != 'astro-bridge'
                or info.get('api_version') != '1' or info.get('contract_version') != 1
                or info.get('request_path') != 'hotspot'):
            raise ValueError('management unavailable')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open('http://192.168.77.1/', timeout=3) as response:
        if response.status != 200 or not response.headers.get('Content-Type', '').startswith('text/html') or not response.read(1024):
            raise ValueError('web interface unavailable')
    if (STATE / 'config.json').read_bytes() != config_bytes:
        raise ValueError('configuration changed')
    return ssid, password


def private_access():
    """Show only a verified home ingress, never the private Wi-Fi password."""
    config_bytes = (STATE / 'config.json').read_bytes()
    config = json.loads(config_bytes)
    home = config.get('home', {})
    if config.get('connection_mode') not in ('private', 'hotspot_shared') or not home.get('ssid'):
        raise ValueError('home not configured')
    interface = 'wlan0'
    mac = Path('/sys/class/net', interface, 'address').read_text().strip().lower()
    if home.get('mac') != mac or config.get('interface_roles', {}).get(mac) not in ('auto', 'home'):
        raise ValueError('home radio mismatch')
    lines = command('/sbin/ip', '-4', '-o', 'address', 'show', 'dev', interface, 'scope', 'global').splitlines()
    for line in lines:
        fields = line.split()
        try:
            address = ipaddress.IPv4Interface(fields[fields.index('inet') + 1]).ip
            if not address.is_private or address.is_link_local or address.is_loopback:
                continue
            url = 'http://' + str(address) + '/'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(url + 'api/v1/info', timeout=3) as response:
                data = response.read(16385)
                if len(data) > 16384: continue
                info = json.loads(data)
                if (response.status != 200 or info.get('product') != 'astro-bridge' or
                        info.get('api_version') != '1' or info.get('contract_version') != 1 or
                        info.get('request_path') != 'home'):
                    continue
            with opener.open(url, timeout=3) as response:
                if response.status != 200 or not response.headers.get('Content-Type', '').startswith('text/html') or not response.read(1024):
                    continue
            bootstrap = json.loads((STATE / 'bootstrap.json').read_bytes())
            password = None if config.get('admin_hash') else bootstrap['password']
            if password is not None and (not isinstance(password, str) or not (password == '12345678' or re.fullmatch('[0-9a-f]{24}', password))):
                raise ValueError('invalid initial password')
            if (STATE / 'config.json').read_bytes() != config_bytes:
                raise ValueError('configuration changed')
            return url, password
        except (ValueError, KeyError, IndexError, OSError):
            continue
    raise ValueError('home unavailable')


def render_private(url, password, rows, cols):
    code = qr(url)
    lines = ['Astro Bridge', 'Scan to open Astro Bridge:', *code, url,
             ('Initial administrator password: ' + password if password else 'Use your configured administrator password.'),
             'Diagnostic console: Alt+F1']
    if rows <= len(lines) or cols <= max(len(ANSI.sub('', line)) for line in lines):
        raise ValueError('console too small')
    return '\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(lines) + '\x1b[0m'


def qr(payload):
    # qrencode reads the data from stdin; never expose credentials in argv.
    result = subprocess.run(['/usr/bin/qrencode', '-t', 'ANSI', '-m', '4', '-o', '-'],
                            input=payload.encode(), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, check=True)
    rows = result.stdout.decode('utf-8').splitlines()
    if not rows or any(not ANSI.sub('', row).strip(' ') == '' for row in rows):
        raise ValueError('unexpected QR output')
    return rows


def render(ssid, password, rows, cols):
    # A 64-hex Wi-Fi QR password denotes a raw PSK. The bridge applies all
    # inputs as passphrases, so encode its derived key for this special case.
    qr_password = (hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()
                   if re.fullmatch(r'[0-9a-fA-F]{64}', password) else password)
    left = qr('WIFI:T:WPA;S:' + wifi_escape(ssid) + ';P:' + wifi_escape(qr_password) + ';;')
    right = qr('http://192.168.77.1/')
    lw = max(len(ANSI.sub('', row)) for row in left)
    rw = max(len(ANSI.sub('', row)) for row in right)
    height = max(len(left), len(right))
    if cols <= lw + rw + 4 or rows < height + 4:
        raise ValueError('console too small for QR codes')
    # Leave the final terminal row unused, including on autowrapping VTs.
    # Compact mode fits a typical 720p 160x45 framebuffer console.
    compact = rows < height + 7
    labels = 'Connect to Wi-Fi'.ljust(lw + 4) + 'Open Astro Bridge'
    lines = ['Astro Bridge', labels] if compact else ['Astro Bridge', labels, '']
    for i in range(height):
        a = left[i] if i < len(left) else ''
        b = right[i] if i < len(right) else ''
        lines.append(a + '\x1b[0m' + ' ' * (lw - len(ANSI.sub('', a)) + 4) + b + '\x1b[0m')
    lines += (['Scan Wi-Fi, then web QR.  Diagnostic console: Alt+F1'] if compact else
              ['', 'Scan Wi-Fi first, then scan the web QR.', 'Diagnostic console: Alt+F1'])
    return '\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(lines) + '\x1b[0m'


def diagnostic(message):
    try:
        with open('/dev/tty1', 'w') as tty:
            tty.write('\nAstro Bridge: ' + message + '\nCheck: rc-service wifi-bridge status; tail /var/log/wifi-bridge.log\n')
    except OSError:
        pass


def clear():
    try:
        READY.unlink(missing_ok=True)
        if not OWNED.exists():
            return
        console()
        OWNED.unlink(missing_ok=True)
        with TTY.open('w') as tty:
            tty.write('\x1b[0m\x1b[?25h\x1b[2J\x1b[H')
    except OSError:
        pass


def main():
    if not OWNED.exists():
        return 0
    deadline = time.monotonic() + 120
    displayed = None
    while True:
        try:
            try:
                mode, values = 'hotspot', credentials()
            except Exception:
                url, password = private_access()
                mode, values = 'home', (url, password)
            with TTY.open('w') as tty:
                rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(tty, termios.TIOCGWINSZ, b'\0' * 8))
                identity = (mode, values, rows, cols)
                if identity != displayed:
                    screen = (render_private(*values, rows, cols) if mode == 'home'
                              else render(*values, rows, cols))
                    tty.write(screen); tty.flush()
                    displayed = identity
            READY.touch(mode=0o600)
            deadline = time.monotonic() + 120
        except Exception:
            if displayed is not None:
                READY.unlink(missing_ok=True)
                try:
                    with TTY.open('w') as tty:
                        tty.write('\x1b[0m\x1b[2J\x1b[H'); tty.flush()
                except OSError:
                    pass
                displayed = None
            if time.monotonic() >= deadline:
                diagnostic('Management connection is unavailable or timed out.')
                clear()
                return 1
        time.sleep(3)



if __name__ == '__main__':
    def terminate(signum, frame):
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        sys.exit(main())
    finally:
        clear()
