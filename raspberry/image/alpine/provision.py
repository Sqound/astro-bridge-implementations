"""Create only the fixed local bridge account inside the disposable image."""
import json
from pathlib import Path
import subprocess
subprocess.run(['useradd', '-m', '-s', '/bin/ash', '-G', 'wheel', 'bridge'], check=True)
password = json.loads(Path('/etc/wifi-bridge/bootstrap.json').read_text())['password']
subprocess.run(['/usr/sbin/chpasswd'], input='bridge:' + password + '\n', text=True, check=True, capture_output=True)
# No remote root login. The bridge account gets the same maintenance role as Pi OS.
Path('/etc/sudoers.d/bridge').write_text('bridge ALL=(ALL:ALL) ALL\n')
Path('/etc/sudoers.d/bridge').chmod(0o440)
Path('/etc/ssh/sshd_config.d').mkdir(exist_ok=True)
Path('/etc/ssh/sshd_config.d/astro-bridge.conf').write_text('PermitRootLogin no\nPasswordAuthentication yes\n')
