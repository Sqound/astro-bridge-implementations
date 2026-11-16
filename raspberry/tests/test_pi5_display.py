import importlib.util
import hashlib
import json
import tempfile
import pathlib
import unittest
from unittest import mock

path = pathlib.Path(__file__).resolve().parents[1] / 'image/pi5/display.py'
spec = importlib.util.spec_from_file_location('pi5_display', path)
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


class DisplayTests(unittest.TestCase):
    def test_wifi_escapes_qr_metacharacters(self):
        self.assertEqual(display.wifi_escape('a;,:"\\b'), 'a\\;\\,\\:\\"\\\\b')

    def test_qr_passes_secret_only_on_stdin(self):
        result = mock.Mock(stdout=b'\x1b[47m  \x1b[0m\n')
        with mock.patch.object(display.subprocess, 'run', return_value=result) as run:
            display.qr('private-password')
        args, kwargs = run.call_args
        self.assertNotIn('private-password', args[0])
        self.assertEqual(kwargs['input'], b'private-password')
        self.assertEqual(kwargs['stderr'], display.subprocess.DEVNULL)
        self.assertIn('4', args[0])

    def test_small_terminal_fails_closed(self):
        with mock.patch.object(display, 'qr', return_value=['\x1b[47m' + ' ' * 80 + '\x1b[0m'] * 30):
            with self.assertRaises(ValueError):
                display.render('ssid', 'password', 25, 80)
            screen = display.render('ssid', 'password', 40, 170)
            self.assertIn('Alt+F1', screen)
            self.assertNotIn('password', screen)

    def test_compact_720p_keeps_a_free_terminal_row_and_column(self):
        left = ['\x1b[47m' + ' ' * 82 + '\x1b[0m'] * 41
        right = ['\x1b[47m' + ' ' * 58 + '\x1b[0m'] * 29
        with mock.patch.object(display, 'qr', side_effect=[left, right]):
            screen = display.render('Astro-Bridge-ABCDEF', '0123456789abcdef01234567', 45, 160)
        lines = screen.split('\r\n')
        self.assertEqual(len(lines), 44)
        self.assertIn('Alt+F1', lines[-1])
        self.assertIn('Open Astro Bridge', lines[1])
        self.assertEqual(len(display.ANSI.sub('', lines[2])), 144)

    def test_disabled_hotspot_rejected(self):
        with mock.patch.object(display, 'STATE') as state:
            (state / 'config.json').read_bytes.return_value = b'{"hotspot":{"enabled":false}}'
            with self.assertRaises(ValueError):
                display.credentials()

    def test_private_access_only_shows_verified_home_and_initial_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp); mac = 'aa:bb:cc:dd:ee:ff'
            config = {'connection_mode':'private', 'home':{'mac':mac, 'ssid':'Home', 'password':'do-not-display'},
                      'interface_roles':{mac:'auto'}, 'admin_hash':''}
            (state/'config.json').write_text(json.dumps(config))
            (state/'bootstrap.json').write_text(json.dumps({'password':'12345678'}))
            address = state/'address'; address.write_text(mac)
            def path_override(*args):
                return address if args[0] == '/sys/class/net' else pathlib.Path(*args)
            info = {'product':'astro-bridge','api_version':'1','contract_version':1,'request_path':'home'}
            def opener(*args):
                client = mock.MagicMock()
                def response(url, timeout):
                    result = mock.MagicMock(); result.status = 200
                    result.headers = {'Content-Type':'text/html'}
                    result.read.return_value = json.dumps(info).encode() if url.endswith('/info') else b'<html>'
                    result.__enter__.return_value = result
                    return result
                client.open.side_effect = response
                return client
            with mock.patch.object(display, 'STATE', state), mock.patch.object(display, 'Path', side_effect=path_override), mock.patch.object(display, 'command', return_value='2: wlan0 inet 192.168.1.12/24 scope global wlan0'), mock.patch.object(display.urllib.request, 'build_opener', side_effect=opener):
                url, password = display.private_access()
                self.assertEqual(url, 'http://192.168.1.12/')
                with mock.patch.object(display, 'qr', return_value=['\x1b[47m  \x1b[0m']) as qr:
                    screen = display.render_private(url, password, 30, 120)
                qr.assert_called_once_with(url)
                self.assertIn('12345678', screen)
                self.assertNotIn('do-not-display', screen)
                config['interface_roles'][mac] = 'home'
                (state/'config.json').write_text(json.dumps(config))
                self.assertEqual(display.private_access()[0], url)
                config['admin_hash'] = 'configured'
                (state/'config.json').write_text(json.dumps(config))
                self.assertIsNone(display.private_access()[1])
                info['request_path'] = 'other'
                with self.assertRaises(ValueError): display.private_access()

    def test_applied_password_and_hotspot_ingress(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)
            mac = 'aa:bb:cc:dd:ee:ff'
            ssid, password = 'Astro-Bridge-DDEEFF', 'example-secret'
            config = {'hotspot': {'enabled': True, 'mac': mac, 'ssid': ssid},
                      'interface_roles': {mac: 'hotspot'}, 'hotspot_password': password}
            (state / 'config.json').write_text(json.dumps(config))
            (state / 'bootstrap.json').write_text(json.dumps({'password': 'old-password'}))
            (state / 'hotspot.json').write_text(json.dumps({'interface': 'wlan0'}))
            psk = hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()
            (state / 'hostapd.conf').write_text('interface=wlan0\nssid2=' + ssid.encode().hex() + '\nwpa_psk=' + psk)
            address = state / 'address'; address.write_text(mac)
            def path_override(*args):
                return address if args[0] == '/sys/class/net' else pathlib.Path(*args)
            def cmd(*args):
                if args[0].endswith('/iw'): return 'Interface wlan0\n\ttype AP\n'
                if args[0].endswith('/hostapd_cli'): return 'state=ENABLED\nssid[0]=' + ssid
                return 'started' if 'rc-service' in args[0] else '192.168.77.1/24'
            info = {'product': 'astro-bridge', 'api_version': '1', 'contract_version': 1, 'request_path': 'hotspot'}
            def opener(*args):
                client = mock.MagicMock()
                def response(url, timeout):
                    result = mock.MagicMock(); result.status = 200
                    result.headers = {'Content-Type': 'text/html'}
                    result.read.return_value = json.dumps(info).encode() if url.endswith('/info') else b'<html>'
                    result.__enter__.return_value = result
                    return result
                client.open.side_effect = response
                return client
            with mock.patch.object(display, 'STATE', state), mock.patch.object(display, 'Path', side_effect=path_override), mock.patch.object(display, 'command', side_effect=cmd), mock.patch.object(display.urllib.request, 'build_opener', side_effect=opener):
                self.assertEqual(display.credentials(), (ssid, password))
                with mock.patch.object(display, 'command', side_effect=lambda *args: 'type managed' if args[0].endswith('/iw') else cmd(*args)):
                    with self.assertRaises(ValueError): display.credentials()
                with mock.patch.object(display, 'command', side_effect=lambda *args: 'state=DISABLED' if args[0].endswith('/hostapd_cli') else cmd(*args)):
                    with self.assertRaises(ValueError): display.credentials()
                config.update(hotspot={'enabled': False, 'mac': '', 'ssid': ssid}, connection_mode='private', home={'mac': mac}, interface_roles={mac:'auto'})
                (state / 'config.json').write_text(json.dumps(config))
                self.assertEqual(display.credentials(), (ssid, password))
                config.update(hotspot={'enabled': True, 'mac': '', 'ssid': ssid}, connection_mode='hotspot_shared', home={'mac': '11:22:33:44:55:66', 'ssid': 'Home'}, interface_roles={})
                (state / 'config.json').write_text(json.dumps(config))
                self.assertEqual(display.credentials(), (ssid, password))
                config['interface_roles'][mac] = 'telescope'
                (state / 'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): display.credentials()
                config['interface_roles'][mac] = 'auto'
                (state / 'config.json').write_text(json.dumps(config))
                info['request_path'] = 'home'
                with self.assertRaises(ValueError): display.credentials()
                info['request_path'] = 'hotspot'
                config['hotspot_password'] = 'rotated-secret'
                (state / 'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): display.credentials()


if __name__ == '__main__':
    unittest.main()
