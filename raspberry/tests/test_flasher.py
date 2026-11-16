"""Portable flash preparation: never write a disk or launch a GUI in tests."""
import hashlib
import io
import json
import subprocess
import sys
import urllib.error
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('flasher', Path(__file__).resolve().parents[1] / 'tools/astro-bridge-flasher.py')
flasher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flasher)


class FlasherTests(unittest.TestCase):
    def make_wifi_fixture(self, root):
        data = bytearray(100000)
        data[510:512] = b'\x55\xaa'
        flasher.struct.pack_into('<B3sB3sII', data, 446, 0x80, b'\0'*3, 0x06, b'\0'*3, 2048, 264192)
        flasher.struct.pack_into('<B3sB3sII', data, 462, 0, b'\0'*3, 0x83, b'\0'*3, 266240, 2097152)
        offset = flasher.WIFI_SEED_OFFSET
        data[offset:offset + len(flasher.WIFI_SEED_MARKER)] = flasher.WIFI_SEED_MARKER
        image = root / 'base.img.xz'
        image.write_bytes(flasher.lzma.compress(bytes(data)))
        return image, bytes(data), hashlib.sha256(image.read_bytes()).hexdigest()

    def test_wifi_seed_derivation_and_strict_validation(self):
        settings = {'ssid': 'IEEE', 'password': 'password', 'hidden': True, 'country': 'JP'}
        seed = flasher.wifi_seed(settings)
        self.assertEqual(len(seed), 4096)
        obj = json.loads(seed[len(flasher.WIFI_SEED_MARKER):].split(b'\n', 1)[0])
        self.assertEqual(obj, {'ssid': 'IEEE', 'psk': 'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e', 'hidden': True, 'country': 'JP'})
        self.assertNotIn(b'password', seed)
        unicode_password = 'café1234'
        unicode_seed = flasher.wifi_seed(dict(settings, password=unicode_password))
        unicode_psk = json.loads(unicode_seed[len(flasher.WIFI_SEED_MARKER):].split(b'\n')[0])['psk']
        self.assertEqual(unicode_psk, '2be7911ca704de032b2fb5cb8383a4fb3aebc6caddf5b9302a24c0bcc3400a68')
        self.assertEqual(json.loads(flasher.wifi_seed(dict(settings, password='AB'*32))[len(flasher.WIFI_SEED_MARKER):].split(b'\n')[0])['psk'], 'ab'*32)
        for bad in (dict(settings, ssid=''), dict(settings, ssid='a'*33), dict(settings, ssid='x\nsecret'), dict(settings, password='short'), dict(settings, password='é'*4+'\n'), dict(settings, country='jp'), dict(settings, hidden=1), dict(settings, extra='x')):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'Invalid Wi-Fi settings'):
                flasher.wifi_seed(bad)

    def test_personalized_image_is_private_ephemeral_and_only_patches_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image, original, checksum = self.make_wifi_fixture(root)
            wifi = {'ssid': '日本', 'password': 'abcdefgh', 'hidden': False, 'country': 'JP'}
            with patch.object(flasher, 'WIFI_IMAGE_SIZE', len(original)), patch.object(flasher, 'cache_directory', return_value=root / 'cache'):
                with flasher.personalized_image(image, checksum, len(original), wifi) as (raw, digest):
                    self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(raw.parent.stat().st_mode & 0o777, 0o700)
                    result = raw.read_bytes(); self.assertEqual(hashlib.sha256(result).hexdigest(), digest)
                    start = flasher.WIFI_SEED_OFFSET; end = start + flasher.WIFI_SEED_SIZE
                    self.assertEqual(result[:start], original[:start]); self.assertEqual(result[end:], original[end:])
                    payload = json.loads(result[start + len(flasher.WIFI_SEED_MARKER):end].split(b'\n')[0])
                    self.assertEqual(payload['ssid'], wifi['ssid']); self.assertNotIn(b'abcdefgh', result)
                self.assertFalse(raw.exists()); self.assertFalse(raw.parent.exists())

    def test_personalized_rejects_changed_source_bad_geometry_or_marker(self):
        wifi = {'ssid': 'test', 'password': 'abcdefgh', 'hidden': False, 'country': 'JP'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image, original, checksum = self.make_wifi_fixture(root)
            with patch.object(flasher, 'WIFI_IMAGE_SIZE', len(original)), patch.object(flasher, 'cache_directory', return_value=root / 'cache'):
                for position in (510, 450, 478, flasher.WIFI_SEED_OFFSET):
                    damaged = bytearray(original); damaged[position] ^= 1
                    image.write_bytes(flasher.lzma.compress(damaged)); actual = hashlib.sha256(image.read_bytes()).hexdigest()
                    with self.subTest(position=position), self.assertRaisesRegex(ValueError, 'does not support'):
                        with flasher.personalized_image(image, actual, len(original), wifi): pass
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    with flasher.personalized_image(image, checksum, len(original), wifi): pass
                with self.assertRaisesRegex(ValueError, 'does not fit'):
                    with flasher.personalized_image(image, checksum, len(original)-1, wifi): pass
            self.assertEqual(list((root / 'cache/personalized').iterdir()), [])

    def test_setup_wifi_uses_ephemeral_raw_and_redacts_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image, original, checksum = self.make_wifi_fixture(root)
            disk = {'device': '/dev/example', 'name': 'Fixture', 'size': len(original), 'identity': 'serial'}
            wifi = {'ssid': 'IEEE', 'password': 'password', 'hidden': False, 'country': 'JP'}
            request = {'command': 'flash', 'image': str(image), 'sha256': checksum, 'disk': disk, 'wifi': wifi}
            def verify_write(raw, selected, notify, digest, **kwargs):
                self.assertTrue(raw.exists()); self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), digest)
                self.assertEqual(kwargs, {'setup': True, 'personalized': True})
                self.assertNotIn(b'password', raw.read_bytes())
            out = io.StringIO()
            with patch.object(flasher, 'WIFI_IMAGE_SIZE', len(original)), patch.object(flasher, 'cache_directory', return_value=root / 'cache'), patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(request)+'\n')), patch.object(flasher.sys, 'stdout', out), patch.object(flasher, 'disks', return_value=[disk]), patch.object(flasher, 'write_card', side_effect=verify_write):
                self.assertEqual(flasher.setup_command(), 0)
            self.assertEqual(json.loads(out.getvalue().splitlines()[-1]), {'success': True})
            self.assertNotIn('password', out.getvalue())
            self.assertEqual(list((root / 'cache/personalized').iterdir()), [])
            out = io.StringIO()
            with patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(request)+'\n')), patch.object(flasher.sys, 'stdout', out), patch.object(flasher, 'disks', return_value=[disk]), patch.object(flasher, 'personalized_image', side_effect=RuntimeError('password')):
                self.assertEqual(flasher.setup_command(), 1)
            self.assertNotIn('password', out.getvalue())
            out = io.StringIO()
            with patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(request)+'\n')), patch.object(flasher.sys, 'stdout', out), patch.object(flasher, 'disks', return_value=[disk]), patch.object(flasher, 'personalized_image', side_effect=ValueError('This image does not support Wi-Fi preparation.')):
                self.assertEqual(flasher.setup_command(), 1)
            self.assertIn('This image does not support Wi-Fi preparation.', out.getvalue())

    def test_personalized_write_bypasses_persistent_image_cache(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / 'custom.img'; raw.write_bytes(b'private')
            checksum = hashlib.sha256(raw.read_bytes()).hexdigest()
            selected = {'device': '/dev/example', 'identity': 'fixture', 'size': 100}
            def worker(command):
                job = Path(command[-1]); payload = json.loads(job.read_text())
                self.assertEqual(payload['image'], str(raw.resolve()))
                self.assertEqual(payload['sha256'], checksum)
                job.with_suffix('.log').write_text(json.dumps({'success': True}) + '\n')
                return Mock(poll=Mock(return_value=0), communicate=Mock(return_value=('', None)), returncode=0)
            with patch.object(flasher, 'validate_target'), patch.object(flasher, 'check_source_drive'), patch.object(flasher, 'prepare_setup_imager', return_value=('/engine', {})), patch.object(flasher, 'start_flash_worker', side_effect=worker), patch.object(flasher, 'stage_image') as stage:
                flasher._write_card(raw, selected, lambda _: None, checksum, True, str(root), True)
                stage.assert_not_called()

    def test_setup_reports_distinct_monotonic_flash_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'base.img'; image.write_bytes(b'base')
            disk = {'device': '/dev/example', 'name': 'Fixture', 'size': 4, 'identity': 'serial'}
            request = {'command': 'flash', 'image': str(image), 'sha256': hashlib.sha256(b'base').hexdigest(), 'disk': disk}
            messages = ['Checking image integrity before writing…', 'Waiting for system authorization…',
                        'Checking the image and selected drive…', 'Unmounting drive...', 'Cleaning disk...', 'Writing: 50 %', 'Verifying: 20 %',
                        'permission message', 'Writing: 99 %']
            def write(image, disk, notify, *args, **kwargs):
                for message in messages: notify(message)
            out = io.StringIO()
            with patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(request)+'\n')), patch.object(flasher.sys, 'stdout', out), patch.object(flasher, 'disks', return_value=[disk]), patch.object(flasher, 'write_card', side_effect=write):
                self.assertEqual(flasher.setup_command(), 0)
            events = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([event['phase'] for event in events[:-1]], ['preparing', 'preparing', 'authorizing', 'authorizing', 'media_preparing', 'media_preparing', 'writing', 'flash_verifying', 'flash_verifying', 'flash_verifying'])
            self.assertEqual(events[-1], {'success': True})

    def test_null_wifi_keeps_unmodified_setup_path(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'base.img'; image.write_bytes(b'base')
            disk = {'device': '/dev/example', 'name': 'Fixture', 'size': 4, 'identity': 'serial'}
            request = {'command': 'flash', 'image': str(image), 'sha256': hashlib.sha256(b'base').hexdigest(), 'disk': disk, 'wifi': None}
            with patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(request)+'\n')), patch.object(flasher.sys, 'stdout', io.StringIO()), patch.object(flasher, 'disks', return_value=[disk]), patch.object(flasher, 'write_card') as write:
                self.assertEqual(flasher.setup_command(), 0)
                self.assertEqual(write.call_args.kwargs, {'setup': True})

    def test_setup_protocol_refuses_changed_identity_capacity_and_bad_checksum(self):
        disk = {'device': '/dev/example', 'name': 'Fixture', 'size': 4, 'identity': 'serial'}
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'fixture.img.xz'
            image.write_bytes(flasher.lzma.compress(b'12345'))
            request = {'command': 'flash', 'image': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'disk': disk}
            def run(req, current):
                output = io.StringIO()
                with patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(req) + '\n')), patch.object(flasher.sys, 'stdout', output), patch.object(flasher, 'disks', return_value=current), patch.object(flasher, 'write_card') as write:
                    code = flasher.setup_command()
                write.assert_not_called()
                return code, output.getvalue()
            self.assertIn('does not fit', run(request, [disk])[1])
            self.assertIn('changed or disappeared', run(request, [dict(disk, identity='changed')])[1])
            self.assertIn('changed or disappeared', run(request, [dict(disk, identity=disk['device'])])[1])
            bad = dict(request, sha256='0' * 64)
            self.assertIn('checksum mismatch', run(bad, [disk])[1])

    def test_setup_rejects_ambiguous_or_missing_hardware_identity(self):
        disk = {'device': '/dev/a', 'name': 'Fixture', 'size': 5, 'identity': 'serial'}
        other = dict(disk, device='/dev/b')
        self.assertEqual(flasher.setup_media([disk, other]), [])
        self.assertEqual(flasher.setup_media([dict(disk, identity='/dev/a')]), [])
        self.assertEqual(flasher.setup_media([disk]), [disk])
        with patch.object(flasher, 'disks', return_value=[disk, other]):
            with self.assertRaisesRegex(ValueError, 'changed or disappeared'):
                flasher.validate_target(disk, setup=True)

    def test_setup_protocol_success_requires_engine_and_exact_identity(self):
        disk = {'device': '/dev/example', 'name': 'Fixture', 'size': 5, 'identity': 'serial'}
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'fixture.img'
            image.write_bytes(b'12345')
            request = {'command': 'flash', 'image': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'disk': disk}
            output = io.StringIO()
            with patch.object(flasher.sys, 'stdin', io.StringIO(json.dumps(request) + '\n')), patch.object(flasher.sys, 'stdout', output), patch.object(flasher, 'disks', return_value=[disk]), patch.object(flasher, 'write_card') as write:
                self.assertEqual(flasher.setup_command(), 0)
            self.assertTrue(write.call_args.kwargs['setup'])
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), {'success': True})
            with self.assertRaisesRegex(ValueError, 'stable hardware identity'):
                flasher.validate_target(dict(disk, identity=disk['device']), setup=True)

    def test_setup_engine_uses_fresh_pinned_archive_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / 'cached.AppImage'
            archive.write_bytes(b'vendor fixture')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            private = base / 'private'
            private.mkdir()
            stale = base / 'squashfs-root'
            stale.mkdir()
            (stale / 'AppRun').write_text('untrusted cached engine')
            def extract(command, **kwargs):
                self.assertEqual(command[-1], '--appimage-extract')
                root = Path(kwargs['cwd']) / 'squashfs-root'
                root.mkdir()
                (root / 'AppRun').write_text('trusted fixture')
            with patch.object(flasher.sys, 'platform', 'linux'), patch.object(flasher.platform, 'machine', return_value='x86_64'), patch.dict(flasher.IMAGER_ASSETS, {'linux-x86_64': ('fixture.AppImage', digest)}), patch.object(flasher, 'checked_download', return_value=archive), patch.object(flasher, 'run_command', side_effect=extract):
                engine, proof = flasher.prepare_setup_imager(private, lambda _: None)
                self.assertEqual(Path(engine).read_text(), 'trusted fixture')
                flasher.verify_setup_imager(engine, proof)
                Path(engine).write_text('changed')
                with self.assertRaisesRegex(ValueError, 'engine changed'):
                    flasher.verify_setup_imager(engine, proof)
                Path(engine).write_text('trusted fixture')
                (private / 'fixture.AppImage').write_bytes(b'bad')
                with self.assertRaisesRegex(ValueError, 'asset changed'):
                    flasher.verify_setup_imager(engine, proof)

    def test_setup_mac_derives_engine_from_mounted_verified_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / 'cached.dmg'
            archive.write_bytes(b'vendor fixture')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            private = base / 'private'
            private.mkdir()
            mount = base / 'mount'
            (mount / 'Vendor.app').mkdir(parents=True)
            def command(args, **kwargs):
                self.assertIs(kwargs.get('stdout'), subprocess.PIPE)
                self.assertIs(kwargs.get('stderr'), subprocess.PIPE)
                # Simulate verbose native tools; both streams stay captured.
                result = subprocess.run([sys.executable, '-c', "import sys; print('native stdout'); print('native stderr', file=sys.stderr)"], **kwargs)
                self.assertIn(b'native stdout', result.stdout)
                self.assertIn(b'native stderr', result.stderr)
                if args[0] == 'ditto':
                    target = Path(args[2]) / 'Contents/MacOS'
                    target.mkdir(parents=True)
                    (target / 'rpi-imager').write_text('vendor engine')
            plist = flasher.plistlib.dumps({'system-entities': [{'mount-point': str(mount)}]})
            with patch.object(flasher.sys, 'platform', 'darwin'), patch.dict(flasher.IMAGER_ASSETS, {'darwin': ('fixture.dmg', digest)}), patch.object(flasher, 'checked_download', return_value=archive), patch.object(flasher, 'command_output', return_value=plist), patch.object(flasher, 'run_command', side_effect=command) as commands:
                engine, proof = flasher.prepare_setup_imager(private, lambda _: None)
                flasher.verify_setup_imager(engine, proof)
                self.assertIs(flasher.command_output.call_args.kwargs['stderr'], subprocess.PIPE)
                self.assertEqual(Path(engine).read_text(), 'vendor engine')
                self.assertTrue(any(call.args[0][0] == 'hdiutil' and call.args[0][1] == 'detach' for call in commands.call_args_list))
                Path(engine).write_text('changed')
                with self.assertRaisesRegex(ValueError, 'engine changed'):
                    flasher.verify_setup_imager(engine, proof)

    def test_setup_worker_rejects_untrusted_engine_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'os.img'
            image.write_bytes(b'image')
            job = Path(directory) / 'job.json'
            job.write_text(json.dumps(dict(image=str(image), sha256=hashlib.sha256(b'image').hexdigest(), disk={'device': '/dev/mock'}, engine='/engine', setup=True, provenance={})))
            with patch.object(flasher, 'validate_target'), patch.object(flasher, 'check_source_drive'), patch.object(flasher, 'verify_setup_imager', side_effect=ValueError('engine changed')), patch.object(flasher, 'start_command') as launch:
                self.assertEqual(flasher.flash_worker(job), 1)
                launch.assert_not_called()
            self.assertIn('engine changed', job.with_suffix('.log').read_text())

    def test_setup_windows_uses_installer_signer_and_version_and_rechecks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / 'cached.exe'
            archive.write_bytes(b'installer fixture')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            private = base / 'private'
            private.mkdir()
            root = base / 'Program Files'
            engine = root / 'Raspberry Pi Ltd/Imager/rpi-imager.exe'
            engine.parent.mkdir(parents=True)
            engine.write_bytes(b'installed fixture')
            good = {'status': 'Valid', 'thumbprint': 'A' * 40, 'file': '2.0.12.0', 'product': '2.0.12'}
            signatures = {}
            def signature(path):
                return signatures.get(Path(path).name, good)
            with patch.object(flasher.sys, 'platform', 'win32'), patch.dict(flasher.IMAGER_ASSETS, {'win32': ('fixture.exe', digest)}), patch.object(flasher, 'checked_download', return_value=archive), patch.object(flasher, 'windows_program_roots', return_value=[root]), patch.object(flasher, 'windows_signature', side_effect=signature), patch.object(flasher, 'elevated') as elevate, patch.object(flasher, 'imager') as path_search:
                selected, proof = flasher.prepare_setup_imager(private, lambda _: None)
                self.assertEqual(Path(selected), engine.resolve())
                elevate.assert_not_called()
                path_search.assert_not_called()
                flasher.verify_setup_imager(selected, proof)
                signatures[engine.name] = dict(good, thumbprint='B' * 40)
                with self.assertRaisesRegex(ValueError, 'publisher'):
                    flasher.verify_setup_imager(selected, proof)
                signatures[engine.name] = dict(good, file='2.0.11.0')
                with self.assertRaisesRegex(ValueError, 'pinned version'):
                    flasher.verify_setup_imager(selected, proof)
                signatures[engine.name] = good
                engine.write_bytes(b'replaced engine')
                with self.assertRaisesRegex(ValueError, 'changed before launch'):
                    flasher.verify_setup_imager(selected, proof)
                (private / 'fixture.exe').write_bytes(b'bad installer')
                with self.assertRaisesRegex(ValueError, 'installer changed'):
                    flasher.verify_setup_imager(selected, proof)

    def test_setup_windows_installs_only_verified_installer_then_rediscovers(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / 'cached.exe'
            archive.write_bytes(b'verified installer')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            private = base / 'private'
            private.mkdir()
            process = Mock(returncode=0)
            process.communicate.return_value = ('', None)
            with patch.object(flasher.sys, 'platform', 'win32'), patch.dict(flasher.IMAGER_ASSETS, {'win32': ('fixture.exe', digest)}), patch.object(flasher, 'checked_download', return_value=archive), patch.object(flasher, 'windows_signature', return_value={'thumbprint': 'A' * 40}), patch.object(flasher, 'windows_find_engine', side_effect=[None, ('/program-files/imager.exe', 'f' * 64)]), patch.object(flasher, 'elevated', return_value=process) as elevate:
                _, proof = flasher.prepare_setup_imager(private, lambda _: None)
                self.assertEqual(elevate.call_args.args[0][0], str(private.resolve() / 'fixture.exe'))
                self.assertEqual(proof['engine_sha256'], 'f' * 64)

    def test_windows_program_files_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'Program Files'
            outside = base / 'outside'
            root.mkdir()
            outside.mkdir()
            (outside / 'rpi-imager.exe').write_text('not trusted')
            (root / 'Raspberry Pi Imager').symlink_to(outside, target_is_directory=True)
            with patch.object(flasher, 'windows_program_roots', return_value=[root]):
                self.assertNotIn(root / 'Raspberry Pi Imager/rpi-imager.exe', flasher.windows_imager_candidates())
                with self.assertRaisesRegex(ValueError, 'Program Files'):
                    flasher.windows_engine_metadata(outside / 'rpi-imager.exe', 'A' * 40)

    def test_windows_signature_rejects_invalid_status_and_missing_certificate(self):
        for response in ({'status': 'NotSigned', 'thumbprint': 'A' * 40}, {'status': 'Valid', 'thumbprint': None}):
            with patch.object(flasher, 'command_output', return_value=json.dumps(response)):
                with self.assertRaisesRegex(ValueError, 'signature'):
                    flasher.windows_signature(Path('installer.exe'))

    def test_image_checksum_required_and_matches_file(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'astro-bridge.img.xz'
            image.write_bytes(b'fixture')
            expected = hashlib.sha256(b'fixture').hexdigest()
            self.assertEqual(flasher.verified(image, expected), image.resolve())
            with self.assertRaises(ValueError):
                flasher.verified(image, '0' * 64)
            with self.assertRaises(ValueError):
                flasher.verified(Path(directory), expected)

    def test_stable_platform_assets_and_checksums_are_explicit_without_fallback(self):
        for selected,suffix,sums in [('alpine-standard','alpine-armhf-standard','ALPINE-STANDARD-SHA256SUMS'),('raspios','armhf','SHA256SUMS')]:
            with self.subTest(platform=selected),tempfile.TemporaryDirectory() as temporary:
                name=f'astro-bridge-0.6.1-{suffix}.img.xz';data=b'synthetic image';checksum=hashlib.sha256(data).hexdigest()
                prefix=f'https://github.com/{flasher.REPO}/releases/download/v0.6.1/'
                release={'tag_name':'v0.6.1','draft':False,'prerelease':False,'assets':[{'name':n,'browser_download_url':prefix+n} for n in (name,sums)]}
                calls=[]
                def request(url,**kwargs):
                    address=url.full_url if isinstance(url,flasher.urllib.request.Request) else url;calls.append(address)
                    return io.BytesIO(json.dumps(release).encode() if address.endswith('/latest') else (checksum+'  '+name+'\n').encode() if address==prefix+sums else data)
                with patch.object(flasher.urllib.request,'urlopen',side_effect=request):
                    result=flasher.latest(Path(temporary),selected)
                self.assertEqual(result.read_bytes(),data);self.assertEqual(calls[-2:],[prefix+sums,prefix+name])
                release['assets'].pop()
                with patch.object(flasher.urllib.request,'urlopen',side_effect=request):
                    with self.assertRaisesRegex(ValueError,'no fallback'):flasher.latest(Path(temporary),selected)
                self.assertEqual(calls[-1],f'https://api.github.com/repos/{flasher.REPO}/releases/latest')

    def test_private_unreleased_prerelease_or_untrusted_assets_never_download_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for code in (401,403,404,429):
                with patch.object(flasher.urllib.request,'urlopen',side_effect=urllib.error.HTTPError('https://example.invalid',code,'unavailable',{},None)) as network:
                    with self.assertRaisesRegex(ValueError,'reviewed local image'):flasher.latest(folder)
                    self.assertEqual(network.call_count,1)
            base={'tag_name':'v0.6.1','draft':False,'prerelease':False,'assets':[]}
            for release in (dict(base,draft=True),dict(base,prerelease=True),dict(base,tag_name='v0.6.1-preview'),dict(base,tag_name='v00.6.1'),dict(base,tag_name='v0.06.1'),dict(base,tag_name='v0.6.01'),base,dict(base,assets=[{'name':'x','browser_download_url':'https://evil.invalid'}]*2),[]):
                with patch.object(flasher.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(release).encode())) as network:
                    with self.assertRaises(ValueError):flasher.latest(folder)
                    self.assertEqual(network.call_count,1)
            self.assertEqual(list(folder.iterdir()),[])
        with self.assertRaises(ValueError):flasher.latest(Path('/not-used'),'unknown-platform')

    def test_local_standard_checksum_file_is_strict_and_can_be_verified_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);image=folder/'astro-bridge-0.6.1-alpine-armhf-standard.img.xz';image.write_bytes(b'fixture')
            expected=hashlib.sha256(b'fixture').hexdigest();sums=folder/'ALPINE-STANDARD-SHA256SUMS'
            sums.write_text(expected+'  '+image.name+'\n')
            with patch.object(flasher.urllib.request,'urlopen') as network:
                self.assertEqual(flasher.verified(image,flasher.checksum_file(sums,image.name)),image.resolve());network.assert_not_called()
            result=__import__('subprocess').run([__import__('sys').executable,str(Path(flasher.__file__)), '--image',str(image),'--checksum-file',str(sums),'--verify-only'],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('Verified image:',result.stdout)
            for data in ('bad  '+image.name,expected+'  other.img',sums.read_text()*2,'x'*65537):
                with self.assertRaises(ValueError):flasher.checksum_for(data,image.name)
            with self.assertRaisesRegex(ValueError,'64-digit'):flasher.verified(image,'short')

    def test_missing_imager_is_reported_without_disk_access(self):
        with patch.object(flasher.shutil, 'which', return_value=None), patch.object(flasher.Path, 'is_file', return_value=False):
            self.assertIsNone(flasher.imager())

    def test_hash_mismatch_does_not_cache_vendor_binary(self):
        import io
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'engine'
            response = io.BytesIO(b'wrong bytes')
            response.headers = {}
            with patch.object(flasher.urllib.request, 'urlopen', return_value=response):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    flasher.checked_download('https://example.invalid/engine', target, '0' * 64)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix('.partial').exists())

    def test_pinned_download_is_cached_only_after_verification(self):
        import io
        data = b'official fixture'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'engine'
            response = io.BytesIO(data)
            response.headers = {'Content-Length': str(len(data))}
            with patch.object(flasher.urllib.request, 'urlopen', return_value=response):
                flasher.checked_download('https://example.invalid/engine', target, hashlib.sha256(data).hexdigest())
            with patch.object(flasher.urllib.request, 'urlopen') as network:
                flasher.checked_download('https://example.invalid/engine', target, hashlib.sha256(data).hexdigest())
                network.assert_not_called()

    def test_target_replaced_after_selection_is_rejected(self):
        selected = dict(device='/dev/disk9', name='SD', size=32000, identity='reader-A')
        with patch.object(flasher, 'disks', return_value=[dict(selected, identity='reader-B')]):
            with self.assertRaisesRegex(ValueError, 'changed or disappeared'):
                flasher.validate_target(selected)

    def test_linux_filters_system_internal_readonly_and_partitions(self):
        rows = [
            dict(name='/dev/sda', type='disk', rm=False, hotplug=True, ro=False, size=100),
            dict(name='/dev/sdb', type='disk', rm=True, ro=False, size=100, children=[dict(mountpoints=['/'])]),
            dict(name='/dev/sdc', type='disk', rm=True, ro=True, size=100),
            dict(name='/dev/sdd1', type='part', rm=True, ro=False, size=100),
            dict(name='/dev/sde', type='disk', rm=True, ro=False, size=32000, model='SD', serial='123'),
        ]
        with patch.object(flasher.sys, 'platform', 'linux'), patch.object(flasher, 'run_json', return_value={'blockdevices': rows}):
            self.assertEqual([disk['device'] for disk in flasher.disks()], ['/dev/sde'])

    def test_windows_excludes_system_disks_in_enumeration_query(self):
        fixture = '{"Number":4,"FriendlyName":"SD","Size":32000,"UniqueId":"reader"}'
        with patch.object(flasher.sys, 'platform', 'win32'), patch.object(flasher.subprocess, 'check_output', return_value=fixture) as call:
            self.assertEqual(flasher.disks()[0]['device'], r'\\.\PhysicalDrive4')
            self.assertIn('-not $_.IsSystem', call.call_args.args[0][-1])
            self.assertIn('-not $_.IsBoot', call.call_args.args[0][-1])
            self.assertIn("$_.MediaType -like '*Removable*'", call.call_args.args[0][-1])

    def test_mac_excludes_external_boot_store_and_fixed_drives(self):
        import plistlib
        def output(command, **kwargs):
            if command[-1] == '/':
                result = dict(ParentWholeDisk='disk8', APFSPhysicalStores=[dict(APFSPhysicalStore='disk7s2')])
            else:
                def media(name, removable=True):
                    return {'BSD Name': name, 'Whole': True, 'Writable': True,
                            'Removable': removable, 'Size': 32000, 'IORegistryEntryID': name}
                result = [
                    {'Protocol Characteristics': {'Physical Interconnect': 'USB'},
                     'IORegistryEntryChildren': [media('disk7'), media('disk9', False), media('disk10')]},
                    {'Protocol Characteristics': {'Physical Interconnect': 'Virtual Interface'},
                     'IORegistryEntryChildren': [media('disk11')]},
                ]
            return plistlib.dumps(result)
        with patch.object(flasher.sys, 'platform', 'darwin'), patch.object(flasher.subprocess, 'check_output', side_effect=output):
            self.assertEqual([disk['device'] for disk in flasher.disks()], ['/dev/disk10'])

    def test_image_on_target_card_is_rejected(self):
        with patch.object(flasher.sys, 'platform', 'linux'), patch.object(flasher.subprocess, 'check_output', return_value='Filesystem blocks used available capacity mounted\n/dev/sdz1 10 1 9 10% /media/card\n'):
            with self.assertRaisesRegex(ValueError, 'Move the image'):
                flasher.check_source_drive(Path('/media/card/os.img'), {'device': '/dev/sdz'})

    def test_engine_keeps_readback_verification_enabled(self):
        with patch.object(flasher.sys, 'platform', 'darwin'):
            command = flasher.engine_command('/engine', Path('/os.img.xz'), '/dev/disk99')
        self.assertIn('--cli', command)
        self.assertNotIn('--enable-writing-system-drives', command)
        self.assertNotIn('--disable-verify', command)
        self.assertEqual(command[-1], '/dev/disk99')

    def test_privileged_worker_rejects_changed_image_without_starting_writer(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'os.img'
            image.write_bytes(b'changed')
            job = Path(directory) / 'job.json'
            job.write_text(json.dumps(dict(image=str(image), sha256='0'*64, disk={'device': '/dev/mock'}, engine='/engine')))
            with patch.object(flasher.subprocess, 'Popen') as writer:
                self.assertEqual(flasher.flash_worker(job), 1)
                writer.assert_not_called()
            self.assertIn('checksum mismatch', job.with_suffix('.log').read_text())

    def test_worker_never_reports_success_when_engine_fails(self):
        import io
        import json
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'os.img'
            image.write_bytes(b'image')
            job = Path(directory) / 'job.json'
            job.write_text(json.dumps(dict(image=str(image), sha256=hashlib.sha256(b'image').hexdigest(), disk={'device': '/dev/mock'}, engine='/engine')))
            process = Mock(stdout=io.StringIO('Writing: 50 %\nError: reader disconnected\n'))
            process.wait.return_value = 1
            with patch.object(flasher, 'validate_target'), patch.object(flasher, 'check_source_drive'), patch.object(flasher.subprocess, 'Popen', return_value=process):
                self.assertEqual(flasher.flash_worker(job), 1)
            self.assertNotIn('"success": true', job.with_suffix('.log').read_text())
            self.assertIn('reader disconnected', job.with_suffix('.log').read_text())

    def test_linux_cli_only_engine_does_not_receive_gui_switches(self):
        with patch.object(flasher.sys, 'platform', 'linux'):
            self.assertEqual(flasher.engine_command('/AppRun', Path('/os.img'), '/dev/sdz'),
                             ['/AppRun', str(Path('/os.img')), '/dev/sdz'])

    def test_windows_download_uses_official_inno_silent_install_options(self):
        from unittest.mock import Mock
        process = Mock(returncode=0)
        process.communicate.return_value = ('', None)
        with patch.object(flasher.sys, 'platform', 'win32'), patch.object(flasher, 'checked_download', return_value=Path('/vendor.exe')), patch.object(flasher, 'imager', side_effect=[None, '/installed/rpi-imager.exe']), patch.object(flasher, 'elevated', return_value=process) as elevate:
            self.assertEqual(flasher.ensure_imager(), '/installed/rpi-imager.exe')
            self.assertIn('/VERYSILENT', elevate.call_args.args[0])
            self.assertIn('/NORESTART', elevate.call_args.args[0])

    def test_worker_rechecks_identity_after_authorization(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'os.img'
            image.write_bytes(b'image')
            job = Path(directory) / 'job.json'
            job.write_text(json.dumps(dict(image=str(image), sha256=hashlib.sha256(b'image').hexdigest(), disk={'device': '/dev/mock'}, engine='/engine')))
            with patch.object(flasher, 'validate_target', side_effect=ValueError('Drive changed')), patch.object(flasher.subprocess, 'Popen') as writer:
                self.assertEqual(flasher.flash_worker(job), 1)
                writer.assert_not_called()
            self.assertIn('Drive changed', job.with_suffix('.log').read_text())

    def test_privileged_source_is_staged_outside_documents_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Documents' / 'os.img.xz'
            source.parent.mkdir()
            source.write_bytes(b'image fixture')
            checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.object(flasher, 'cache_directory', return_value=root / 'Cache'):
                staged = flasher.stage_image(source, checksum)
                self.assertTrue(staged.is_relative_to((root / 'Cache').resolve()))
                self.assertEqual(staged.read_bytes(), source.read_bytes())
                self.assertNotEqual(staged, source)
                staged.write_bytes(b'damaged cache')
                self.assertEqual(flasher.stage_image(source, checksum).read_bytes(), b'image fixture')

    def test_staging_rejects_a_source_changed_since_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'os.img'
            source.write_bytes(b'changed')
            checksum = hashlib.sha256(b'original').hexdigest()
            with patch.object(flasher, 'cache_directory', return_value=root / 'Cache'):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    flasher.stage_image(source, checksum)
            self.assertEqual(list((root / 'Cache/images').iterdir()), [])

    def test_macos_worker_runs_in_user_session_without_outer_elevation(self):
        from unittest.mock import Mock
        process = Mock()
        with patch.object(flasher.sys, 'platform', 'darwin'), patch.object(flasher, 'start_command', return_value=process) as start, patch.object(flasher, 'elevated') as elevation:
            self.assertIs(flasher.start_flash_worker(['/flasher', '--flash-worker', '/job.json']), process)
            elevation.assert_not_called()
            self.assertEqual(start.call_args.args[0], ['/flasher', '--flash-worker', '/job.json'])

    def test_other_platforms_still_elevate_the_worker(self):
        for platform in ['linux', 'win32']:
            with patch.object(flasher.sys, 'platform', platform), patch.object(flasher, 'elevated') as elevation:
                flasher.start_flash_worker(['/flasher', '--flash-worker', '/job.json'])
                elevation.assert_called_once()

    def test_authorization_failure_terminates_stuck_engine_and_explains_it(self):
        import io
        import json
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'os.img'
            image.write_bytes(b'image')
            job = Path(directory) / 'job.json'
            job.write_text(json.dumps(dict(image=str(image), sha256=hashlib.sha256(b'image').hexdigest(), disk={'device': '/dev/mock'}, engine='/engine')))
            process = Mock(stdout=io.StringIO('Authorization cancelled by user\n'))
            with patch.object(flasher, 'validate_target'), patch.object(flasher, 'check_source_drive'), patch.object(flasher, 'start_command', return_value=process):
                self.assertEqual(flasher.flash_worker(job), 1)
            process.terminate.assert_called_once()
            self.assertIn('not necessarily a cancellation', job.with_suffix('.log').read_text())
