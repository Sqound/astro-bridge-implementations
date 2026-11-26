#!/usr/bin/python3
"""Prepare the remote console (SSH) of the appliance; no password ever comes with the image.

SSH is started at every boot. Who may log in is decided here, at each start:

- a public key the owner placed in /var/lib/wifi-bridge/ssh/authorized_keys
  (kept on the data partition) always works;
- the root password works only once the owner has replaced the documented
  initial one (passwd, then rc-service astro-console-account save). While root
  still has the initial password, or when that cannot be told, password login
  stays off: a published initial password must never open a network login.

The host key is created once and kept on the data partition, so the bridge
keeps one identity across boots of its in-memory system.

The rule itself is shared with the Pi Zero W image (astro_ssh_rule.py, installed
next to this file); the state really applied is written for the bridge service.
"""
import os
from pathlib import Path
import subprocess
import sys

# Installed side by side in /etc/wifi-bridge; image/common in the source tree.
for _folder in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1] / 'common'):
    if (_folder / 'astro_ssh_rule.py').is_file():
        sys.path.insert(0, str(_folder))
        break
from astro_ssh_rule import account_hash, initial_password, password_changed, sha512_crypt, write_state  # noqa: E402

STATE = Path('/var/lib/wifi-bridge')
FOLDER = STATE / 'ssh'
RUNTIME = Path('/run/astro-ssh')
SHADOW = Path('/etc/shadow')
HOME = Path('/root')


def root_hash(shadow=SHADOW):
    return account_hash(shadow.read_text(), 'root')


def server_config(password_login, folder=FOLDER):
    answer = 'yes' if password_login else 'no'
    return '\n'.join([
        'Port 22', 'HostKey ' + str(folder / 'ssh_host_ed25519_key'), 'PidFile /run/astro-sshd.pid',
        'AuthorizedKeysFile ' + str(folder / 'authorized_keys'),
        'PubkeyAuthentication yes',
        'PasswordAuthentication ' + answer,
        'PermitRootLogin ' + ('yes' if password_login else 'prohibit-password'),
        'KbdInteractiveAuthentication no', 'PermitEmptyPasswords no', 'AllowUsers root',
        'MaxAuthTries 3', 'LoginGraceTime 30',
        'AllowAgentForwarding no', 'X11Forwarding no', 'PermitTunnel no', 'PermitUserEnvironment no', ''])


def private_folder(path):
    if path.is_symlink():
        raise ValueError('unexpected link: ' + str(path))
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def key_link(home, keys):
    """Make the standard ssh-copy-id land in the kept key file.

    ssh-copy-id appends to ~/.ssh/authorized_keys, which lives in memory here and is
    not the file sshd reads. That path becomes a link to the file on the data
    partition; keys written there before the link existed are moved into it.
    """
    if not home.is_dir() or home.is_symlink():
        return
    dot = home / '.ssh'
    private_folder(dot)
    link = dot / 'authorized_keys'
    if link.is_symlink():
        if os.readlink(link) == str(keys):
            return
        link.unlink()
    elif link.exists():
        kept = keys.read_text().splitlines() if keys.exists() else []
        added = [line for line in link.read_text().splitlines() if line.strip() and line not in kept]
        if added:
            fd = os.open(keys, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write('\n'.join(([''] if kept and kept[-1] else []) + added) + '\n')
            os.chmod(keys, 0o600)
        link.unlink()
    os.symlink(keys, link)


def prepare(state=STATE, folder=FOLDER, runtime=RUNTIME, shadow=SHADOW, keygen=None, crypt=sha512_crypt, home=HOME):
    if not state.is_dir() or state.is_symlink():
        raise ValueError('data partition is not mounted')
    private_folder(folder)
    key = folder / 'ssh_host_ed25519_key'
    for path in (key, key.with_name(key.name + '.pub'), folder / 'authorized_keys'):
        if path.is_symlink():
            raise ValueError('unexpected link: ' + str(path))
    if not key.exists():
        (keygen or (lambda target: subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(target)], check=True)))(key)
    os.chmod(key, 0o600)
    keys = folder / 'authorized_keys'
    key_link(home, keys)
    if keys.exists():
        os.chmod(keys, 0o600)
    initial = initial_password(state / 'bootstrap.json')
    try:
        allowed = password_changed(root_hash(shadow), initial, crypt)
    except OSError:
        allowed = False
    private_folder(runtime)
    target = runtime / 'sshd_config'
    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(server_config(allowed, folder))
    write_state(runtime, allowed)
    return allowed


if __name__ == '__main__':
    if sys.argv[1:] != ['prepare']:
        raise SystemExit('usage: ssh-access.py prepare')
    try:
        allowed = prepare()
    except Exception as error:
        try:
            write_state(RUNTIME, False)  # Never leave an earlier "open" state behind.
        except Exception:
            pass
        print('Astro Bridge SSH:', error, file=sys.stderr)
        sys.exit(1)
    print('Astro Bridge SSH: key login' + (' and root password login' if allowed else
          ' only (root still has the initial password, or it cannot be told)'))
