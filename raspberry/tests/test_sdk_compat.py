"""Synthetic bytes/fake calls only: no vendor download, ELF execution or USB access."""
import ctypes
import importlib.util
import io
import json
import pathlib
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


elf = load('elf_test', 'elf-inspect.py')
probe = load('probe_test', 'sdk-probe.py')
runner = load('runner_test', 'sdk-compat-run.py')


def fixture(bits=32, order='<', version_name='GLIBC_2.4', definition=False,
            export_name='exported', export_binding=1, export_visibility=0):
    """Build an ELF from bytes, with genuine section links and GNU symbol versions."""
    pack = lambda fmt, *args: struct.pack(order + fmt, *args)
    names = b'\0.shstrtab\0.dynstr\0.dynamic\0.dynsym\0.gnu.version\0.gnu.version_r\0.ARM.attributes\0'
    strings = b'\0libc.so.6\0memcpy\0' + version_name.encode() + b'\0exported\0weak_missing\0'
    name_offset = lambda name: names.index(name.encode() + b'\0')
    string_offset = lambda name: strings.index(name.encode() + b'\0')
    dynamic = pack('iI' if bits == 32 else 'qQ', 1, string_offset('libc.so.6'))
    dynamic += pack('iI' if bits == 32 else 'qQ', 0, 0)
    def symbol(name, shndx, binding=1, visibility=0):
        offset = string_offset(name) if name else 0
        if bits == 32:
            return pack('IIIBBH', offset, 0, 0, binding << 4, visibility, shndx)
        return pack('IBBHQQ', offset, binding << 4, visibility, shndx, 0, 0)
    symbols = (symbol('', 0, 0) + symbol('memcpy', 0)
               + symbol(export_name, 1, export_binding, export_visibility) + symbol('weak_missing', 0, 2))
    versions = pack('HHHH', 0, 1 if definition else 2, 2 if definition else 1, 1)
    need = pack('HHIII', 1, 1, string_offset('libc.so.6'), 16, 0)
    need += pack('IHHII', 0, 0, 2, string_offset(version_name), 0)
    if definition:
        need = pack('HHHHIII', 1, 0, 2, 1, 0, 20, 0) + pack('II', string_offset(version_name), 0)
    file_attrs = b'\x01' + pack('I', 9) + b'\x06\x07\x1c\x01'
    attrs = b'A' + pack('I', 4 + 6 + len(file_attrs)) + b'aeabi\0' + file_attrs
    sections = [(b'', 0, 0, 0), (names, 3, 0, 0), (strings, 3, 0, 0),
                (dynamic, 6, 2, 8 if bits == 32 else 16),
                (symbols, 11, 2, 16 if bits == 32 else 24),
                (versions, 0x6fffffff, 4, 2), (need, 0x6ffffffd if definition else 0x6ffffffe, 2, 0),
                (attrs, 0x70000003, 0, 0)]
    labels = ['', '.shstrtab', '.dynstr', '.dynamic', '.dynsym', '.gnu.version', '.gnu.version_r', '.ARM.attributes']
    header_size, section_size = (52, 40) if bits == 32 else (64, 64)
    body, entries = bytearray(header_size), []
    for i, (data, kind, link, size) in enumerate(sections):
        offset = len(body)
        body.extend(data)
        fields = (name_offset(labels[i]) if i else 0, kind, 0, 0, offset, len(data), link, 0, 1, size)
        entries.append(pack('IIIIIIIIII' if bits == 32 else 'IIQQQQIIQQ', *fields))
    table = len(body)
    body.extend(b''.join(entries))
    ident = b'\x7fELF' + bytes((1 if bits == 32 else 2, 1 if order == '<' else 2, 1)) + b'\0' * 9
    fields = (3, 40 if bits == 32 else 183, 1, 0, 0, table, 0, header_size, 0, 0, section_size, len(sections), 1)
    body[:header_size] = ident + pack('HHIIIIIHHHHHH' if bits == 32 else 'HHIQQQIHHHHHH', *fields)
    return bytes(body)


