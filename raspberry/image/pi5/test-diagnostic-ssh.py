#!/usr/bin/env python3
"""Offline checks for the opt-in diagnostic SSH overlay."""
import base64, importlib.util, pathlib, tempfile, unittest
HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('recipe',HERE/'prepare-gphoto.py')
recipe=importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
BLOB=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+bytes(range(32))
KEY='ssh-ed25519 '+base64.b64encode(BLOB).decode()
class DiagnosticSSH(unittest.TestCase):
 def test_validation_and_overlay(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d); key=root/'key.pub'; key.write_text(KEY+'\n')
   result=recipe.diagnostic_key(key)
   for content in ('', KEY+'\n'+KEY+'\n', 'ssh-rsa '+KEY.split()[1], KEY+' comment', '-----BEGIN OPENSSH ' + 'PRIVATE KEY-----', 'ssh-ed25519 AAAA', 'ssh-ed25519\n'+KEY.split()[1]):
    key.write_text(content)
    with self.assertRaises(ValueError): recipe.diagnostic_key(key)
   overlay=root/'overlay'; (overlay/'etc/runlevels/default').mkdir(parents=True); (overlay/'etc/init.d').mkdir()
   self.assertFalse((overlay/'etc/runlevels/default/astro-diagnostic-ssh').exists())
   recipe.install_diagnostic_ssh(overlay,result)
   authorized=overlay/'etc/ssh/astro-diagnostic-authorized-keys'
   self.assertEqual(authorized.read_text(),'restrict,pty '+KEY+'\n')
   self.assertEqual(authorized.stat().st_mode & 0o777,0o600)
   self.assertEqual((overlay/'etc/init.d/astro-diagnostic-ssh').stat().st_mode & 0o777,0o755)
   self.assertEqual((overlay/'etc/runlevels/default/astro-diagnostic-ssh').readlink().as_posix(),'/etc/init.d/astro-diagnostic-ssh')
   self.assertEqual((overlay/'etc/ssh/astro-diagnostic-sshd_config').stat().st_mode & 0o777,0o600)
if __name__=='__main__': unittest.main()
