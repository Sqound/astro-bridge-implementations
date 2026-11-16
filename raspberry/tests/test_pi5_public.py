"""Public Pi 5 recipe: only pinned public inputs, no key or Wi-Fi secret in the overlay."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import struct
import tempfile
import unittest
import unittest.mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PI5 = ROOT / 'image/pi5'
spec = importlib.util.spec_from_file_location('pi5_public', PI5 / 'build-public.py')
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def apk(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class PublicInputsTests(unittest.TestCase):
    def test_lock_pins_only_official_public_inputs(self):
        lock = json.loads((PI5 / 'public-inputs.json').read_text())
        self.assertTrue(lock['base']['url'].startswith('https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/'))
        names = [item['name'] for items in lock['packages'].values() for item in items]
        self.assertEqual(len(names), len(set(names)))
        for items in lock['packages'].values():
            for item in items:
                self.assertRegex(item['name'], public.NAME)
                self.assertIn(item['repository'], ('main', 'community'))
                self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
        runtime = {item['name'] for item in lock['packages']['runtime']}
        for prefix in ('dbus-openrc-', 'networkmanager-openrc-', 'avahi-'):
            self.assertTrue(any(name.startswith(prefix) for name in runtime), prefix)
        text = json.dumps(lock)
        self.assertNotRegex(text, r'zwoastro|ASICamera|qhy', 'no vendor SDK is a public input')

    def test_fetch_refuses_hosts_other_than_the_alpine_cdn(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                public.fetch('https://example.invalid/x.apk', Path(folder) / 'x.apk', '0' * 64)

    def test_wheel_becomes_app_archive_and_overlay_has_no_secret(self):
        build = 'a' * 64
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            wheel = base / 'astro_bridge_core-9.9.9-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('wifi_bridge/__init__.py', '__version__ = "9.9.9"\n')
                archive.writestr('wifi_bridge/build.py', f'BUILD_ID = "{build}"\n')
                archive.writestr('astro_bridge_core-9.9.9.dist-info/RECORD', '')
            lock = base / 'core.lock.json'
            lock.write_text(json.dumps({'asset': wheel.name, 'sha256': sha(wheel.read_bytes()), 'version': '9.9.9'}))
            version, found = public.application(wheel, lock, base / 'app.tar')
            self.assertEqual((version, found), ('9.9.9', build))
            with tarfile.open(base / 'app.tar') as archive:
                self.assertEqual(sorted(archive.getnames()), ['wifi_bridge/__init__.py', 'wifi_bridge/build.py'])
            folders = {}
            for group in ('runtime', 'display', 'camera'):
                folders[group] = base / group
                folders[group].mkdir()
            runtime = {'dbus-openrc-1.0-r0.apk': apk({'etc/init.d/dbus': b'#!/sbin/openrc-run\n'}),
                       'networkmanager-openrc-1.0-r0.apk': apk({'etc/init.d/networkmanager': b'#!/sbin/openrc-run\n'})}
            for name, data in runtime.items():
                (folders['runtime'] / name).write_bytes(data)
            manifest = ''.join(f'{sha(data)}  {name}\n' for name, data in runtime.items())
            (folders['runtime'] / 'astro-packages.sha256').write_text(manifest)
            prepare = (PI5 / 'prepare.py').read_text()
            for group, variable in (('display', 'DISPLAY_PACKAGES'), ('camera', 'CAMERA_PACKAGES')):
                block = prepare[prepare.index(variable + '={'):]
                for name in re.findall(r"'([^']+\.apk)'", block[:block.index('}')]):
                    (folders[group] / name).write_bytes(b'placeholder')
            tools = base / 'tools'
            tools.mkdir()
            for name in ('install-zwo.py', 'asi_archive.py'):
                (tools / name).write_text('# fixture\n')
            output = base / 'prepared'
            command = [sys.executable, str(PI5 / 'prepare.py'), '--public', '--app', str(base / 'app.tar'),
                       '--app-sha256', sha((base / 'app.tar').read_bytes()), '--build-id', build,
                       '--apks', str(folders['runtime']), '--manifest-sha256', sha(manifest.encode()), '--package-count', '2',
                       '--display-apks', str(folders['display']), '--camera-apks', str(folders['camera']),
                       '--owner-tools', str(tools), '--output', str(output), '--project', str(ROOT)]
            # Display and camera packages are pinned; placeholders must be refused.
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            for group, variable in (('display', 'DISPLAY_PACKAGES'), ('camera', 'CAMERA_PACKAGES')):
                pins = {}
                block = prepare[prepare.index(variable + '={'):]
                for name, digest in re.findall(r"'([^']+\.apk)':'([0-9a-f]{64})'", block[:block.index('}')]):
                    pins[name] = digest
                self.assertTrue(pins)
            # The overlay rules are checked on an overlay built with pins satisfied by patching.
            source = prepare.replace("  assert sha(a.display_apks/name)==digest\n", "").replace("  assert sha(a.camera_apks/name)==digest\n", "")
            patched = base / 'prepare-fixture.py'
            patched.write_text(source.replace("HERE=pathlib.Path(__file__).resolve().parent", f"HERE=pathlib.Path({str(PI5)!r})"))
            shutil_output = base / 'prepared2'
            command[1] = str(patched)
            command[command.index(str(output))] = str(shutil_output)
            subprocess.run(command, check=True, capture_output=True)
            with tarfile.open(shutil_output / 'astro-bridge.apkovl.tar.gz') as archive:
                names = archive.getnames()
                self.assertFalse([n for n in names if 'authorized_keys' in n or 'diagnostic' in n])
                for member in archive.getmembers():
                    if member.isfile():
                        data = archive.extractfile(member).read()
                        if member.name.endswith('.nmconnection') or 'wpa_supplicant' in member.name:
                            self.assertNotRegex(data, rb'(?m)^\s*(?:psk|wpa-psk)\s*=', member.name)
                        self.assertNotIn(b'PRIVATE' + b' KEY', data, member.name)
                self.assertEqual(archive.extractfile('etc/wifi-bridge/pi5-asi220-test').read(), b'Pi 5 attached camera profile\n')
                self.assertIn(b'host-name=astro-bridge', archive.extractfile('etc/avahi/avahi-daemon.conf').read())
                self.assertIn('usr/local/share/astro-bridge/zwo/install-zwo.py', names)


if __name__ == '__main__':
    unittest.main()


class ConsoleAndSplashTests(unittest.TestCase):
    def load(self, name, path):
        loader = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        return module

    def test_console_root_gets_initial_password_once_then_its_saved_hash(self):
        account = self.load('console_account', PI5 / 'console-account.py')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            state = base / 'state'
            state.mkdir()
            (state / 'bootstrap.json').write_text('{"password": "12345678"}')
            shadow = base / 'shadow'
            shadow.write_text('root:::0:::::\nbin:!::0:::::\n')
            calls = []

            def chpasswd(args, input, **kwargs):
                calls.append((args, input))
                user, value = input.rstrip('\n').split(':', 1)
                if '-e' not in args:
                    value = '$6$fixture$' + hashlib.sha256(value.encode()).hexdigest()
                lines = [user + ':' + value + ':0:::::' if l.startswith(user + ':') else l for l in shadow.read_text().splitlines()]
                shadow.write_text('\n'.join(lines) + '\n')
                return subprocess.CompletedProcess(args, 0)

            with unittest.mock.patch.object(account, 'STATE', state), unittest.mock.patch.object(account, 'RECORD', state / 'console-root.json'), \
                    unittest.mock.patch.object(account, 'SHADOW', shadow), unittest.mock.patch.object(account.subprocess, 'run', chpasswd):
                account.apply()
                first = json.loads((state / 'console-root.json').read_text())['hash']
                self.assertTrue(first.startswith('$6$'))
                self.assertEqual(calls[0][0], ['chpasswd', '-c', 'sha512'])
                shadow.write_text('root:::0:::::\n')  # a fresh diskless boot
                account.apply()
                self.assertIn('root:' + first + ':', shadow.read_text())
                self.assertEqual(calls[-1][0], ['chpasswd', '-e'])
                (state / 'console-root.json').write_text('{"version": 1, "hash": ""}')
                with self.assertRaises(ValueError):
                    account.apply()

    def test_overlay_services_lock_root_first_and_image_holds_no_password(self):
        prepare = (PI5 / 'prepare.py').read_text()
        self.assertIn("'boot':['astro-console-lock',", prepare)
        self.assertIn("'astro-console-account'", prepare)
        self.assertNotIn('etc/shadow', prepare)
        self.assertIn("printf 'root:!\\n' | chpasswd -e", (PI5 / 'astro-console-lock').read_text())

    def test_renderer_must_be_static_arm64(self):
        renderer = self.load('renderer', PI5.parent / 'splash/build-renderer.py')
        def elf(machine, interp):
            header = bytearray(64)
            header[:7] = b'\x7fELF\x02\x01\x01'
            struct.pack_into('<H', header, 18, machine)
            struct.pack_into('<Q', header, 32, 64)
            struct.pack_into('<HH', header, 54, 56, 1)
            program = bytearray(56)
            struct.pack_into('<I', program, 0, 3 if interp else 1)
            return bytes(header + program)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'renderer'
            path.write_bytes(elf(183, False))
            renderer.static_aarch64(path)
            for data in (elf(62, False), elf(183, True)):
                path.write_bytes(data)
                with self.assertRaises(SystemExit):
                    renderer.static_aarch64(path)
        svg = (PI5.parent / 'splash/boot-splash.svg').read_text()
        self.assertIn('>Astro Bridge is booting<', svg)
        self.assertNotRegex(re.sub(r'<!--.*?-->', '', svg, flags=re.S), r'(?i)plan ?astro')


class SplashToolchainTests(unittest.TestCase):
    def test_every_include_has_a_pinned_package(self):
        toolchain = json.loads((PI5 / 'public-inputs.json').read_text())['splash_toolchain']
        includes = re.findall(r'#include <([^>]+)>', (PI5.parent / 'splash/graphic-splash.c').read_text())
        self.assertTrue(includes)
        for header in includes:
            # Linux UAPI headers come from linux-headers; the rest from musl-dev.
            self.assertIn('linux-headers' if header.startswith(('linux/', 'asm/')) else 'musl-dev', toolchain, header)
        for version in toolchain.values():
            self.assertRegex(version, r'^[0-9][0-9A-Za-z._]*-r[0-9]+$')
