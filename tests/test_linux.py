"""Real Linux certificate storage without Raspberry partition assumptions."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('linux_tls',ROOT/'linux/tls.py');tls=importlib.util.module_from_spec(spec);spec.loader.exec_module(tls)


@unittest.skipUnless(shutil.which('openssl'),'OpenSSL required')
class LinuxTLS(unittest.TestCase):
    def test_owner_confirmed_clock_generates_trusted_identity_on_private_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'var/lib/wifi-bridge';state.mkdir(parents=True,mode=0o700)
            config=state/'config.json';config.write_text(json.dumps({'hostname':'astro-bridge-test'}));config.chmod(0o600)
            def execute(args,**kwargs):
                if args[0]=='/usr/bin/openssl':args=[shutil.which('openssl'),*args[1:]]
                return subprocess.run(args,**kwargs)
            now=1792481093
            helper=tls.Certificates(root,run=execute,now=lambda:now)
            result=helper.prepare({'owner_confirmed_utc':now})
            self.assertTrue(result['configured'])
            helper.enable();status=helper.diagnose()
            self.assertTrue(status['configured']);self.assertTrue(status['enabled'])
            self.assertFalse((root/'dev/disk/by-label/ASTRO_DATA').exists())
            # Shared helper still refuses permissive secret storage.
            state.chmod(0o755)
            with self.assertRaises(ValueError):helper.mounted()
