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
        text = json.dumps(lock['packages'])
        self.assertNotRegex(text, r'zwoastro|ASICamera|qhy', 'no vendor file among the Alpine packages')
        # No manufacturer input of any kind, ZWO included: every driver is downloaded on request.
        self.assertEqual(set(lock), {'format', 'alpine', 'architecture', 'base', 'packages', 'splash_toolchain'})
        self.assertNotRegex(json.dumps(lock), r'(?i)zwo|asi_?camera|qhy|player_?one|atik|moravian')
        for prefix in ('chrony-', 'chrony-openrc-', 'libcap2-', 'libseccomp-'):
            self.assertTrue(any(name.startswith(prefix) for name in runtime), prefix)

    def test_no_manufacturer_file_or_recipe_step_in_the_image_sources(self):
        """ZWO is a manufacturer like the others: nothing of it in the image or in how the image is built."""
        for name in ('build-public.py', 'prepare.py'):
            source = (PI5 / name).read_text()
            for forbidden in ('vendor_zwo', '--vendor-zwo', '--vendor-archive', '--owner-tools', 'owner-tools', 'install-zwo',
                              'asi_archive', 'libASICamera2', 'zwo-asi', 'astro-bridge-asi', 'accept_bundled', "'bundled'"):
                self.assertNotIn(forbidden, source, f'{name}: {forbidden}')
        self.assertFalse((PI5 / 'owner-tools').exists())
        # Neither the shipped driver catalog nor the seed readers know a build that comes with an image.
        catalog = json.loads((ROOT / 'wifi_bridge/static/drivers-catalog.json').read_text())
        for entry in catalog['drivers']:
            self.assertTrue(entry['builds'], entry['id'])
            for build in entry['builds']:
                self.assertEqual(build['source'], 'download', entry['id'])
                self.assertTrue(build['url'].startswith('https://'), entry['id'])
        for path in (ROOT / 'image/common/astro_seed.py', ROOT / 'image/pi5/firstboot.py', ROOT / 'image/alpine/persistence.py'):
            self.assertNotIn('bundled', path.read_text(), path.name)
        # The release action, where this tree has it (a staged platform test copy has none).
        for base in (ROOT / 'publication/implementations', ROOT.parent):
            action = base / '.github/actions/image-pi-5/action.yml'
            if action.is_file():
                self.assertNotRegex(action.read_text(), r'--owner-tools|--vendor')

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
                archive.writestr('wifi_bridge/static/drivers-catalog.json', (ROOT / 'wifi_bridge/static/drivers-catalog.json').read_text())
                archive.writestr('astro_bridge_core-9.9.9.dist-info/RECORD', '')
            lock = base / 'core.lock.json'
            lock.write_text(json.dumps({'asset': wheel.name, 'sha256': sha(wheel.read_bytes()), 'version': '9.9.9'}))
            version, found = public.application(wheel, lock, base / 'app.tar')
            self.assertEqual((version, found), ('9.9.9', build))
            with tarfile.open(base / 'app.tar') as archive:
                self.assertEqual(sorted(archive.getnames()), ['wifi_bridge/__init__.py', 'wifi_bridge/build.py', 'wifi_bridge/static/drivers-catalog.json'])
            folders = {}
            for group in ('runtime', 'display', 'camera'):
                folders[group] = base / group
                folders[group].mkdir()
            runtime = {'dbus-openrc-1.0-r0.apk': apk({'etc/init.d/dbus': b'#!/sbin/openrc-run\n'}),
                       'networkmanager-openrc-1.0-r0.apk': apk({'etc/init.d/networkmanager': b'#!/sbin/openrc-run\n'}),
                       'chrony-openrc-1.0-r0.apk': apk({'etc/init.d/chronyd': b'#!/sbin/openrc-run\n'})}
            for name, data in runtime.items():
                (folders['runtime'] / name).write_bytes(data)
            manifest = ''.join(f'{sha(data)}  {name}\n' for name, data in runtime.items())
            (folders['runtime'] / 'astro-packages.sha256').write_text(manifest)
            prepare = (PI5 / 'prepare.py').read_text()
            for group, variable in (('display', 'DISPLAY_PACKAGES'), ('camera', 'CAMERA_PACKAGES')):
                block = prepare[prepare.index(variable + '={'):]
                for name in re.findall(r"'([^']+\.apk)'", block[:block.index('}')]):
                    (folders[group] / name).write_bytes(b'placeholder')
            gphoto = base / 'gphoto'
            gphoto.mkdir()
            gphoto_pins = json.loads((PI5 / 'gphoto-packages.lock.json').read_text())['selected']
            for name in gphoto_pins:
                (gphoto / name).write_bytes(b'placeholder')
            output = base / 'prepared'
            command = [sys.executable, str(PI5 / 'prepare.py'), '--public', '--app', str(base / 'app.tar'),
                       '--app-sha256', sha((base / 'app.tar').read_bytes()), '--build-id', build,
                       '--apks', str(folders['runtime']), '--manifest-sha256', sha(manifest.encode()), '--package-count', '3',
                       '--display-apks', str(folders['display']), '--camera-apks', str(folders['camera']),
                       '--gphoto-apks', str(gphoto),
                       '--output', str(output), '--project', str(ROOT)]
            # Display and camera packages are pinned; placeholders must be refused.
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            for group, variable in (('display', 'DISPLAY_PACKAGES'), ('camera', 'CAMERA_PACKAGES')):
                pins = {}
                block = prepare[prepare.index(variable + '={'):]
                for name, digest in re.findall(r"'([^']+\.apk)':'([0-9a-f]{64})'", block[:block.index('}')]):
                    pins[name] = digest
                self.assertTrue(pins)
            # The overlay rules are checked on an overlay built with pins satisfied by patching.
            source = prepare.replace("  assert sha(a.display_apks/name)==digest\n", "").replace("  assert sha(a.camera_apks/name)==digest\n", "") \
                .replace("assert sha(a.gphoto_apks/name)==meta['sha256']; ", "")
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
                # The public image announces itself and may use its wired uplink.
                marker = archive.getmember('etc/wifi-bridge/pi5-appliance')
                self.assertEqual((marker.uid, marker.mode & 0o022), (0, 0))
                self.assertIn(b'host-name=astro-bridge', archive.extractfile('etc/avahi/avahi-daemon.conf').read())
                # No manufacturer file, licence text, installer or access rule is in the image. The only trace of a
                # manufacturer is the udev link below, to the rule its installed driver writes on the data partition.
                self.assertFalse([n for n in names if re.search(r'(?i)zwo|asi|qhy|playerone|player-one|atik|moravian|/vendor|licen[cs]es/|install-zwo', n)
                                  and not re.fullmatch(r'etc/udev/rules\.d/70-astro-bridge-[a-z-]+\.rules', n)
                                  and n != 'etc/wifi-bridge/pi5-asi220-test'], names)  # Historical name of the generic camera-profile marker.
                for member in archive.getmembers():
                    if member.isfile():
                        self.assertNotIn(b'libASICamera2', archive.extractfile(member).read(), member.name)
                # Network time steps the clock whenever an uplink appears; boot never waits for it.
                self.assertIn(b'makestep 1.0 -1', archive.extractfile('etc/chrony/chrony.conf').read())
                self.assertIn(b'FAST_STARTUP=yes', archive.extractfile('etc/conf.d/chronyd').read())
                self.assertIn('etc/runlevels/default/chronyd', names)
                # Longer system logs: 2 MB per file and four rotated files, kernel messages included.
                self.assertEqual(archive.extractfile('etc/conf.d/syslog').read(), b'SYSLOGD_OPTS="-t -s 2048 -b 4"\n')
                self.assertEqual(archive.getmember('etc/runlevels/default/syslog').linkname, '/etc/init.d/syslog')
                self.assertEqual(archive.getmember('etc/runlevels/default/klogd').linkname, '/etc/init.d/klogd')
                # Logs survive a restart: this boot and three before it on the data partition, memory without it.
                self.assertEqual(archive.getmember('etc/runlevels/default/astro-logs').linkname, '/etc/init.d/astro-logs')
                self.assertEqual(archive.getmember('etc/init.d/astro-logs').mode & 0o777, 0o755)
                logs = archive.extractfile('etc/init.d/astro-logs').read().decode()
                self.assertIn('after astro-data; before syslog klogd networkmanager wifi-bridge', logs)
                self.assertIn('mount --bind "$LOGS/current" /var/log', logs)
                self.assertIn('rm -rf "$LOGS/previous-3"', logs)
                # Forwarding, and each interface answers address resolution for its own address only:
                # with a cable and a Wi-Fi link on one network, relay rules name the entry interface.
                self.assertEqual(archive.extractfile('etc/sysctl.d/80-wifi-bridge.conf').read(),
                                 b'net.ipv4.ip_forward=1\nnet.ipv4.conf.all.arp_ignore=1\n')
                # The remote console starts at every boot, and the image holds no key and no password for it.
                self.assertEqual(archive.getmember('etc/runlevels/default/astro-ssh').linkname, '/etc/init.d/astro-ssh')
                self.assertEqual(archive.getmember('etc/init.d/astro-ssh').mode & 0o777, 0o755)
                self.assertIn('etc/wifi-bridge/ssh-access.py', names)
                self.assertIn('etc/wifi-bridge/astro_ssh_rule.py', names)
                self.assertFalse([n for n in names if n.startswith('etc/ssh/') or 'ssh_host' in n])
                # The first-boot zone reader is beside firstboot.py, which imports it from its own folder.
                self.assertEqual(archive.extractfile('etc/wifi-bridge/astro_seed.py').read(), (ROOT / 'image/common/astro_seed.py').read_bytes())
                # One udev link per catalog manufacturer, to the rule its installed driver writes on the data partition.
                for entry in json.loads((ROOT / 'wifi_bridge/static/drivers-catalog.json').read_text())['drivers']:
                    folder = 'zwo' if entry['id'] == 'zwo_asi' else entry['id'].replace('_', '-')
                    self.assertNotIn(f'etc/udev/rules.d/71-astro-bridge-{folder}-bundled.rules', names)
                    link = archive.getmember(f'etc/udev/rules.d/70-astro-bridge-{folder}.rules')
                    self.assertEqual(link.linkname, f'/var/lib/wifi-bridge/camera-drivers/{folder}/70-astro-bridge-{folder}.rules')
                # gPhoto2 is installed at boot from pinned packages, and stays off: no private gPhoto marker.
                self.assertEqual(archive.getmember('etc/runlevels/default/astro-gphoto-packages').linkname, '/etc/init.d/astro-gphoto-packages')
                listed = archive.extractfile('etc/wifi-bridge/gphoto-packages.sha256').read().decode().splitlines()
                self.assertEqual(sorted(line.split()[1] for line in listed), sorted(gphoto_pins))
                self.assertNotIn('etc/wifi-bridge/pi5-indi-gphoto-test', names)
            self.assertEqual(sorted(p.name for p in (shutil_output / 'data/cache/gphoto').iterdir()), sorted(gphoto_pins))


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


