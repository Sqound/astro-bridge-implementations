#!/usr/bin/env python3
"""Refuse an SD image that carries an SSH key, a saved Wi-Fi secret or private key material.

Reads a compressed image file only; it never touches a disk device. The boot
partition is read with mtools (`mdir`/`mcopy`), the rest is scanned as bytes.
"""
import argparse
import lzma
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile

KEY_LINE = re.compile(rb'(?m)^(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+) AAAA[0-9A-Za-z+/]{40,}')
ARMOUR = re.compile(rb'-{5}BEGIN [A-Z ]*PRIVATE KEY-{5}')
WIFI_SECRET = re.compile(rb'(?m)^\s*(?:psk|wpa-psk|sae-password|password)\s*=\s*\S+')


def partitions(header):
    if header[510:512] != b'\x55\xaa':
        raise SystemExit('Not an MBR disk image')
    for index in range(4):
        kind, start, sectors = struct.unpack_from('<4xB3xII', header, 446 + 16 * index)
        if kind:
            yield kind, start * 512, sectors * 512


def check_member(name, data, problems):
    base = name.rsplit('/', 1)[-1]
    if base in ('authorized_keys', 'authorized_keys2') and KEY_LINE.search(data):
        problems.append(f'SSH authorized key in {name}')
    if ARMOUR.search(data):
        problems.append(f'private key material in {name}')
    if (base.endswith('.nmconnection') or base.startswith('wpa_supplicant')) and WIFI_SECRET.search(data):
        problems.append(f'saved Wi-Fi secret in {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    if not shutil.which('mcopy'):
        raise SystemExit('mtools is required')
    problems = []
    with tempfile.TemporaryDirectory(prefix='astro-image-check-') as folder:
        raw = Path(folder) / 'image.img'
        with lzma.open(args.image) as source, raw.open('wb') as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        with raw.open('rb') as stream:
            table = list(partitions(stream.read(512)))
            for kind, offset, size in table:
                if kind in (0x04, 0x06, 0x0b, 0x0c, 0x0e):
                    spec = f'{raw}@@{offset}'
                    names = subprocess.run(['mdir', '-i', spec, '-/', '-b', '::'], check=True, capture_output=True, text=True).stdout.split()
                    for name in names:
                        if name.endswith('/'):
                            continue  # mdir -/ lists directories too; their files follow.
                        target = Path(folder) / 'boot' / name.lstrip(':/').replace('/', '__')
                        target.parent.mkdir(exist_ok=True)
                        subprocess.run(['mcopy', '-n', '-i', spec, name, str(target)], check=True)
                        if target.is_dir():
                            continue  # a directory entry; its files are listed separately
                        data = target.read_bytes()
                        check_member(name, data, problems)
                        if name.endswith('.apkovl.tar.gz'):
                            with tarfile.open(target) as archive:
                                for member in archive:
                                    if member.isfile():
                                        check_member(name + '!' + member.name, archive.extractfile(member).read(), problems)
                else:
                    stream.seek(offset)
                    remaining = size
                    tail = b''
                    while remaining > 0:
                        block = stream.read(min(remaining, 8 * 1024 * 1024))
                        if not block:
                            break
                        remaining -= len(block)
                        window = tail + block
                        if KEY_LINE.search(window) or ARMOUR.search(window):
                            problems.append(f'key material in partition at offset {offset}')
                            break
                        tail = block[-4096:]
    if problems:
        raise SystemExit('Refused:\n' + '\n'.join(sorted(set(problems))))
    print(f'PASS {args.image.name}: no SSH key, saved Wi-Fi secret or private key material in {len(table)} partitions')


if __name__ == '__main__':
    main()
