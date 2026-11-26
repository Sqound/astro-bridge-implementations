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


def hotspot_secret(config, bootstrap, ssid):
    """The applied key, and the password to show: only the public initial one.

    A personal bridge password is never on screen (it also opens SSH and the web
    interface). config.json keeps only its derived key (hotspot_key); an older
    installation not yet migrated by the bridge keeps hotspot_password, also
    never shown when it is not the initial password.
    """
    key = config.get('hotspot_key')
    if key is not None:
        if (not isinstance(key, dict) or set(key) != {'ssid', 'psk'} or key['ssid'] != ssid
                or not re.fullmatch('[0-9a-f]{64}', str(key['psk']))):
            raise ValueError('configuration mismatch')
        return key['psk'], None
    password = config.get('hotspot_password', bootstrap['password'])
    shown = password if password == bootstrap['password'] else None
    return hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex(), shown


def credentials():
    # Only a configured, currently applied AP is eligible. hostapd stores the
    # derived PSK, so compare it to the expected key without exposing anything.
    config_bytes = (STATE / 'config.json').read_bytes()
    config = json.loads(config_bytes)
    hotspot_config = config.get('hotspot', {})
    if hotspot_config.get('enabled') is not True and not (
            hotspot_config.get('enabled') is False and not hotspot_config.get('mac') and
            config.get('connection_mode') in ('private', 'hotspot_shared') and config.get('home', {}).get('mac')):
        raise ValueError('hotspot disabled')
    bootstrap = json.loads((STATE / 'bootstrap.json').read_bytes())
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
    # The bridge may run its hotspot on another free radio than the saved one
    # when that one is missing or keeps failing; the radio must still allow it.
    configured = (hotspot.get('enabled') is True and role in (None, 'auto', 'hotspot') and
                  (config.get('home', {}).get('mac') != mac or role == 'auto'))
    recovery = (hotspot.get('enabled') is False and not hotspot.get('mac') and
                config.get('connection_mode') in ('private', 'hotspot_shared') and role == 'auto' and
                config.get('home', {}).get('mac') == mac)
    key = config.get('hotspot_key')
    expected = key['ssid'] if isinstance(key, dict) and isinstance(key.get('ssid'), str) else hotspot_ssid(config, mac)
    if (not (configured or recovery) or ssid != expected):
        raise ValueError('configuration mismatch')
    psk, password = hotspot_secret(config, bootstrap, ssid)
    iw = command('/usr/sbin/iw', 'dev', interface, 'info')
    if not re.search(r'^\s*type AP\s*$', iw, re.MULTILINE):
        raise ValueError('radio is not an AP')
    status = dict(line.split('=', 1) for line in command('/usr/bin/hostapd_cli', '-p', '/run/wifi-bridge-hostapd', '-i', interface, 'status').splitlines() if '=' in line)
    if status.get('state') != 'ENABLED' or status.get('ssid[0]') != ssid:
        raise ValueError('hostapd not active')
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


def home_interfaces(config):
    """The private Wi-Fi radio when configured, then the onboard Ethernet port."""
    home = config.get('home', {})
    interfaces = []
    if config.get('connection_mode') in ('private', 'hotspot_shared') and home.get('ssid'):
        mac = Path('/sys/class/net', 'wlan0', 'address').read_text().strip().lower()
        if home.get('mac') == mac and config.get('interface_roles', {}).get(mac) in ('auto', 'home'):
            interfaces.append('wlan0')
    # The bridge reports an owned wired path as its home path; the API check
    # below accepts the address only when the bridge itself confirmed it.
    if Path('/sys/class/net', 'eth0').exists():
        interfaces.append('eth0')
    return interfaces


