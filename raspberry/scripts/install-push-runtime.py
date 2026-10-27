#!/usr/bin/env python3
"""Explicit optional crypto provisioning, isolated from the live diskless OS.

Existing boxes: sudo python3 scripts/install-push-runtime.py
Image builder: --vapid-only TARGET (native crypto is supplied by signed world APKs).
No OS package upgrade, boot overlay capture, credentials or service restart.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

WHEEL = 'https://files.pythonhosted.org/packages/6b/46/8843d8e00eb4fcece8e12c109ecf5b17a3d4cc9355dab23c2585cb51ed3c/py_vapid-1.9.6-py2.py3-none-any.whl'
SHA256 = '06d775951016f63f0d0d6dd8babd333fc2f831f0769fd5db2e1ec8cfbbccc70f'
TARGET = Path('/var/lib/wifi-bridge/push-runtime')


def vapid(target):
    with urllib.request.urlopen(WHEEL, timeout=30) as response:
        data = response.read(131073)
    if len(data) > 131072 or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Pinned VAPID wheel integrity check failed')
    target.mkdir(mode=0o700, parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            parts = Path(info.filename).parts
            if (not parts or parts[0] not in ('py_vapid', 'py_vapid-1.9.6.dist-info')
                    or any(p in ('.', '..') for p in parts) or info.filename.startswith('/')
                    or info.external_attr >> 16 & 0o170000 == 0o120000):
                raise ValueError('Unexpected wheel member')
            path = target.joinpath(*parts)
            if info.is_dir(): path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(info)); path.chmod(0o644)
    (target / 'SHA256').write_text(SHA256 + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vapid-only', type=Path)
    args = parser.parse_args()
    if args.vapid_only:
        vapid(args.vapid_only); return
    if os.geteuid() != 0: raise SystemExit('Run as root on the Alpine bridge.')
    if not Path('/etc/wifi-bridge/diskless').is_file(): raise SystemExit('Diskless Alpine is required.')
    if TARGET.exists(): raise SystemExit('Runtime already exists; inspect it rather than overwriting it.')
    TARGET.parent.mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.push-runtime-', dir=TARGET.parent))
    try:
        stage.chmod(0o700)
        repositories = stage / 'repositories'
        repositories.write_text('https://dl-cdn.alpinelinux.org/alpine/v3.24/main\nhttps://dl-cdn.alpinelinux.org/alpine/v3.24/community\n')
        subprocess.run(['/sbin/apk', '--root', str(stage), '--arch', 'armhf', '--keys-dir', '/etc/apk/keys',
                        '--repositories-file', str(repositories), '--no-cache', 'add', '--initdb', '--no-scripts',
                        'py3-cryptography=47.0.0-r0', 'py3-http-ece=1.2.1-r1'], check=True, timeout=240)
        vapid(stage / 'vapid')
        # Prove ABI/loading with the live interpreter before exposing the depot.
        env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(stage/'usr/lib/python3.14/site-packages') + ':' + str(stage/'vapid'),
               'LD_LIBRARY_PATH': str(stage/'usr/lib') + ':' + str(stage/'lib')}
        subprocess.run(['/usr/bin/python3', '-c', 'import cryptography,http_ece,py_vapid; from py_vapid import Vapid02; v=Vapid02();v.generate_keys();print("Push crypto runtime verified")'],
                       env=env, check=True, timeout=30)
        stage.rename(TARGET)
        print('Persistent push runtime installed. Restart only the application when safe.')
    finally:
        if stage.exists(): shutil.rmtree(stage)


if __name__ == '__main__': main()
