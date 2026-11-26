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


def fake_qrencode(command, input, **kwargs):
    """Like qrencode: version 4 for the Wi-Fi code, 1 for the address, -v as a minimum."""
    kind = command[command.index('-t') + 1]; margin = int(command[command.index('-m') + 1])
    version = max(4 if len(input) > 40 else 1, int(command[command.index('-v') + 1]) if '-v' in command else 1)
    size = 17 + 4 * version + 2 * margin
    if kind == 'ANSI':
        rows = ['\x1b[47m' + '  ' * size + '\x1b[0m'] * size
    else:
        rows = ['\x1b[40;37m' + '\u2580' * size + '\x1b[0m'] * ((size + 1) // 2)
    return mock.Mock(stdout='\n'.join(rows).encode())


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
        with mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode):
            screen = display.render('Astro-Bridge-ABCDEF', '0123456789abcdef01234567', 45, 160)
        lines = screen.split('\r\n')
        self.assertLessEqual(len(lines), 44)
        self.assertIn('Alt+F1', lines[-1])
        self.assertIn('Open Astro Bridge', lines[1])
        self.assertLess(max(len(display.ANSI.sub('', line)) for line in lines), 160)

    def test_both_codes_have_the_same_size_and_quiet_zone(self):
        wifi = 'WIFI:T:WPA;S:Astro-Bridge-ABCDEF;P:0123456789abcdef01234567;;'
        for margin, kind in display.LAYOUTS:
            with mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode) as run:
                left, right = display.pair(wifi, 'http://192.168.77.1/', margin, kind)
            self.assertEqual((len(left), display.width(left)), (len(right), display.width(right)), kind)
            # The short web address is drawn at the version of the longer Wi-Fi code, never bigger.
            self.assertTrue(any('-v' in call.args[0] for call in run.call_args_list))
            self.assertTrue(all(call.args[0][call.args[0].index('-m') + 1] == str(margin) for call in run.call_args_list))
            self.assertNotIn(b'0123456789abcdef01234567', b''.join(str(call.args[0]).encode() for call in run.call_args_list))

    def test_screen_size_picks_the_largest_layout_that_fits(self):
        for rows, cols, expected in ((60, 200, ('4', 'ANSI')), (45, 160, ('2', 'ANSI')), (37, 128, ('4', 'ANSIUTF8'))):
            with mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode) as run:
                screen = display.render('Astro-Bridge-ABCDEF', '0123456789abcdef01234567', rows, cols)
            last = run.call_args.args[0]
            self.assertEqual((last[last.index('-m') + 1], last[last.index('-t') + 1]), expected, (rows, cols))
            lines = screen.split('\r\n')
            self.assertLess(len(lines), rows)
            self.assertLess(max(len(display.ANSI.sub('', line)) for line in lines), cols)
        with mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode):
            with self.assertRaises(ValueError): display.render('Astro-Bridge-ABCDEF', '0123456789abcdef01234567', 16, 50)
        self.assertIn('too small', display.explain(ValueError('console too small for QR codes')))

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
                qr.assert_called_once()
                self.assertEqual(qr.call_args[0], (url,))
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
            ssid, password = 'astro-bridge-ddeeff', 'example-secret'
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
                self.assertEqual(display.credentials(), (ssid, None))
                with mock.patch.object(display, 'command', side_effect=lambda *args: 'type managed' if args[0].endswith('/iw') else cmd(*args)):
                    with self.assertRaises(ValueError): display.credentials()
                with mock.patch.object(display, 'command', side_effect=lambda *args: 'state=DISABLED' if args[0].endswith('/hostapd_cli') else cmd(*args)):
                    with self.assertRaises(ValueError): display.credentials()
                config.update(hotspot={'enabled': False, 'mac': '', 'ssid': ssid}, connection_mode='private', home={'mac': mac}, interface_roles={mac:'auto'})
                (state / 'config.json').write_text(json.dumps(config))
                self.assertEqual(display.credentials(), (ssid, None))
                config.update(hotspot={'enabled': True, 'mac': '', 'ssid': ssid}, connection_mode='hotspot_shared', home={'mac': '11:22:33:44:55:66', 'ssid': 'Home'}, interface_roles={})
                (state / 'config.json').write_text(json.dumps(config))
                self.assertEqual(display.credentials(), (ssid, None))
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
                # The bridge keeps only the derived key: never shown, still checked.
                del config['hotspot_password']
                config['hotspot_key'] = {'ssid': ssid, 'psk': psk}
                (state / 'config.json').write_text(json.dumps(config))
                self.assertEqual(display.credentials(), (ssid, None))
                config['hotspot_key'] = {'ssid': ssid, 'psk': '0' * 64}
                (state / 'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): display.credentials()
                config['hotspot_key'] = {'ssid': 'Other-name', 'psk': psk}
                (state / 'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): display.credentials()

    def test_personal_password_is_never_drawn(self):
        with mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode) as run:
            screen = display.render('Astro-Bridge-ABCDEF', None, 45, 160)
        self.assertIn('Wi-Fi: Astro-Bridge-ABCDEF', screen)
        self.assertIn('bridge password', screen)
        self.assertIn('http://192.168.77.1/', screen)
        self.assertEqual([call.kwargs['input'] for call in run.call_args_list], [b'http://192.168.77.1/'])
        self.assertLess(len(screen.split('\r\n')), 45)


class NoteKeepsConsoleHintTests(unittest.TestCase):
    def test_restarting_note_does_not_replace_the_alt_f1_instruction(self):
        with mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode):
            for password in ('0123456789abcdef01234567', None):
                for rows in (45, 60):
                    screen = display.render('Astro-Bridge-ABCDEF', password, rows, 160, display.RESTARTING)
                    self.assertIn(display.RESTARTING, screen)
                    self.assertIn('Alt+F1', screen)
                    self.assertLess(len(screen.split('\r\n')), rows)


