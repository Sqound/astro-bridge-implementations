#!/usr/bin/env python3
"""Exercise installed core on disposable Linux networks, never physical radios."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('core',ROOT/'tools/core-artifact.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
CASES=('linux_profiles.py','linux_forwarding.py','linux_multi_forwarding.py','linux_hotspot_sharing.py','linux_consultation.py','linux_mounts.py')


def main():
    p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--image',default='astro-bridge:1.0.1-linux');args=p.parse_args()
    lock=core.verified(args.wheel)
    if hashlib.sha256(args.source.read_bytes()).hexdigest()!=lock['source_sha256']:raise ValueError('Core source checksum mismatch')
    staging=ROOT/'build';staging.mkdir(exist_ok=True)
    # A checkout child is shared with Linux Docker VMs; macOS /var/folders may not be.
    with tempfile.TemporaryDirectory(prefix='astro-linux-tests-',dir=staging) as folder:
        tests=Path(folder)
        with tarfile.open(args.source) as archive:
            for name in CASES:
                member=archive.getmember(f'astro_bridge_core-{lock["version"]}/tests/'+name)
                if not member.isfile() or member.size>1024*1024:raise ValueError('Unsafe test fixture')
                (tests/name).write_bytes(archive.extractfile(member).read())
        for name in CASES:
            # A new --network none namespace per test; harnesses also refuse foreign links.
            command=['docker','run','--rm','--privileged','--network','none','--entrypoint','/bin/sh',
                     '-v',str(tests)+':/tests:ro','-v',str(args.wheel.resolve())+':/core.whl:ro',args.image,'-c',
                     'python3 -m zipfile -e /core.whl /tmp/installed && PYTHONPATH=/tmp/installed python3 /tests/'+name]
            subprocess.run(command,check=True)
    print('PASS all six installed-core Linux network suites in isolated containers')


if __name__=='__main__':main()
