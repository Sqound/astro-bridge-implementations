#!/usr/bin/python3
"""Give the local console root account a password; never leave it empty.

The image carries no password or hash. On the first boot root receives the
documented initial password (bootstrap.json, normally 12345678); the resulting
hash is kept on the persistent data partition and applied again on every boot,
because the diskless root file system starts from the image each time.
After `passwd`, run `rc-service astro-console-account save` (also done at a
clean shutdown) so the new password survives a reboot.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

STATE = Path('/var/lib/wifi-bridge')
RECORD = STATE / 'console-root.json'
SHADOW = Path('/etc/shadow')


def valid(value):
    if (not isinstance(value, str) or not 2 <= len(value) <= 512 or not value.startswith('$')
            or any(ord(c) < 33 or ord(c) > 126 or c == ':' for c in value)):
        raise ValueError('invalid root password hash')
    return value


def current():
    entries = [line.split(':') for line in SHADOW.read_text().splitlines() if line.startswith('root:')]
    if len(entries) != 1 or len(entries[0]) < 2:
        raise ValueError('missing root entry')
    return entries[0][1]


def save():
    value = valid(current())
    temporary = RECORD.with_name('.console-root.json.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'version': 1, 'hash': value}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, RECORD)


def apply():
    if RECORD.is_file() and not RECORD.is_symlink():
        record = json.loads(RECORD.read_text())
        if not isinstance(record, dict) or record.get('version') != 1:
            raise ValueError('invalid console record')
        subprocess.run(['chpasswd', '-e'], input='root:' + valid(record['hash']) + '\n', text=True, check=True)
        return
    password = json.loads((STATE / 'bootstrap.json').read_text())['password']
    if not isinstance(password, str) or not 8 <= len(password) <= 128 or any(ord(c) < 33 or ord(c) == 127 or c == ':' for c in password):
        raise ValueError('invalid initial password')
    subprocess.run(['chpasswd', '-c', 'sha512'], input='root:' + password + '\n', text=True, check=True)
    save()


def main():
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    if action == 'apply':
        apply()
    elif action == 'save':
        save()
    else:
        raise SystemExit('usage: console-account.py apply|save')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Fail closed: an unusable state locks root rather than leaving it open.
        subprocess.run(['chpasswd', '-e'], input='root:!\n', text=True, check=False)
        print('Astro console account:', error, file=sys.stderr)
        sys.exit(1)