def private_access():
    """Show only a verified home ingress, never the private Wi-Fi password."""
    config_bytes = (STATE / 'config.json').read_bytes()
    config = json.loads(config_bytes)
    lines = []
    for interface in home_interfaces(config):
        try:
            lines += command('/sbin/ip', '-4', '-o', 'address', 'show', 'dev', interface, 'scope', 'global').splitlines()
        except (OSError, subprocess.SubprocessError):
            continue
    if not lines:
        raise ValueError('home unavailable')
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
    for margin, kind in LAYOUTS:
        code = qr(url, margin=margin, kind=kind)
        lines = ['Astro Bridge', 'Scan to open Astro Bridge:', *code, url,
                 ('Initial administrator password: ' + password if password else 'Use your configured administrator password.'),
                 'Diagnostic console: Alt+F1']
        if rows > len(lines) and cols > max(len(ANSI.sub('', line)) for line in lines):
            break
    else:
        raise ValueError('console too small')
    return '\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(lines) + '\x1b[0m'


# Block characters of qrencode's half-height output (two modules per text row).
BLOCKS = set(' \u2580\u2584\u2588')


def qr(payload, version=None, margin=4, kind='ANSI'):
    # qrencode reads the data from stdin; never expose credentials in argv.
    command = ['/usr/bin/qrencode', '-t', kind, '-m', str(margin), '-o', '-']
    if version:
        command[1:1] = ['-v', str(version)]
    result = subprocess.run(command, input=payload.encode(), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, check=True)
    rows = result.stdout.decode('utf-8').splitlines()
    allowed = {' '} if kind == 'ANSI' else BLOCKS
    if not rows or any(not set(ANSI.sub('', row)) <= allowed for row in rows):
        raise ValueError('unexpected QR output')
    return rows


def width(rows):
    return max(len(ANSI.sub('', row)) for row in rows)


def version(rows, margin, kind):
    """The QR version of a rendered code: 17 + 4 x version modules, plus the quiet zone."""
    modules = width(rows) // (2 if kind == 'ANSI' else 1) - 2 * margin
    return max(1, (modules - 17) // 4)


def pair(first, second, margin, kind):
    """Both codes at the same version, so they have the same size and the same quiet zone."""
    left, right = qr(first, margin=margin, kind=kind), qr(second, margin=margin, kind=kind)
    size = max(version(left, margin, kind), version(right, margin, kind))
    if version(left, margin, kind) != size:
        left = qr(first, version=size, margin=margin, kind=kind)
    if version(right, margin, kind) != size:
        right = qr(second, version=size, margin=margin, kind=kind)
    return left, right


# Largest first: full quiet zone, then a narrower one, then half-height blocks for small screens.
LAYOUTS = ((4, 'ANSI'), (2, 'ANSI'), (4, 'ANSIUTF8'))


def render_personal(ssid, rows, cols, note=None):
    """A personal password is never drawn: the network name and the web code only."""
    for margin, kind in LAYOUTS:
        code = qr('http://192.168.77.1/', margin=margin, kind=kind)
        lines = ['Astro Bridge', 'Wi-Fi: ' + ssid, 'Join it with your bridge password, then scan:', *code,
                 'http://192.168.77.1/', *([note] if note else []), 'Diagnostic console: Alt+F1']
        if rows > len(lines) and cols > max(len(ANSI.sub('', line)) for line in lines):
            break
    else:
        raise ValueError('console too small for QR codes')
    return '\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(lines[:max(rows - 1, 1)]) + '\x1b[0m'


def render(ssid, password, rows, cols, note=None):
    if password is None:
        return render_personal(ssid, rows, cols, note)
    # A 64-hex Wi-Fi QR password denotes a raw PSK. The bridge applies all
    # inputs as passphrases, so encode its derived key for this special case.
    qr_password = (hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()
                   if re.fullmatch(r'[0-9a-fA-F]{64}', password) else password)
    for margin, kind in LAYOUTS:
        left, right = pair('WIFI:T:WPA;S:' + wifi_escape(ssid) + ';P:' + wifi_escape(qr_password) + ';;',
                           'http://192.168.77.1/', margin, kind)
        lw, rw = width(left), width(right)
        height = max(len(left), len(right))
        if cols > lw + rw + 4 and rows >= height + 4:
            break
    else:
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
    if note:
        # The note never replaces the instruction to reach the diagnostic console.
        lines += ([(note + '  Diagnostic console: Alt+F1')[:max(cols - 1, 1)]] if compact else
                  ['', note[:max(cols - 1, 1)], 'Diagnostic console: Alt+F1'])
    else:
        lines += (['Scan Wi-Fi, then web QR.  Diagnostic console: Alt+F1'] if compact else
                  ['', 'Scan Wi-Fi first, then scan the web QR.', 'Diagnostic console: Alt+F1'])
    return '\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(lines[:max(rows - 1, 1)]) + '\x1b[0m'


CHECK = 'Check: rc-service wifi-bridge status; tail /var/log/wifi-bridge.log /var/log/wifi-bridge-ap.log'


def hotspot_interface():
    try:
        name = json.loads((STATE / 'hotspot.json').read_text())['interface']
        return name if re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name) else ''
    except (OSError, ValueError, KeyError, TypeError):
        return ''


def explain(error):
    """Plain words for the failed check, never a network name or a password."""
    text = str(error)
    radio = hotspot_interface()
    if text.startswith('console too small'):
        return 'This screen is too small for the codes. Join the bridge Wi-Fi, then open http://192.168.77.1/.'
    if text == 'hotspot disabled':
        return 'The bridge Wi-Fi is turned off in the bridge settings.'
    if not radio:
        return 'The bridge Wi-Fi has not started: no Wi-Fi radio could start it yet.'
    if text in ('radio is not an AP', 'hostapd not active', 'AP services unavailable', 'AP address unavailable'):
        return ('The bridge Wi-Fi did not start on ' + radio + '. The bridge keeps trying, '
                'and moves it to a USB Wi-Fi adapter if one is free.')
    if text in ('management unavailable', 'web interface unavailable') or isinstance(error, OSError):
        return 'The bridge Wi-Fi is on, but the web interface does not answer yet.'
    return 'The bridge is still starting its network.'


def render_waiting(reason, rows, cols):
    lines = ['Astro Bridge', '', 'The bridge cannot be reached yet.', reason, '',
             'What to do:', '- Wait two minutes: the bridge keeps trying by itself.',
             '- Or connect the bridge to your router with an Ethernet cable.',
             '- Or restart the bridge (unplug it, then plug it in again).',
             '', 'Diagnostic console: Alt+F1. ' + CHECK]
    width = max(cols - 1, 20)
    wrapped = []
    for line in lines:
        while len(line) > width:
            cut = line.rfind(' ', 0, width)
            cut = cut if cut > 0 else width
            wrapped.append(line[:cut]); line = line[cut:].lstrip()
        wrapped.append(line)
    return '\x1b[0m\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(wrapped[:max(rows - 1, 1)])


def diagnostic(message):
    try:
        with open('/dev/tty1', 'w') as tty:
            tty.write('\nAstro Bridge: ' + message + '\n' + CHECK + '\n')
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


def show_waiting(reason, displayed):
    """Replace the codes with the reason in plain words; return the new identity."""
    with TTY.open('w') as tty:
        rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(tty, termios.TIOCGWINSZ, b'\0' * 8))
        identity = ('waiting', reason, rows, cols)
        if identity != displayed:
            tty.write(NO_BLANK + render_waiting(reason, rows, cols)); tty.flush()
    return identity


def show_starting(displayed):
    """Never a blank screen while the first check has not passed yet."""
    with TTY.open('w') as tty:
        rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(tty, termios.TIOCGWINSZ, b'\0' * 8))
        identity = ('starting', None, rows, cols)
        if identity != displayed:
            tty.write(NO_BLANK + '\x1b[0m\x1b[?25l\x1b[2J\x1b[H' + '\r\n'.join(['Astro Bridge', '', STARTING][:max(rows - 1, 1)]))
            tty.flush()
    return identity


def show_codes(mode, values, displayed, note=None):
    """Draw the codes once per change of what they say, of the screen size or of the note."""
    with TTY.open('w') as tty:
        rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(tty, termios.TIOCGWINSZ, b'\0' * 8))
        identity = (mode, values, rows, cols, note)
        if identity != displayed:
            screen = render_private(*values, rows, cols) if mode == 'home' else render(*values, rows, cols, note)
            tty.write(NO_BLANK + screen); tty.flush()
    return identity


