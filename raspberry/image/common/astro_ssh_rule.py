"""The SSH rule shared by every Astro Bridge image.

SSH accepts the account password only when its hash is proved different from
the hash of the documented initial password (bootstrap.json, 12345678). In any
doubt (locked account, another hash scheme, missing tool or file) password login
stays off. The account is root on the Raspberry Pi 5 image and bridge on the Pi
Zero W image. The state really applied is written to /run/astro-ssh/state.json
({"password_login": true|false}) for the bridge service to report.

No password ever reaches a command line, a file or a log here: the candidate
initial password goes to openssl through stdin only.
"""
import json
import os
import re
import subprocess

STATE_NAME = 'state.json'


def sha512_crypt(password, salt):
    """The $6$ hash of password with salt, computed by openssl; the password goes through stdin."""
    result = subprocess.run(['openssl', 'passwd', '-6', '-salt', salt, '-stdin'], input=password + '\n',
                            text=True, capture_output=True, check=True, timeout=20)
    return result.stdout.strip()


def password_changed(current, initial, crypt=sha512_crypt):
    """True only when the hash is provably not the one of the initial password."""
    if not isinstance(current, str) or not isinstance(initial, str) or not initial:
        return False
    match = re.fullmatch(r'\$6\$([A-Za-z0-9./]{1,16})\$[A-Za-z0-9./]{86}', current)
    if not match:
        # Locked, empty or another scheme: nothing proves the owner chose it.
        return False
    try:
        return crypt(initial, match[1]) != current
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def account_hash(shadow_text, account):
    entries = [line.split(':') for line in shadow_text.splitlines() if line.startswith(account + ':')]
    return entries[0][1] if len(entries) == 1 and len(entries[0]) > 1 else ''


def initial_password(path):
    try:
        with open(path) as stream:
            value = json.load(stream)['password']
        return value if isinstance(value, str) else ''
    except (OSError, ValueError, KeyError, TypeError):
        return ''


def allowed(shadow_path, account, bootstrap_path, crypt=sha512_crypt):
    try:
        with open(shadow_path) as stream:
            current = account_hash(stream.read(), account)
    except OSError:
        return False
    return password_changed(current, initial_password(bootstrap_path), crypt)


def write_state(folder, password_login):
    """Record what SSH accepts now, for /status. Root-only, never a symlink."""
    folder = str(folder)
    if os.path.islink(folder):
        raise ValueError('unexpected link: ' + folder)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    target = os.path.join(folder, STATE_NAME)
    temporary = os.path.join(folder, '.' + STATE_NAME + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'password_login': bool(password_login)}, stream)
    os.replace(temporary, target)
