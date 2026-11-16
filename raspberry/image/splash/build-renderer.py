#!/usr/bin/env python3
"""Compile the boot splash renderer as a static ARM64 Linux executable.

Runs graphic-splash.c through gcc, musl and the Linux UAPI headers (linux/fb.h,
kd.h, vt.h) from signed Alpine packages, at the exact versions pinned in
image/pi5/public-inputs.json, inside the pinned Alpine container
(image/alpine/standard/inputs.json). Needs Docker on
an ARM64 host. Nothing from outside this tree and the Alpine repositories is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def static_aarch64(path):
    data = path.read_bytes()
    if data[:7] != b'\x7fELF\x02\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise SystemExit('renderer is not a 64-bit little-endian ARM64 ELF')
    phoff, = struct.unpack_from('<Q', data, 32)
    size, count = struct.unpack_from('<HH', data, 54)
    for index in range(count):
        if struct.unpack_from('<I', data, phoff + index * size)[0] == 3:  # PT_INTERP
            raise SystemExit('renderer is dynamically linked')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory for the executable')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing an existing output directory')
    args.output.mkdir(parents=True)
    container = json.loads((ROOT / 'image/alpine/standard/inputs.json').read_text())['container']
    toolchain = json.loads((ROOT / 'image/pi5/public-inputs.json').read_text())['splash_toolchain']
    if set(toolchain) != {'gcc', 'musl-dev', 'linux-headers'} or not all(re.fullmatch(r'[0-9][0-9A-Za-z._]*-r[0-9]+', v) for v in toolchain.values()):
        raise SystemExit('Invalid splash toolchain pin')
    packages = ' '.join(f'{name}={version}' for name, version in sorted(toolchain.items()))
    script = (f'apk add --no-cache {packages} >/dev/null && '
              'cc -static -Os -Wall -Werror -o /out/astro-graphic-splash /src/graphic-splash.c && '
              'strip /out/astro-graphic-splash && apk list --installed gcc musl-dev linux-headers binutils musl > /out/toolchain.txt')
    subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                    '--mount', f'type=bind,source={HERE},target=/src,readonly',
                    '--mount', f'type=bind,source={args.output.resolve()},target=/out',
                    '--entrypoint', '/bin/sh', container, '-euc', script], check=True)
    binary = args.output / 'astro-graphic-splash'
    static_aarch64(binary)
    print(json.dumps({'renderer': binary.name, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                      'toolchain': [line.split()[0] for line in (args.output / 'toolchain.txt').read_text().splitlines() if line.strip()]}, indent=2))


if __name__ == '__main__':
    main()
