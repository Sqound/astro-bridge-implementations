import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
root = Path(sys.argv[1])
project = Path(__file__).resolve().parents[2]
manifest = {'version': '0.6.0-alpine-experimental', 'base': json.loads((project / 'image/alpine/base.json').read_text()),
            'built_at': datetime.now(timezone.utc).isoformat(), 'physical_boot_verified': False,
            'mode': 'sys (ext4 root on SD, not diskless)', 'ota_supported': False,
            'boot_used_bytes': int(subprocess.check_output(['du','-sx','--block-size=1',str(root / 'boot')]).split()[0]),
            'rootfs_used_bytes': int(subprocess.check_output(['du','-sx','--block-size=1',str(root)]).split()[0]),
            'package_database': (root / 'lib/apk/db/installed').read_text(),
            'application_sha256': {str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (project / 'wifi_bridge').rglob('*') if p.is_file() and '__pycache__' not in p.parts}}
(project / 'dist/alpine-build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
