import hashlib
import json
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parent
base = json.loads((root / "base.json").read_text())
destination = root / "cache" / "base.img.xz"
destination.parent.mkdir(exist_ok=True)
if not destination.exists():
    partial = destination.with_suffix(".part")
    print("Downloading the official 32-bit base image…", flush=True)
    with urllib.request.urlopen(base["url"], timeout=60) as response, partial.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    partial.replace(destination)
digest = hashlib.file_digest(destination.open("rb"), "sha256").hexdigest()
if digest != base["sha256"]:
    raise SystemExit("SHA-256 mismatch. Remove image/cache/base.img.xz and retry.")
print("Official base image verified: " + digest, flush=True)
