"""Portable flash preparation: never write a disk or launch a GUI in tests."""
import hashlib
import io
import json
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
