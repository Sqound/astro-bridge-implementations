"""Offline installer fixtures: only synthetic ELF headers, licence and archives."""
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from wifi_bridge.cameras import zwo_platform
from wifi_bridge.cameras.driver import CameraError
from wifi_bridge.cameras.zwo_asi import MODELS

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
spec = importlib.util.spec_from_file_location('zwo_setup', TOOLS/'install-zwo.py')
setup = importlib.util.module_from_spec(spec)
with patch.object(sys, 'path', [str(TOOLS), *sys.path]):
    spec.loader.exec_module(setup)


def elf(abi):
    (bits, machine), = [k for k, v in setup.ABI.items() if v == abi]
    data = bytearray(64)
    data[:7] = b'\x7fELF' + bytes([bits, 1, 1])
    data[18:20] = machine.to_bytes(2, 'little')
    data[36:40] = (0x5000400).to_bytes(4, 'little')
    return bytes(data)


VERSION = '9.98'
PREFIX = f'ASI_linux_mac_SDK_V{VERSION}'
NAME = f'{setup.LIBRARY}.{VERSION}'
LICENCE = b'Entire synthetic licence\n'


def known(abi='x64', binary=None):
    # A synthetic release added to the reviewed list, as a real one would be.
    library = hashlib.sha256(binary or elf(abi)).hexdigest()
    return patch.dict(setup.known_build.__globals__['KNOWN_BUILDS'], {VERSION: {
        'license_sha256': hashlib.sha256(LICENCE).hexdigest(), 'libraries': {abi: library}}})


def archive(path, abi='x64', binary=None, duplicate=False, link=False):
    nested = io.BytesIO()
    with tarfile.open(fileobj=nested, mode='w:bz2') as tar:
        entries = [('license.txt', LICENCE), (f'lib/{abi}/{NAME}', binary or elf(abi))]
        if duplicate:
            entries.append(entries[0])
        for name, raw in entries:
            member = tarfile.TarInfo(f'{PREFIX}/{name}')
            if link and name == 'license.txt':
                member.type, member.linkname = tarfile.SYMTYPE, '/outside'
                tar.addfile(member)
            else:
                member.size = len(raw)
                tar.addfile(member, io.BytesIO(raw))
    with zipfile.ZipFile(path, 'w') as outer:
        outer.writestr(f'ASI_Camera_SDK/{PREFIX}.tar.bz2', nested.getvalue())
    return 'f' * 64


