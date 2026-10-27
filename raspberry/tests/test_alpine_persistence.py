"""Exercise the fixed OS helper against independent disposable diskless roots."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'image/alpine/persistence.py'
spec = importlib.util.spec_from_file_location('alpine_persistence', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@unittest.skipUnless(shutil.which('ssh-keygen'), 'OpenSSH key tool required')
class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ['etc/ssh', 'etc/wifi-bridge', 'etc/sudoers.d', 'proc/self', 'dev/disk/by-label', 'var/lib/wifi-bridge']:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.state = self.root / 'var/lib/wifi-bridge'; self.state.chmod(0o700)
        (self.root / 'dev/mmcblk0p2').touch()
        (self.root / 'dev/disk/by-label/ASTRO_DATA').symlink_to('../../mmcblk0p2')
        self.mountinfo = self.root / 'proc/self/mountinfo'
        self.mountinfo.write_text('42 1 179:2 / /var/lib/wifi-bridge rw,noatime - ext4 /dev/mmcblk0p2 rw\n')
        (self.root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\n')
        (self.root / 'etc/shadow').write_text('root:!:1:0:99999:7:::\n')
        self.secret_file(self.root / 'etc/wifi-bridge/bootstrap.json', '{"password":"12345678"}')
        self.commands = []
        self.clock = 1792481093
        self.helper = module.Persistence(self.root, run=self.execute,
                                         now=lambda: self.clock, set_time=self.set_clock)

    def secret_file(self, path, text):
        path.write_text(text); path.chmod(0o600)

    def set_clock(self, value):
        self.clock = value

    def execute(self, args, **kwargs):
        self.commands.append((args, kwargs.get('input')))
        if args[0] == '/usr/sbin/adduser':
            with (self.root / 'etc/passwd').open('a') as stream:
                stream.write('bridge:x:1000:10::/home/bridge:/bin/ash\n')
            with (self.root / 'etc/shadow').open('a') as stream:
                stream.write('bridge:!:1:0:99999:7:::\n')
        elif args[0] == '/usr/sbin/chpasswd':
            account, value = kwargs['input'].rstrip('\n').split(':', 1)
            self.assertEqual(account, 'bridge')
            if '-e' not in args:
                value = '$6$test$' + value.encode().hex()
            shadow = self.root / 'etc/shadow'
            lines = shadow.read_text().splitlines()
            shadow.write_text('\n'.join('bridge:' + value + ':1:0:99999:7:::' if l.startswith('bridge:') else l for l in lines) + '\n')
        else:
            # Real independent Ed25519 key generation and key validation.
            return subprocess.run(args, **kwargs)
        return subprocess.CompletedProcess(args, 0, '', '')

    def request(self, password='synthetic-new-password'):
        self.secret_file(self.state / 'linux-password.pending', password)

    def test_fresh_device_password_and_keys_survive_ram_root_reconstruction(self):
        self.helper.boot()
        original = (self.state / 'identity/ssh/ssh_host_ed25519_key').read_bytes()
        initial_hash = self.helper.stored_hash()
        self.assertEqual(self.helper.shadow_hash(), initial_hash)
        self.assertTrue((self.root / 'etc/ssh/ssh_host_ed25519_key').is_symlink())
        unrelated={name: self.state/name for name in ['config.json','hotspot.secret']}
        for name,path in unrelated.items(): self.secret_file(path,'synthetic-'+name)
        self.request(); self.helper.change_password()
        for name,path in unrelated.items(): self.assertEqual(path.read_text(),'synthetic-'+name)
        changed = self.helper.stored_hash()
        self.assertNotEqual(changed, initial_hash)
        self.assertFalse((self.state / 'linux-password.pending').exists())
        # Simulate loss of all RAM account/key files, retaining only ASTRO_DATA.
        (self.root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/ash\n')
        (self.root / 'etc/shadow').write_text('root:!:1:0:99999:7:::\n')
        for file in (self.root / 'etc/ssh').iterdir(): file.unlink()
        self.commands.clear(); self.helper.boot()
        self.assertEqual(self.helper.shadow_hash(), changed)
        self.assertEqual((self.root / 'etc/ssh/ssh_host_ed25519_key').read_bytes(), original)
        self.assertFalse(any(args == ['/usr/sbin/chpasswd'] for args, _ in self.commands))
        for path in [self.state, self.state/'identity', self.state/'identity/ssh']:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        for path in [self.state/'identity/bridge-password.json', self.state/'identity/ssh/ssh_host_ed25519_key']:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_migration_preserves_existing_password_and_host_key(self):
        self.execute(['/usr/sbin/adduser']); self.execute(['/usr/sbin/chpasswd'], input='bridge:already-changed\n')
        key = self.root / 'etc/ssh/ssh_host_ed25519_key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)], check=True, capture_output=True)
        original, original_hash = key.read_bytes(), self.helper.shadow_hash()
        self.commands.clear(); self.helper.boot()
        self.assertEqual(self.helper.stored_hash(), original_hash)
        self.assertEqual(key.read_bytes(), original)
        self.assertFalse(any(args[0] in ['/usr/sbin/adduser','/usr/sbin/chpasswd'] for args, _ in self.commands))

    def test_distinct_fresh_roots_have_independent_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            second=Path(folder)/'device-two'
            shutil.copytree(self.root,second,symlinks=True)
            self.helper.boot()
            first=(self.state/'identity/ssh/ssh_host_ed25519_key.pub').read_bytes()
            original_root=self.root
            try:
                self.root=second
                other=module.Persistence(second,run=self.execute,now=lambda:self.clock,set_time=self.set_clock)
                other.boot()
            finally:
                self.root=original_root
            self.assertNotEqual((second/'var/lib/wifi-bridge/identity/ssh/ssh_host_ed25519_key.pub').read_bytes(),first)

    def test_persistence_failure_rolls_back_ram_and_committed_hash(self):
        self.helper.boot(); before = self.helper.stored_hash()
        original_atomic = self.helper.atomic
        for after_replace in [False, True]:
            with self.subTest(after_replace=after_replace):
                self.request()
                calls = 0
                def failed(path, content, mode=0o600):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        if after_replace: original_atomic(path, content, mode)
                        raise OSError('simulated storage failure')
                    return original_atomic(path, content, mode)
                with patch.object(self.helper, 'atomic', side_effect=failed), self.assertRaises(OSError):
                    self.helper.change_password()
                self.assertEqual(self.helper.shadow_hash(), before)
                self.assertEqual(self.helper.stored_hash(), before)
                self.assertFalse((self.state/'linux-password.pending').exists())

    def test_cli_password_boundary_requires_explicit_save(self):
        self.helper.boot(); before = self.helper.stored_hash()
        self.execute(['/usr/sbin/chpasswd'], input='bridge:cli-change\n')
        self.assertEqual(self.helper.stored_hash(), before)
        with self.helper.locked(): self.helper.save_password()
        self.assertEqual(self.helper.stored_hash(), self.helper.shadow_hash())

    def test_missing_wrong_readonly_mount_fails_before_account_or_key_changes(self):
        for content in ['', '42 1 0:1 / /var/lib/wifi-bridge rw - tmpfs tmpfs rw\n',
                        '42 1 179:2 / /var/lib/wifi-bridge ro - ext4 /dev/mmcblk0p2 ro\n',
                        '42 1 179:3 / /var/lib/wifi-bridge rw - ext4 /dev/other rw\n',
                        'malformed\n', '42 1 179:2 / /var/lib/wifi-bridge rw - ext4\n']:
            with self.subTest(content=content):
                self.mountinfo.write_text(content)
                with self.assertRaises(RuntimeError): self.helper.boot()
                self.assertFalse((self.state/'identity').exists())
                self.assertFalse(self.commands)
        self.mountinfo.write_text('42 1 179:2 / /var/lib/wifi-bridge rw - ext4 /dev/mmcblk0p2 rw\n')
        (self.root/'dev/disk/by-label/ASTRO_DATA').unlink()
        with self.assertRaises(RuntimeError): self.helper.boot()

    def test_corrupt_or_symlinked_private_state_never_resets_credentials(self):
        self.helper.boot(); before = self.helper.shadow_hash()
        record = self.state/'identity/bridge-password.json'
        for value in ['null','[]','{}','{"version":1,"hash":"bridge:injection"}']:
            self.secret_file(record, value); self.commands.clear()
            with self.assertRaises((ValueError, TypeError)): self.helper.boot()
            self.assertEqual(self.helper.shadow_hash(), before)
            self.assertFalse(self.commands)
        record.unlink(); record.symlink_to(self.root/'etc/shadow')
        with self.assertRaises(OSError): self.helper.boot()
        self.assertEqual(self.helper.shadow_hash(), before)

    def test_unprotected_or_symlinked_private_directory_is_rejected(self):
        self.state.chmod(0o755)
        with self.assertRaises(RuntimeError): self.helper.boot()
        self.state.chmod(0o700)
        outside=self.root/'outside'; outside.mkdir(mode=0o700)
        (self.state/'identity').symlink_to(outside)
        with self.assertRaises(RuntimeError): self.helper.boot()
        self.assertFalse(list(outside.iterdir()))

    def test_invalid_or_public_pending_request_is_removed_without_password_change(self):
        self.helper.boot(); before=self.helper.shadow_hash()
        for value in ['short','bad\npassword','x'*129]:
            self.request(value)
            with self.assertRaises(ValueError): self.helper.change_password()
            self.assertEqual(self.helper.shadow_hash(),before)
            self.assertFalse((self.state/'linux-password.pending').exists())
        self.request(); (self.state/'linux-password.pending').chmod(0o644)
        with self.assertRaises(RuntimeError): self.helper.change_password()
        self.assertEqual(self.helper.shadow_hash(),before)

    def test_clock_checkpoint_is_only_a_bounded_lower_bound(self):
        clock=self.state/'clock-lower-bound'
        initial=self.clock
        for value in ['broken','-1',str(module.MAX_TIME+1)]:
            self.secret_file(clock,value); self.helper.restore_time(); self.assertEqual(self.clock,initial)
        self.secret_file(clock,str(initial-100)); self.helper.restore_time(); self.assertEqual(self.clock,initial)
        self.secret_file(clock,str(initial+100)); self.helper.restore_time(); self.assertEqual(self.clock,initial+100)
        self.helper.checkpoint_time(); self.assertEqual(clock.read_text(),str(initial+100))
        self.assertEqual(clock.stat().st_mode&0o777,0o600)
        # An online time correction may move backwards; checkpoints must follow it.
        self.clock=initial; self.helper.checkpoint_time(); self.assertEqual(clock.read_text(),str(initial))

    def test_clock_restores_from_epoch_and_multi_year_old_base(self):
        checkpoint=1792481093
        for current in [0,1609459200]:
            self.clock=current
            self.secret_file(self.state/'clock-lower-bound',str(checkpoint))
            self.helper.restore_time()
            self.assertEqual(self.clock,checkpoint)

    def test_missing_public_sidecar_is_repaired_before_runtime_publication(self):
        self.helper.boot()
        key=self.state/'identity/ssh/ssh_host_ed25519_key'
        original=key.read_bytes()
        (key.parent/(key.name+'.pub')).unlink()
        for file in (self.root/'etc/ssh').iterdir(): file.unlink()
        self.helper.boot()
        self.assertEqual(key.read_bytes(),original)
        self.assertTrue((self.root/'etc/ssh'/key.name).is_file())
        self.assertTrue((self.root/'etc/ssh'/(key.name+'.pub')).is_file())
        expected=subprocess.run(['ssh-keygen','-y','-f',str(key)],check=True,capture_output=True).stdout
        self.assertEqual((self.root/'etc/ssh'/(key.name+'.pub')).read_bytes(),expected)
        # Invalid persistent keys fail before creating any runtime link.
        for file in (self.root/'etc/ssh').iterdir(): file.unlink()
        self.secret_file(key,'not a valid private key')
        with self.assertRaises(subprocess.CalledProcessError): self.helper.boot()
        self.assertFalse(list((self.root/'etc/ssh').iterdir()))

    def test_cli_has_no_account_root_or_path_override_and_redacts_errors(self):
        # Rejection occurs before trying to touch any live path, even for root CI.
        result=subprocess.run([os.sys.executable,str(SOURCE),'password','--root',str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn(str(self.root),result.stderr)
        self.assertNotIn('Traceback',result.stderr)