class ELFTests(unittest.TestCase):
    def test_all_encodings_and_dynamic_versions(self):
        for bits in (32, 64):
            for order in ('<', '>'):
                with self.subTest(bits=bits, order=order):
                    info = elf.ELF(fixture(bits, order)).inspect()
                    self.assertEqual(info['class'], bits)
                    self.assertEqual(info['needed'], ['libc.so.6'])
                    self.assertEqual(info['arm_attributes'], [{'Tag_CPU_arch': 7, 'Tag_ABI_VFP_args': 1}])
                    self.assertEqual(info['undefined_by_version']['GLIBC_2.4'], [{'name': 'memcpy', 'weak': False}])
                    self.assertEqual(info['undefined_by_version']['unversioned'], [{'name': 'weak_missing', 'weak': True}])
                    self.assertEqual(info['exports'], [{'name': 'exported', 'version': 'unversioned'}])

    def test_cpp_version_groups_and_definitions(self):
        for version in ('GLIBCXX_3.4.21', 'CXXABI_1.3.9'):
            info = elf.ELF(fixture(version_name=version)).inspect()
            self.assertEqual(info['undefined_by_version'][version], [{'name': 'memcpy', 'weak': False}])
            info = elf.ELF(fixture(version_name=version, definition=True)).inspect()
            self.assertEqual(info['exports'], [{'name': 'exported', 'version': version}])

    def test_all_truncations_fail_cleanly(self):
        data = fixture()
        for size in range(len(data)):
            with self.subTest(size=size), self.assertRaises(elf.ELFError):
                elf.ELF(data[:size]).inspect()

    def test_rejects_encoding_tables_strings_and_links(self):
        for offset, patch in [(4, b'\x03'), (5, b'\x00'), (32, b'\xff'*4), (48, b'\xff\xff'), (50, b'\xff\xff')]:
            data = bytearray(fixture())
            data[offset:offset + len(patch)] = patch
            with self.assertRaises(elf.ELFError):
                elf.ELF(data).inspect()
        with self.assertRaises(elf.ELFError):
            elf.ELF.string(b'abc', 0)

    def test_attribute_bounds_and_unknown_vendor(self):
        self.assertEqual(elf.arm_attributes(b'A\x08\0\0\0xyz\0', '<'), [])
        for data in (b'', b'B', b'A\xff\0\0\0a\0', b'A\x05\0\0\0a'):
            with self.assertRaises(elf.ELFError):
                elf.arm_attributes(data, '<')

    def test_gnu_unique_definition_satisfies_ordinary_demand_in_provider_scope(self):
        for bits in (32, 64):
            consumer = elf.ELF(fixture(bits)).inspect()
            for binding, visibility, exported in ((10, 0, True), (10, 3, True),
                                                   (10, 1, False), (10, 2, False), (11, 0, False)):
                with self.subTest(bits=bits, binding=binding, visibility=visibility):
                    provider = elf.ELF(fixture(bits, definition=True, export_name='memcpy',
                                              export_binding=binding, export_visibility=visibility)).inspect()
                    provider.update(path='/lib/synthetic', aliases=['libc.so.6', 'libgcompat.so.0'])
                    scope, missing = runner.provider_scope(consumer, [provider], {'needed': []})
                    self.assertEqual(missing, [])
                    result = runner.symbols_required(consumer, scope)
                    missing_names = {entry['name'] for entry in result['missing_by_name']}
                    self.assertEqual('memcpy' not in missing_names, exported)
                    self.assertEqual(result['version_not_exported'], [])
                    weak = next(entry for entry in result['missing_by_name'] if entry['name'] == 'weak_missing')
                    self.assertTrue(weak['weak'])

    def test_provider_scope_excludes_unrelated_installed_exports(self):
        providers = [
            {'path': '/lib/musl', 'aliases': ['libc.so.6', 'libc.musl.so'], 'needed': []},
            {'path': '/lib/gcompat', 'aliases': ['libgcompat.so.0'], 'needed': ['libc.musl.so']},
            {'path': '/lib/unrelated', 'aliases': ['unrelated.so'], 'needed': []}]
        scope, missing = runner.provider_scope({'needed': ['libc.so.6', 'missing.so']}, providers, {'needed': []})
        self.assertEqual({p['path'] for p in scope}, {'/lib/musl', '/lib/gcompat'})
        self.assertEqual(missing, ['missing.so'])

    def test_static_missing_names_does_not_claim_version_compatibility(self):
        info = elf.ELF(fixture()).inspect()
        providers = [{'exports': [{'name': 'memcpy', 'version': 'unversioned'}]}]
        result = runner.symbols_required(info, providers)
        self.assertEqual(result['missing_by_name'][0]['name'], 'weak_missing')
        self.assertTrue(result['missing_by_name'][0]['weak'])
        self.assertEqual(result['version_not_exported'][0]['name'], 'memcpy')