class WaitingScreenTests(unittest.TestCase):
    """Without a reachable bridge, the screen says why and keeps checking."""

    def test_reasons_are_plain_words_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)
            with mock.patch.object(display, 'STATE', state):
                self.assertIn('no Wi-Fi radio could start it', display.explain(FileNotFoundError('hotspot.json')))
                (state / 'hotspot.json').write_text(json.dumps({'interface': 'wlan0', 'mac': 'aa:bb:cc:dd:ee:ff'}))
                text = display.explain(ValueError('hostapd not active'))
                self.assertIn('did not start on wlan0', text)
                self.assertIn('USB Wi-Fi adapter', text)
                self.assertIn('turned off', display.explain(ValueError('hotspot disabled')))
                self.assertIn('does not answer', display.explain(ValueError('management unavailable')))
        screen = display.render_waiting('The bridge Wi-Fi did not start on wlan0.', 45, 60)
        lines = display.ANSI.sub('', screen).split('\r\n')
        self.assertLessEqual(max(len(line) for line in lines), 59)
        self.assertLess(len(lines), 45)
        self.assertIn('Ethernet', screen)
        self.assertIn('wifi-bridge-ap.log', screen)

    def test_timeout_keeps_checking_and_codes_return(self):
        clock = [0.0]
        results = [ValueError('hostapd not active')] * 3 + [('Astro-Bridge-DDEEFF', '12345678')]
        written = []

        class Tty:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def write(self, text): written.append(text)
            def flush(self): pass

        def credentials():
            value = results.pop(0) if len(results) > 1 else results[0]
            if isinstance(value, Exception): raise value
            return value

        def sleep(seconds):
            clock[0] += 61
            if not results or len(results) == 1 and written and 'Connect to Wi-Fi' in written[-1]:
                raise StopIteration

        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)
            (state / 'hotspot.json').write_text(json.dumps({'interface': 'wlan0'}))
            owned = state / 'owned'; owned.write_text('')
            with mock.patch.object(display, 'STATE', state), mock.patch.object(display, 'OWNED', owned), \
                    mock.patch.object(display, 'READY', state / 'ready'), \
                    mock.patch.object(display, 'TTY', mock.Mock(open=lambda mode: Tty())), \
                    mock.patch.object(display.fcntl, 'ioctl', return_value=b'\x2d\x00\xa0\x00\x00\x00\x00\x00'), \
                    mock.patch.object(display, 'credentials', side_effect=credentials), \
                    mock.patch.object(display, 'private_access', side_effect=ValueError('home unavailable')), \
                    mock.patch.object(display, 'qr', return_value=['\x1b[47m  \x1b[0m']), \
                    mock.patch.object(display, 'diagnostic') as diagnostic, \
                    mock.patch.object(display.time, 'monotonic', side_effect=lambda: clock[0]), \
                    mock.patch.object(display.time, 'sleep', side_effect=sleep):
                with self.assertRaises(StopIteration):
                    display.main()
            self.assertTrue(owned.exists())
        diagnostic.assert_called_once()
        self.assertIn('did not start on wlan0', diagnostic.call_args[0][0])
        self.assertTrue(any('cannot be reached yet' in text for text in written))
        self.assertIn('Connect to Wi-Fi', written[-1])
        self.assertFalse(any('12345678' in text for text in written if 'cannot be reached' in text))

    def run_main(self, results, steps):
        """Run the display loop over a list of check results, one per 3 s; return what was written."""
        clock = [0.0]; written = []; results = list(results)

        class Tty:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def write(self, text): written.append(text)
            def flush(self): pass

        def credentials():
            value = results.pop(0) if len(results) > 1 else results[0]
            if isinstance(value, Exception): raise value
            return value

        count = [0]
        def sleep(seconds):
            clock[0] += 3; count[0] += 1
            if count[0] >= steps: raise StopIteration

        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)
            (state / 'hotspot.json').write_text(json.dumps({'interface': 'wlan0'}))
            owned = state / 'owned'; owned.write_text('')
            with mock.patch.object(display, 'STATE', state), mock.patch.object(display, 'OWNED', owned), \
                    mock.patch.object(display, 'READY', state / 'ready'), \
                    mock.patch.object(display, 'TTY', mock.Mock(open=lambda mode: Tty())), \
                    mock.patch.object(display.fcntl, 'ioctl', return_value=b'\x2d\x00\xa0\x00\x00\x00\x00\x00'), \
                    mock.patch.object(display, 'credentials', side_effect=credentials), \
                    mock.patch.object(display, 'private_access', side_effect=ValueError('home unavailable')), \
                    mock.patch.object(display.subprocess, 'run', side_effect=fake_qrencode), \
                    mock.patch.object(display, 'diagnostic'), \
                    mock.patch.object(display.time, 'monotonic', side_effect=lambda: clock[0]), \
                    mock.patch.object(display.time, 'sleep', side_effect=sleep):
                with self.assertRaises(StopIteration):
                    display.main()
        return written

    def test_codes_stay_while_the_hotspot_restarts(self):
        # The bench: the hotspot is restarted every 30 s or so, its checks fail for a few seconds each time.
        codes = ('Astro-Bridge-DDEEFF', '0123456789abcdef01234567')
        sequence = [codes, codes] + [ValueError('radio is not an AP'), ValueError('hostapd not active'), OSError('timed out')] + [codes] * 3
        written = self.run_main(sequence * 4, 40)
        blank = [text for text in written if 'Connect to Wi-Fi' not in text]
        self.assertEqual(blank, [])
        self.assertTrue(any(display.RESTARTING in text for text in written))
        self.assertNotIn(display.RESTARTING, written[-1])
        # Drawn on changes only, not every 3 s.
        self.assertLess(len(written), 12)
        # The console never blanks itself under the codes.
        self.assertTrue(all(text.startswith(display.NO_BLANK) for text in written))

    def test_a_long_failure_explains_instead_of_going_black(self):
        codes = ('Astro-Bridge-DDEEFF', '12345678')
        written = self.run_main([codes, ValueError('hostapd not active')], 60)
        self.assertIn('Connect to Wi-Fi', written[0])
        self.assertTrue(any(display.RESTARTING in text for text in written))
        self.assertIn('cannot be reached yet', written[-1])
        self.assertFalse(any(text.strip() in ('', '\x1b[0m\x1b[2J\x1b[H') for text in written))

    def test_wrong_codes_are_taken_down_at_once_and_explained(self):
        codes = ('Astro-Bridge-DDEEFF', '12345678')
        written = self.run_main([codes, ValueError('hotspot disabled')], 3)
        self.assertIn('Connect to Wi-Fi', written[0])
        self.assertIn('turned off', written[1])
        self.assertNotIn('12345678', written[1])

    def test_first_check_not_passed_yet_shows_a_starting_line(self):
        written = self.run_main([ValueError('hostapd not active')], 3)
        self.assertEqual(len(written), 1)
        self.assertIn(display.STARTING, written[0])

    def test_hotspot_on_another_free_radio_still_shows_its_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)
            saved, fallback = 'aa:bb:cc:dd:ee:01', 'aa:bb:cc:dd:ee:02'
            ssid, password = 'Astro-Bridge-DDEE01', '12345678'
            config = {'hotspot': {'enabled': True, 'mac': saved, 'ssid': ssid},
                      'interface_roles': {saved: 'hotspot'}, 'connection_mode': 'hotspot_shared', 'home': {}}
            (state / 'config.json').write_text(json.dumps(config))
            (state / 'bootstrap.json').write_text(json.dumps({'password': password}))
            (state / 'hotspot.json').write_text(json.dumps({'interface': 'wlan1', 'mac': fallback}))
            psk = hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()
            (state / 'hostapd.conf').write_text('interface=wlan1\nssid2=' + ssid.encode().hex() + '\nwpa_psk=' + psk)
            address = state / 'address'; address.write_text(fallback)
            def path_override(*args):
                return address if args[0] == '/sys/class/net' else pathlib.Path(*args)
            def cmd(*args):
                if args[0].endswith('/iw'): return 'Interface wlan1\n\ttype AP\n'
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
            with mock.patch.object(display, 'STATE', state), mock.patch.object(display, 'Path', side_effect=path_override), \
                    mock.patch.object(display, 'command', side_effect=cmd), \
                    mock.patch.object(display.urllib.request, 'build_opener', side_effect=opener):
                self.assertEqual(display.credentials(), (ssid, password))
                config['interface_roles'][fallback] = 'telescope'
                (state / 'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): display.credentials()


if __name__ == '__main__':
    unittest.main()