class OwnerInstallTests(unittest.TestCase):
    def test_real_pin_consistency_and_rule_is_narrow(self):
        manifest = json.loads((ROOT/'tools/sdk/manifest.json').read_bytes())['zwo']
        self.assertEqual(setup.URL, manifest['url'])
        # One reviewed list of SDK builds, shared by the installer and the probes.
        self.assertEqual(setup.known_build.__globals__['KNOWN_BUILDS'], manifest['known_builds'])
        self.assertEqual(setup.INSTALL, zwo_platform.DIRECTORY)
        table = json.loads((ROOT/'cameras/zwo_asi.json').read_bytes())
        self.assertEqual({(0x03c3, p) for p in setup.PRODUCTS},
                         {(d['vendor_id'],d['product_id']) for d in table['usb']})
        self.assertEqual(set(MODELS), set(setup.PRODUCTS))
        rules = setup.rule_bytes().decode().splitlines()
        self.assertEqual(len(rules), len(setup.PRODUCTS))
        for line in rules:
            self.assertIn('MODE="0600"', line)
            self.assertIn('ATTR{idProduct}', line)
            for forbidden in ('RUN', 'SYSCTL', '0666', 'trigger'):
                self.assertNotIn(forbidden, line)

    def test_explicit_licence_exact_members_correct_abi_and_hash(self):
        for abi in setup.ABI.values():
            with self.subTest(abi=abi), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                fetch, accept = Mock(side_effect=lambda url, path: archive(path, abi)), Mock(return_value=True)
                with known(abi):
                    payload, version = setup.stage(temp, abi, setup.INSTALL, accept, fetch)
                self.assertEqual(version, VERSION)
                fetch.assert_called_once_with(setup.URL, temp/'input.zip')
                accept.assert_called_once_with(LICENCE, VERSION)
                self.assertEqual({p.name for p in payload.iterdir()}, {NAME, 'platform.json', 'license.txt', setup.RULE})
                record = json.loads((payload/'platform.json').read_bytes())
                self.assertEqual(record, {'path': str(setup.INSTALL/NAME),
                                          'sha256': hashlib.sha256(elf(abi)).hexdigest()})

    def test_bad_archive_or_abi_cannot_reach_acceptance_or_publish(self):
        for options in ({'link':True}, {'duplicate':True}, {'binary':elf('armv8')}, {'binary':b'abc'}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                temp, accept = Path(directory), Mock(return_value=True)
                with self.assertRaises(ValueError), known():
                    setup.stage(temp, 'x64', setup.INSTALL, accept,
                                lambda url,path:archive(path, **options))
                accept.assert_not_called()
                self.assertFalse((temp/'payload/platform.json').exists())
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            with self.assertRaisesRegex(ValueError, 'license_not_accepted'), known():
                setup.stage(temp, 'x64', setup.INSTALL, lambda licence, version: False,
                            lambda url,path:archive(path))
            self.assertFalse((temp/'payload/platform.json').exists())

    def test_platform_gates_without_reading_the_host(self):
        def check(target, abi, model, *, alpine=False, gcompat=False, label=False,
                  container_abi=None, diskless_link=False):
            def path(raw):
                p = Mock()
                p.exists.return_value = raw == '/etc/alpine-release' and alpine or raw == '/proc/device-tree/model' and bool(model)
                p.is_file.return_value = {'/lib/libgcompat.so.0': gcompat, '/etc/rpi-issue': True,
                                          '/etc/wifi-bridge/diskless': alpine}.get(raw, False)
                p.open.return_value = io.BytesIO(model.encode())
                p.read_text.return_value = ''
                return p
            with ExitStack() as stack:
                for key,value in [('safe_directory',None), ('persistent_state',True), ('binary_abi',abi),
                                  ('astro_data',label)]:
                    stack.enter_context(patch.object(setup,key,return_value=value))
                stack.enter_context(patch.object(setup,'Path',side_effect=path))
                link = stack.enter_context(patch.object(setup,'RULE_LINK'))
                link.is_symlink.return_value = diskless_link
                stack.enter_context(patch.object(setup.os,'readlink',return_value=str(setup.INSTALL/setup.RULE)))
                stack.enter_context(patch.object(setup.sys,'platform','linux'))
                stack.enter_context(patch.object(setup.os,'geteuid',return_value=0))
                stack.enter_context(patch.object(setup.shutil,'which',return_value='/fixture/udevadm'))
                stack.enter_context(patch.object(setup.shutil,'disk_usage',return_value=Mock(free=1024**3)))
                return setup.preflight(target,container_abi)
        pi = 'Raspberry Pi 3 Model B Rev 1.2'
        for model in ('Raspberry Pi Zero W', 'Raspberry Pi Zero 2 W','Raspberry Pi 4 Model B',''):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError,'pi_3b_required'):
                check('raspios','armv6',model)
        self.assertEqual(check('raspios','armv6',pi),'armv6')
        self.assertEqual(check('docker-linux','x64','',container_abi='x64'),'x64')
        with self.assertRaisesRegex(ValueError,'matching_native_container_abi_required'):
            check('docker-linux','x64','',container_abi='x86')
        with self.assertRaisesRegex(ValueError,'astro_data_mount_required'):
            check('alpine','armv6',pi,alpine=True)
        with self.assertRaisesRegex(ValueError,'gcompat_required'):
            check('alpine','armv6',pi,alpine=True,label=True)
        with self.assertRaisesRegex(ValueError,'diskless_rule_link_required'):
            check('alpine','armv6',pi,alpine=True,label=True,gcompat=True)
        self.assertEqual(check('alpine','armv8',pi,alpine=True,label=True,gcompat=True,diskless_link=True),'armv8')

    def test_mount_label_rw_fs_and_path_all_mandatory(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory)/'partition'
            device.touch()
            label = device.with_name('ASTRO_DATA')
            label.symlink_to(device)
            good = f'1 2 0:3 / {setup.STATE} rw,noatime - ext4 {device} rw'
            self.assertTrue(setup.astro_data(good, label))
            for bad in (good.replace('ext4','tmpfs'), good.replace('rw','ro'),
                        good.replace(str(device),'/dev/other'), good.replace(' / ',' /subdir '),
                        good.replace(str(setup.STATE), '/tmp')):
                self.assertFalse(setup.astro_data(bad, label))

    def test_state_rejects_ram_readonly_and_hidden_submount(self):
        root = '1 2 0:1 / / rw - ext4 /dev/drive rw'
        self.assertTrue(setup.persistent_state(root))
        self.assertFalse(setup.persistent_state(root.replace('ext4','overlay')))
        self.assertFalse(setup.persistent_state(root+'\n2 1 0:2 / /var/lib rw - tmpfs tmpfs rw'))
        self.assertFalse(setup.persistent_state(root.replace(' rw ', ' ro ')))
        self.assertFalse(setup.persistent_state(''))

    def test_atomic_publication_retry_foreign_rule_and_decline(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            install, link = state/'camera-drivers/zwo', state/'udev.rules'
            # No command, network, real platform or privilege. Exercise the same
            # staging, atomic publication, conflict and retry paths as the CLI.
            stage = setup.stage
            def fake_stage(temp, abi, destination, accept):
                return stage(temp, abi, destination, accept, lambda url,path:archive(path))
            with patch.object(setup, 'STATE', state), patch.object(setup,'INSTALL',install), \
                 patch.object(setup,'RULE_LINK',link), patch.object(setup,'preflight',return_value='x64'), \
                 patch.object(setup,'safe_directory'), patch.object(setup,'stage',side_effect=fake_stage), \
                 patch.object(setup.subprocess,'run') as cmd:
                stack = ExitStack()
                self.addCleanup(stack.close)
                stack.enter_context(known())
                with self.assertRaisesRegex(ValueError, 'license_not_accepted'):
                    setup.install('docker-linux', 'x64', lambda *_:False)
                self.assertEqual(list(state.iterdir()), [])
                link.write_text('existing unrelated rule')
                with self.assertRaisesRegex(ValueError, 'existing_rule_conflict'):
                    setup.install('docker-linux','x64',lambda *_:True)
                self.assertEqual(link.read_text(), 'existing unrelated rule')
                self.assertFalse(install.exists())
                link.unlink()
                setup.install('docker-linux', 'x64', lambda *_:True)
                self.assertEqual(link.read_bytes(), setup.rule_bytes())
                self.assertEqual(cmd.call_args.args[0], ['udevadm', 'control', '--reload-rules'])
                # Reconcile against the fixture owner's UID as though installed by root.
                original = Path.lstat
                def synthetic_owner(path):
                    data = list(original(path))
                    data[4] = 0
                    return os.stat_result(data)
                with patch.object(Path, 'lstat', synthetic_owner):
                    self.assertEqual(setup.install('docker-linux', 'x64', lambda *_:True), VERSION)
                    # A changed or older installation is replaced by the accepted build.
                    (install/NAME).write_bytes(b'changed')
                    setup.install('docker-linux', 'x64', lambda *_:True)
                    self.assertEqual((install/NAME).read_bytes(), elf('x64'))
                self.assertFalse(list(state.glob('.asi-setup-*')))

    def test_loading_requires_private_ancestry_regular_hash_and_exact_declared_path(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            # An installation made by an earlier installer keeps its versioned folder.
            drivers = temp/'drivers'
            dest = drivers/'zwo-1.41'
            dest.mkdir(parents=True)
            lib = dest/'libASICamera2.so.1.41'
            lib.write_bytes(elf('x64'))
            def declared(_):
                return zwo_platform.declared_path(drivers)
            digest = hashlib.sha256(lib.read_bytes()).hexdigest()
            original = Path.lstat
            def fixture(path):
                fields = list(original(path))
                fields[4] = 0
                # The fixture ancestor chain models trusted root-owned directories.
                if path in dest.parents:
                    fields[0] = stat.S_IFDIR | 0o755
                return os.stat_result(fields)
            record = dest/'platform.json'
            record.write_text(json.dumps({'path': str(lib), 'sha256': digest}))
            with patch.object(Path, 'lstat', fixture):
                self.assertEqual(declared(dest),lib)
                record.write_text(json.dumps({'path': '/tmp/untrusted', 'sha256':digest}))
                with self.assertRaisesRegex(CameraError, 'camera_driver_unavailable'):
                    declared(dest)
                record.write_text(json.dumps({'path': str(lib), 'sha256':digest}))
                lib.chmod(0o666)
                with self.assertRaises(CameraError):
                    declared(dest)
                lib.chmod(0o600)
                lib.write_bytes(b'changed')
                with self.assertRaises(CameraError):
                    declared(dest)
                lib.unlink()
                lib.symlink_to(record)
                with self.assertRaises(CameraError):
                    declared(dest)
