import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

root = Path(sys.argv[1])
project = Path(__file__).resolve().parents[1]
source_hashes = {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for folder in ("wifi_bridge", "scripts", "systemd", "image")
                 for p in (project / folder).rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and "cache" not in p.parts}
manifest = {"version": "0.6.0", "built_at": datetime.now(timezone.utc).isoformat(),
            "base": json.loads((project / "image/base.json").read_text()),
            "source_sha256": source_hashes,
            "hardware_validation": "partial — Pi Zero W boot, WPA2 setup, USB home Wi-Fi and telescope TCP relay verified; see docs/TESTS.md",
            "packages": (root / "var/lib/dpkg/status").read_text()}
(project / "dist/build-manifest.json.partial").write_text(json.dumps(manifest, indent=2))