class RemoteConsoleTests(unittest.TestCase):
    """SSH is on at every boot; the published initial password never opens it."""

    def setUp(self):
        loader = importlib.util.spec_from_file_location('ssh_access', PI5 / 'ssh-access.py')
        self.access = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(self.access)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'; self.state.mkdir(mode=0o700)
        (self.state / 'bootstrap.json').write_text(json.dumps({'password': 'initial-fixture'}))
        self.shadow = self.root / 'shadow'
        self.generated = []

    @staticmethod
    def crypt(password, salt):
        # A stand-in for the system hash: deterministic, 86 characters, salt-dependent.
        return '$6$' + salt + '$' + (hashlib.sha512((salt + password).encode()).hexdigest() * 2)[:86]

    def prepare(self, hash_value):
        self.shadow.write_text('root:' + hash_value + ':19000:0:::::\nbridge:!::0:::::\n')
        def keygen(target):
            self.generated.append(target); target.write_text('host key fixture\n'); target.with_name(target.name + '.pub').write_text('public\n')
        self.home = self.root / 'home'; self.home.mkdir(exist_ok=True)
        allowed = self.access.prepare(self.state, self.state / 'ssh', self.root / 'run', self.shadow, keygen, self.crypt, self.home)
        return allowed, (self.root / 'run/sshd_config').read_text()

    def test_standard_ssh_copy_id_lands_in_the_kept_key_file(self):
        # The standard ssh-copy-id appends to ~/.ssh/authorized_keys, which is in memory.
        self.prepare('!')
        keys = self.state / 'ssh/authorized_keys'
        link = self.home / '.ssh/authorized_keys'
        self.assertTrue(link.is_symlink()); self.assertEqual((self.home / '.ssh').stat().st_mode & 0o777, 0o700)
        with open(link, 'a') as stream: stream.write('ssh-ed25519 AAAA copied\n')
        self.assertEqual(keys.read_text(), 'ssh-ed25519 AAAA copied\n')
        self.prepare('!')
        self.assertEqual(keys.read_text(), 'ssh-ed25519 AAAA copied\n')
        self.assertEqual(keys.stat().st_mode & 0o777, 0o600)

    def test_keys_written_before_the_link_are_moved_once(self):
        (self.root / 'home/.ssh').mkdir(parents=True, mode=0o700)
        (self.root / 'home/.ssh/authorized_keys').write_text('ssh-ed25519 AAAA early\n')
        (self.state / 'ssh').mkdir(mode=0o700); (self.state / 'ssh/authorized_keys').write_text('ssh-ed25519 AAAA kept')
        self.prepare('!'); self.prepare('!')
        self.assertEqual((self.state / 'ssh/authorized_keys').read_text(), 'ssh-ed25519 AAAA kept\nssh-ed25519 AAAA early\n')
        self.assertTrue((self.home / '.ssh/authorized_keys').is_symlink())

    def test_initial_password_never_opens_a_network_login(self):
        allowed, config = self.prepare(self.crypt('initial-fixture', 'saltsalt'))
        self.assertFalse(allowed)
        self.assertIn('PasswordAuthentication no\n', config); self.assertIn('PermitRootLogin prohibit-password\n', config)
        self.assertIn('PubkeyAuthentication yes\n', config); self.assertIn('PermitEmptyPasswords no\n', config)
        self.assertIn('AuthorizedKeysFile ' + str(self.state / 'ssh/authorized_keys') + '\n', config)
        self.assertNotIn('initial-fixture', config)

    def test_applied_state_is_written_for_the_bridge(self):
        import json as json_module
        self.prepare(self.crypt('initial-fixture', 'saltsalt'))
        self.assertEqual(json_module.loads((self.root / 'run/state.json').read_text()), {'password_login': False})
        self.prepare(self.crypt('chosen by the owner', 'othersalt'))
        self.assertEqual(json_module.loads((self.root / 'run/state.json').read_text()), {'password_login': True})
        self.assertEqual((self.root / 'run/state.json').stat().st_mode & 0o777, 0o600)

    def test_password_chosen_by_the_owner_opens_it(self):
        allowed, config = self.prepare(self.crypt('chosen by the owner', 'othersalt'))
        self.assertTrue(allowed)
        self.assertIn('PasswordAuthentication yes\n', config); self.assertIn('PermitRootLogin yes\n', config)

    def test_locked_unknown_or_unreadable_state_keeps_password_login_off(self):
        for value in ('!', '', '*', '$y$j9T$unknownscheme$abcdef', '$6$rounds=5000$salt$' + 'a' * 86):
            self.assertFalse(self.prepare(value)[0], value)
        def failing(password, salt): raise OSError('no tool')
        self.assertFalse(self.access.password_changed(self.crypt('other', 'salt'), 'initial-fixture', failing))
        (self.state / 'bootstrap.json').unlink()
        self.assertFalse(self.prepare(self.crypt('chosen by the owner', 'othersalt'))[0])

    def test_host_identity_is_created_once_and_kept_private(self):
        self.prepare('!'); self.prepare('!')
        self.assertEqual(len(self.generated), 1)
        folder = self.state / 'ssh'
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((folder / 'ssh_host_ed25519_key').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'run/sshd_config').stat().st_mode & 0o777, 0o600)
        (folder / 'authorized_keys').write_text('ssh-ed25519 AAAA fixture\n'); (folder / 'authorized_keys').chmod(0o644)
        self.prepare('!')
        self.assertEqual((folder / 'authorized_keys').stat().st_mode & 0o777, 0o600)

    def test_service_starts_after_the_data_partition_and_checks_its_configuration(self):
        service = (PI5 / 'astro-ssh').read_text()
        self.assertIn('need astro-data astro-runtime-packages', service)
        self.assertIn('after astro-console-account', service)
        self.assertLess(service.index('ssh-access.py prepare'), service.index('sshd -t -f /run/astro-ssh/sshd_config'))
        self.assertIn('rc-service --ifstarted astro-ssh restart', (PI5 / 'astro-console-account').read_text())

