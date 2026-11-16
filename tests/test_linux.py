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


class LinuxBootstrap(unittest.TestCase):
    def installer(self):
        spec=importlib.util.spec_from_file_location('host_install',ROOT/'linux/host-install.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_fresh_install_has_documented_web_and_hotspot_password(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bootstrap.json'
            self.assertTrue(self.installer().initialize_bootstrap(path))
            self.assertEqual(json.loads(path.read_text()),{'password':'12345678'})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_rerun_preserves_existing_password_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bootstrap.json';path.write_text('{"password":"owner-custom-code","retained":true}');path.chmod(0o600)
            original=path.read_bytes()
            self.assertFalse(self.installer().initialize_bootstrap(path))
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_existing_symlink_is_never_replaced_or_followed_for_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'owner.json';target.write_text('{"password":"owner-custom-code"}')
            path=root/'bootstrap.json';path.symlink_to(target)
            self.assertFalse(self.installer().initialize_bootstrap(path))
            self.assertTrue(path.is_symlink())
            self.assertEqual(json.loads(target.read_text())['password'],'owner-custom-code')