def c_version(value):
    return ctypes.cast(ctypes.create_string_buffer(value), ctypes.POINTER(ctypes.c_ubyte))


class ProbeTests(unittest.TestCase):
    def test_zwo_exact_allowlist_and_signature(self):
        library = mock.Mock(spec=['ASIGetSDKVersion', 'ASIGetNumOfConnectedCameras'])
        library.ASIGetSDKVersion.return_value = c_version(b'1.41')
        library.ASIGetNumOfConnectedCameras.return_value = 0
        self.assertEqual(probe.call_api(library, 'zwo'), {'version': '1.41', 'cameras': 0})
        self.assertEqual([x[0] for x in library.method_calls], ['ASIGetSDKVersion', 'ASIGetNumOfConnectedCameras'])
        self.assertIs(library.ASIGetSDKVersion.restype, ctypes.POINTER(ctypes.c_ubyte))
        self.assertEqual(library.ASIGetNumOfConnectedCameras.argtypes, [])

    def test_qhy_release_on_scan_failure_and_failed_init(self):
        library = mock.Mock(spec=['InitQHYCCDResource', 'ScanQHYCCD', 'ReleaseQHYCCDResource'])
        library.InitQHYCCDResource.return_value = 0
        library.ScanQHYCCD.side_effect = ValueError('fake')
        with self.assertRaises(ValueError):
            probe.call_api(library, 'qhy')
        library.ReleaseQHYCCDResource.assert_called_once_with()
        library.reset_mock()
        library.InitQHYCCDResource.return_value = 1
        self.assertIsNone(probe.call_api(library, 'qhy')['cameras'])
        library.ScanQHYCCD.assert_not_called()
        library.ReleaseQHYCCDResource.assert_not_called()

    def test_successful_qhy_lifecycle(self):
        library = mock.Mock(spec=['InitQHYCCDResource', 'ScanQHYCCD', 'ReleaseQHYCCDResource'])
        for name in ['InitQHYCCDResource', 'ScanQHYCCD', 'ReleaseQHYCCDResource']:
            getattr(library, name).return_value = 0
        self.assertEqual(probe.call_api(library, 'qhy'), {'init_result': 0, 'cameras': 0, 'release_result': 0})
        self.assertEqual([x[0] for x in library.method_calls], ['InitQHYCCDResource', 'ScanQHYCCD', 'ReleaseQHYCCDResource'])

    def test_isolated_strategy_commands(self):
        with mock.patch.dict(probe.os.environ, {'LD_PRELOAD': 'untrusted', 'LD_LIBRARY_PATH': 'untrusted'}):
            for machine, loader in [('armv6l', '/lib/ld-linux-armhf.so.3'), ('aarch64', '/lib/ld-linux-aarch64.so.1')]:
                for strategy in probe.STRATEGIES:
                    argv, env = probe.command('/tmp/lib.so', 'zwo', strategy, machine)
                    self.assertNotIn('LD_LIBRARY_PATH', env)
                    self.assertEqual(env.get('LD_PRELOAD'), '/lib/libgcompat.so.0' if strategy == 'preload' else None)
                    self.assertEqual(argv[0], loader if strategy == 'loader' else probe.sys.executable)
                    self.assertEqual(argv[-1], '--child')

    def test_crash_timeout_and_invalid_output_are_structured(self):
        run = mock.Mock(side_effect=[subprocess.CompletedProcess([], -11, b'ignored'),
                                     subprocess.TimeoutExpired([], 30), subprocess.CompletedProcess([], 0, b'not-json')])
        result = probe.probe('/tmp/fake.so', 'zwo', run=run)
        self.assertEqual([r['error'] for r in result['results']], ['child_failed', 'timeout', 'invalid_child_result'])
        self.assertEqual(result['results'][0]['returncode'], -11)
        self.assertEqual(run.call_count, 3)

    def test_no_vendor_load_outside_disposable_container(self):
        with mock.patch.dict(probe.os.environ, {}, clear=True), mock.patch.object(probe.ctypes, 'CDLL') as cdll:
            self.assertEqual(probe.child('/not-read', 'zwo')['error'], 'disposable_no_usb_container_required')
            cdll.assert_not_called()

    def test_child_acceptance_requires_zero_cameras(self):
        library = mock.Mock(spec=['ASIGetSDKVersion', 'ASIGetNumOfConnectedCameras'])
        library.ASIGetSDKVersion.return_value = c_version(b'1.41')
        with mock.patch.object(probe.sys, 'platform', 'linux'), \
                mock.patch.dict(probe.os.environ, {'SDK_PROBE_CONTAINER': '1'}), \
                mock.patch.object(probe.pathlib.Path, 'exists', return_value=False), \
                mock.patch.object(probe.ctypes, 'CDLL', return_value=library) as cdll, \
                mock.patch.object(probe, 'rss', side_effect=[100, 200, 100, 200]):
            for count in (0, 1):
                library.ASIGetNumOfConnectedCameras.return_value = count
                result = probe.child('/tmp/synthetic.so', 'zwo')
                self.assertEqual(result['ok'], count == 0)
                self.assertEqual(result['rss_delta_bytes'], 100)
                self.assertGreaterEqual(result['load_ms'], 0)
            self.assertEqual(cdll.call_count, 2)

    def test_header_version_format_preserves_decimal_fields(self):
        library = mock.Mock(spec=['ASIGetSDKVersion', 'ASIGetNumOfConnectedCameras'])
        for value in (b'1, 13, 0503', b'1,41,0,0'):
            library.ASIGetSDKVersion.return_value = c_version(value)
            library.ASIGetNumOfConnectedCameras.return_value = 0
            self.assertEqual(probe.call_api(library, 'zwo'), {'version': value.decode(), 'cameras': 0})

    def test_version_rejects_payload_text(self):
        library = mock.Mock(spec=['ASIGetSDKVersion', 'ASIGetNumOfConnectedCameras'])
        for value in (b'', b'1, 41\nother', b'1, 2, private message', b'1, 2\xff',
                      b'https://example.invalid', b'1' * 129):
            library.ASIGetSDKVersion.return_value = c_version(value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_version'):
                probe.call_api(library, 'zwo')
        with self.assertRaisesRegex(ValueError, 'invalid_version'):
            probe.zwo_version(None)
        library.ASIGetNumOfConnectedCameras.assert_not_called()

    def test_unterminated_version_read_is_bounded(self):
        # Exactly 129 readable bytes. A read past the bound raises IndexError.
        with self.assertRaisesRegex(ValueError, 'invalid_version'):
            probe.zwo_version(b'1' * 129)

    def test_raw_loader_errors_not_logged(self):
        self.assertEqual(probe.load_error(OSError('private arbitrary data')),
                         {'error': 'load_failed', 'missing_symbol': None})
        self.assertEqual(probe.load_error(OSError('Error relocating lib.so: __foo: symbol not found'))['missing_symbol'], '__foo')


class ArchiveAndPolicyTests(unittest.TestCase):
    def test_exact_regular_member_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode='w:bz2') as tar:
                for name, content in [('../../escape', b'never'), ('lib/selected.so', b'synthetic')]:
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
            with zipfile.ZipFile(root / 'outer.zip', 'w') as outer:
                outer.writestr('nested.tar.bz2', buf.getvalue())
            runner.select_library(root / 'outer.zip', 'nested.tar.bz2', 'lib/selected.so', root / 'selected.so')
            self.assertEqual((root / 'selected.so').read_bytes(), b'synthetic')
            self.assertEqual(set(p.name for p in root.iterdir()), {'outer.zip', 'nested.tar.bz2', 'selected.so'})

    def test_rejects_library_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root, buf = pathlib.Path(d), io.BytesIO()
            with tarfile.open(fileobj=buf, mode='w:bz2') as tar:
                info = tarfile.TarInfo('lib.so')
                info.type, info.linkname = tarfile.SYMTYPE, '/etc/passwd'
                tar.addfile(info)
            with zipfile.ZipFile(root / 'outer.zip', 'w') as outer:
                outer.writestr('nested', buf.getvalue())
            with self.assertRaisesRegex(ValueError, 'invalid_library_member'):
                runner.select_library(root / 'outer.zip', 'nested', 'lib.so', root / 'result')

    def test_bounded_copy_and_redirect_allowlist(self):
        with self.assertRaisesRegex(ValueError, 'member_too_large'):
            runner.copy_bounded(io.BytesIO(b'abc'), io.BytesIO(), 2)
        for url in ['http://e.zwoastro.com/a', 'https://evil.example/a', 'https://user@e.zwoastro.com/a', 'https://e.zwoastro.com:444/a']:
            with self.assertRaises(ValueError):
                runner.check_url(url)
        runner.check_url('https://e.zwoastro.com/a')

    def test_digest_checked_before_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            response = io.BytesIO(b'fake-download')
            opener = mock.Mock()
            opener.open.return_value = response
            with mock.patch.object(runner.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
                    runner.download('https://e.zwoastro.com/a', pathlib.Path(d) / 'file', '0' * 64)

    def test_manifest_and_workflow_keep_vendor_inputs_private(self):
        manifest = json.loads((ROOT / 'tools/sdk/manifest.json').read_text())
        self.assertTrue(manifest['zwo']['enabled'])
        self.assertFalse(manifest['qhy']['enabled'])
        self.assertRegex(manifest['zwo']['sha256'], r'^[0-9a-f]{64}$')
        # The vendor SDK probe workflow is private research; public exports omit it.
        path = ROOT / '.github/workflows/glibc-compat.yml'
        if path.is_file():
            workflow = path.read_text()
            self.assertIn('workflow_dispatch:', workflow)
            for forbidden in ['upload-artifact', 'upload_to_draft', 'actions/cache', 'secrets.', 'pull_request:', 'push:', '--device', '--privileged']:
                self.assertNotIn(forbidden, workflow)
            self.assertIn('cache-image: false', workflow)
            self.assertIn('cpu: arm1176', workflow)
            self.assertIn('cpu: cortex-a53', workflow)
            self.assertIn('refs/heads/feature/p2-05b-alpine-glibc', workflow)
        # Staged platform test projects are plain copies without Git metadata.
        inside = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, capture_output=True, text=True, check=False)
        if inside.returncode or pathlib.Path(inside.stdout.strip()).resolve() != ROOT.resolve():
            return
        paths = ['vendor-sdk/archive.zip', 'sdk-downloads/archive.zip', 'libASICamera2.so.1.41', 'libqhyccd.so.26',
                 'ASI_linux_mac_SDK_V1.41.tar.bz2', 'sdk_linux_arm32_26.07.28.tar.gz']
        result = subprocess.run(['git', 'check-ignore', '--stdin'], input='\n'.join(paths) + '\n', text=True,
                                cwd=ROOT, capture_output=True, check=False)
        self.assertEqual(result.stdout.splitlines(), paths)
        tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertFalse(any(path.endswith(('.so', '.tar.bz2', '.tgz')) or 'libASICamera2.so' in path or 'libqhyccd.so' in path for path in tracked))


if __name__ == '__main__':
    unittest.main()
