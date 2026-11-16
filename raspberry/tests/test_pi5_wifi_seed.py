import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pi5_firstboot', Path(__file__).resolve().parents[1] / 'image/pi5/firstboot.py')
firstboot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firstboot)


def disk(path, payload=b''):
    mbr = bytearray(512)
    struct.pack_into('<B3sB3sII', mbr, 446, 128, b'\0'*3, 6, b'\0'*3, 2048, 264192)
    struct.pack_into('<B3sB3sII', mbr, 462, 0, b'\0'*3, 0x83, b'\0'*3, 266240, 2097152)
    mbr[510:] = b'\x55\xaa'
    with path.open('wb') as f:
        f.write(mbr); f.seek(firstboot.SEED_OFFSET)
        raw = firstboot.SEED_MAGIC + payload
        f.write(raw + bytes(firstboot.SEED_SIZE-len(raw)))
    return (266240 + 2097152)*512


class SeedTests(unittest.TestCase):
    mac = 'aa:bb:cc:dd:ee:ff'
    def test_avahi_recipe_is_pinned_and_starts_loopback_only(self):
        image = Path(__file__).resolve().parents[1] / 'image/pi5'
        spec = importlib.util.spec_from_file_location('pi5_recipe', image/'prepare-gphoto.py')
        recipe = importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
        self.assertIn('allow-interfaces=lo\n', recipe.AVAHI_INITIAL_CONFIG)
        self.assertIn('enable-reflector=no\n', recipe.AVAHI_INITIAL_CONFIG)
        lock = json.loads((image/'gphoto-packages.lock.json').read_text())['runtime-extra']
        for name in ('avahi-0.9_rc4-r0.apk', 'avahi-libs-0.9_rc4-r0.apk', 'avahi-openrc-0.9_rc4-r0.apk', 'libdaemon-0.14-r6.apk', 'libevent-2.1.13-r0.apk'):
            self.assertRegex(lock[name]['sha256'], r'^[0-9a-f]{64}$')

    def test_assembly_reserves_only_blank_range(self):
        path = Path(__file__).resolve().parents[1] / 'image/pi5/assemble.py'
        spec = importlib.util.spec_from_file_location('pi5_assemble', path)
        assembly = importlib.util.module_from_spec(spec); spec.loader.exec_module(assembly)
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp)/'image'
            with image.open('wb') as f: f.truncate(assembly.SEED_OFFSET + assembly.SEED_SIZE)
            assembly.assert_seed_blank(image)
            with image.open('r+b') as f: assembly.reserve_seed(f)
            with image.open('rb') as f:
                f.seek(assembly.SEED_OFFSET)
                self.assertEqual(f.read(assembly.SEED_SIZE), firstboot.SEED_MAGIC + bytes(firstboot.SEED_SIZE-len(firstboot.SEED_MAGIC)))
            with self.assertRaises(ValueError): assembly.assert_seed_blank(image)

    def test_slot_marker_layout_validation_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp)/'disk.img'
            size = disk(image, b'{"bad":true}\n')
            with image.open('r+b') as f:
                raw = firstboot.seed_slot(f, size)
                self.assertIsNone(firstboot.parse_seed(raw, self.mac))
                firstboot.clear_seed(f)
                self.assertEqual(firstboot.seed_slot(f, size), firstboot.SEED_MAGIC + bytes(firstboot.SEED_SIZE-len(firstboot.SEED_MAGIC)))
                f.seek(462+8); f.write(struct.pack('<I', 9)); f.flush()
                self.assertIsNone(firstboot.seed_slot(f, size))
                self.assertIsNone(firstboot.seed_slot(f, 1024))

    def test_strict_seed_and_no_control_or_duplicates(self):
        seed = {'ssid':'Maison 日本', 'psk':'a'*64, 'hidden':True, 'country':'JP'}
        def raw(line):
            value = firstboot.SEED_MAGIC + line + b'\n'
            return value + bytes(firstboot.SEED_SIZE-len(value))
        parsed = firstboot.parse_seed(raw(json.dumps(seed, ensure_ascii=True).encode()), self.mac)
        self.assertEqual(parsed[0]['ssid'], seed['ssid'])
        self.assertEqual(parsed[0]['password'], seed['psk'])
        self.assertTrue(parsed[0]['hidden'])
        self.assertEqual(parsed[1], 'JP')
        config = firstboot.initial_config(self.mac, raw(json.dumps(seed, ensure_ascii=True).encode()))
        self.assertEqual(config['interface_roles'], {self.mac:'auto'})
        self.assertTrue(config['hotspot']['enabled'])
        self.assertEqual(config['hotspot']['mac'], '')
        self.assertEqual(config['connection_mode'], 'hotspot_shared')
        fallback = firstboot.initial_config(self.mac, raw(b'bad'))
        self.assertTrue(fallback['hotspot']['enabled'])
        self.assertEqual(fallback['connection_mode'], 'hotspot_shared')
        for line in (b'{"ssid":"x","ssid":"y"}', json.dumps(dict(seed, psk='A'*64)).encode(), json.dumps(dict(seed, ssid='bad\n')).encode(), json.dumps(dict(seed, country='jp')).encode()):
            self.assertIsNone(firstboot.parse_seed(raw(line), self.mac))

    def test_gphoto_recipe_installs_eth0_profile_in_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp)
            spec = importlib.util.spec_from_file_location('pi5_gphoto_wired', Path(__file__).resolve().parents[1] / 'image/pi5/prepare-gphoto.py')
            recipe = importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
            recipe.install_wired_profile(overlay)
            profile = overlay/'etc/NetworkManager/system-connections/wifi-bridge-wired.nmconnection'
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
            self.assertIn('id=wifi-bridge-wired\n', profile.read_text())
            self.assertIn('interface-name=eth0\n', profile.read_text())
            self.assertIn('[ipv4]\nmethod=auto\n', profile.read_text())
            self.assertIn('[ipv6]\nmethod=disabled\n', profile.read_text())

    def test_new_identity_uses_shared_eight_digit_default_without_touching_existing(self):
        import builtins
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); radio = state/'radio'; radio.mkdir()
            (radio/'device').mkdir(); (radio/'address').write_text(self.mac)
            for name, target in (('driver', 'brcmfmac'), ('subsystem', 'sdio')):
                directory = state/target; directory.mkdir()
                (radio/'device'/name).symlink_to(directory)
            path_factory = firstboot.Path
            def fake_path(value):
                return radio/'address' if str(value) == '/sys/class/net/wlan0/address' else path_factory(value)
            old_stat = Path.stat
            def root_stat(path, *a, **kw):
                result = old_stat(path, *a, **kw); values = list(result); values[4] = 0
                return os.stat_result(values)
            original_open = builtins.open
            def safe_open(path, *a, **kw):
                return io.StringIO() if str(path) == '/dev/console' else original_open(path, *a, **kw)
            with patch.object(firstboot, 'STATE', state), patch.object(firstboot, 'Path', side_effect=fake_path), \
                 patch.object(Path, 'stat', root_stat), patch.object(firstboot, 'open_seed_device', return_value=(None, None)), \
                 patch.object(builtins, 'open', side_effect=safe_open):
                firstboot.main()
                self.assertEqual(json.loads((state/'bootstrap.json').read_text())['password'], '12345678')
                (state/'bootstrap.json').write_text('{"password":"existing-user-password"}')
                firstboot.main()
                self.assertEqual(json.loads((state/'bootstrap.json').read_text())['password'], 'existing-user-password')

    def test_transaction_resumes_before_seed_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); image = state/'disk.img'
            seed = {'ssid':'Maison', 'psk':'a'*64, 'hidden':False, 'country':'FR'}
            size = disk(image, json.dumps(seed).encode()+b'\n')
            config = {'home': {'ssid':'Maison','password':'a'*64}, 'interface_roles': {self.mac:'auto'}}
            pending = {'bootstrap': {'password':'b'*24}, 'config':config}
            old_stat = Path.stat
            def root_stat(path, *a, **kw):
                result = old_stat(path, *a, **kw)
                values = list(result); values[4] = 0
                return os.stat_result(values)
            def open_device():
                f = image.open('r+b'); return f, firstboot.seed_slot(f, size)
            old_fstat = os.fstat
            def root_fstat(fd):
                result = old_fstat(fd)
                values = list(result); values[4] = 0
                return os.stat_result(values)
            import builtins
            original_open = builtins.open
            def safe_open(path, *a, **kw):
                return io.StringIO() if str(path) == '/dev/console' else original_open(path, *a, **kw)
            with patch.object(firstboot, 'STATE', state), patch.object(Path, 'stat', root_stat), patch.object(os, 'fstat', root_fstat), patch.object(firstboot, 'open_seed_device', side_effect=open_device), patch.object(builtins, 'open', side_effect=safe_open):
                firstboot.atomic_json(state/'identity-pending.json', pending)
                original = firstboot.atomic_json
                def interrupted(path, value):
                    if path.name == 'config.json': raise OSError('simulated interruption')
                    return original(path, value)
                with patch.object(firstboot, 'atomic_json', side_effect=interrupted):
                    with self.assertRaises(OSError): firstboot.main()
                with image.open('rb') as f: self.assertNotEqual(firstboot.seed_slot(f,size), firstboot.SEED_MAGIC + bytes(firstboot.SEED_SIZE-len(firstboot.SEED_MAGIC)))
                firstboot.main()
                self.assertEqual(json.loads((state/'config.json').read_text()), config)
                self.assertEqual(json.loads((state/'bootstrap.json').read_text()), pending['bootstrap'])
                self.assertTrue((state/'identity-ready').exists())
                self.assertFalse((state/'identity-pending.json').exists())
                with image.open('rb') as f: self.assertEqual(firstboot.seed_slot(f,size), firstboot.SEED_MAGIC + bytes(firstboot.SEED_SIZE-len(firstboot.SEED_MAGIC)))
