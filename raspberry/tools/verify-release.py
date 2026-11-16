"""Verify app artifacts for both platforms; optional independently built SD images."""
import hashlib
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ota import unpack

parser = argparse.ArgumentParser()
parser.add_argument('--apps-only', action='store_true')
parser.add_argument('--artifact-dir', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
dist = (args.artifact_dir or root / 'dist').resolve()
for name in ('ota-manifest.json', 'ota-manifest-linux-alpine-armhf.json'):
    manifest = json.loads((dist / name).read_text())
    archive = dist / manifest['asset']
    assert archive.parent == dist and archive.name.endswith('-ota.tar.gz')
    with tempfile.TemporaryDirectory() as temporary:
        unpack(archive.read_bytes(), manifest, Path(temporary), platform=manifest['platform'])
if not args.apps_only:
    for line in (dist / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(); target = dist / name
        assert target.parent == dist and target.name.endswith('.img.xz')
        with target.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest
print('PASS: both platform application archives' + ('' if args.apps_only else ' and supplied image checksums'))
