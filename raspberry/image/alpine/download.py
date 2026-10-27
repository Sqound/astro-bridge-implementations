"""Download pinned official Alpine ARMv6 inputs, with checksum validation."""
import hashlib
import json
from pathlib import Path
import urllib.request
root = Path(__file__).resolve().parent
cache = root.parent / 'cache/alpine'
cache.mkdir(parents=True, exist_ok=True)
base = json.loads((root / 'base.json').read_text())
for key in ('rootfs', 'boot'):
    path = cache / (key + '.tar.gz')
    if not path.exists():
        temporary = path.with_suffix('.part')
        with urllib.request.urlopen(base[key]['url'], timeout=60) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(path)
    with path.open('rb') as source:
        assert hashlib.file_digest(source, 'sha256').hexdigest() == base[key]['sha256'], key
    print('Verified official Alpine input:', key)