# Linux console escape: never blank the screen (setterm -blank 0). The codes must stay readable.
NO_BLANK = '\x1b[9;0]'
STARTING = 'The bridge is starting its Wi-Fi. The codes appear in a moment.'
# Shown under codes that stay valid while the hotspot checks fail for a moment.
RESTARTING = 'The bridge Wi-Fi is restarting. These codes stay valid.'
# Checks that fail for a moment while the hotspot restarts or the bridge is busy.
PASSING = {'radio is not an AP', 'hostapd not active', 'AP services unavailable', 'AP address unavailable',
           'management unavailable', 'web interface unavailable', 'configuration changed'}
GRACE_SECONDS = 120


def passing(error):
    """A failure that does not make the codes on screen wrong (the hotspot restarts, the API is busy)."""
    if isinstance(error, (OSError, subprocess.SubprocessError, TimeoutError)):
        return True
    return isinstance(error, ValueError) and str(error) in PASSING


def main():
    if not OWNED.exists():
        return 0
    deadline = time.monotonic() + GRACE_SECONDS
    displayed = None
    reported = None
    while True:
        try:
            try:
                mode, values = 'hotspot', credentials()
            except Exception as error:
                hotspot_error = error
                # Hotspot codes on screen stay through a passing failure of its checks.
                if displayed is not None and displayed[0] == 'hotspot' and passing(error) and time.monotonic() < deadline:
                    raise
                try:
                    url, password = private_access()
                except Exception:
                    raise hotspot_error from None
                mode, values = 'home', (url, password)
            displayed = show_codes(mode, values, displayed)
            READY.touch(mode=0o600)
            deadline = time.monotonic() + GRACE_SECONDS
            reported = None
        except Exception as error:
            READY.unlink(missing_ok=True)
            codes = displayed is not None and displayed[0] in ('hotspot', 'home')
            try:
                if codes and passing(error) and time.monotonic() < deadline:
                    # The codes stay: they are still right, and the hotspot comes back by itself.
                    if displayed[0] == 'hotspot' and displayed[4] is None:
                        displayed = show_codes('hotspot', displayed[1], displayed, RESTARTING)
                elif passing(error) and time.monotonic() < deadline:
                    displayed = show_starting(displayed)
                else:
                    # Never give up silently, never a blank screen: say why here and on the
                    # console, and keep checking so the codes return by themselves.
                    reason = explain(error)
                    if reason != reported:
                        diagnostic(reason)
                        reported = reason
                    displayed = show_waiting(reason, displayed)
            except OSError:
                pass
            except ValueError as failure:
                # The screen cannot hold the codes any more: say so instead of blanking it.
                try:
                    displayed = show_waiting(explain(failure), displayed)
                except OSError:
                    pass
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
