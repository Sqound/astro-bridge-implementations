"""Apply only the fixed bridge account password from a root-only one-shot request."""
from pathlib import Path
import subprocess

PENDING = Path('/var/lib/wifi-bridge/linux-password.pending')


def apply_linux_password():
    try:
        password = PENDING.read_text()
        if not 8 <= len(password) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in password):
            raise ValueError('Invalid password request')
        subprocess.run(['/usr/sbin/chpasswd'], input='bridge:' + password + '\n', text=True,
                       check=True, capture_output=True)
    finally:
        PENDING.unlink(missing_ok=True)


if __name__ == '__main__':
    apply_linux_password()
