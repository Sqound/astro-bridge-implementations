"""Real temporary OpenSSL identities and paired trusted HTTPS/HTTP listeners.

All keys/certs/mount tables are disposable fixtures. No OS trust or device changes.
"""
import fcntl
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.tls import Certificates, read_request
from wifi_bridge.config import Invalid, Store
from wifi_bridge.controller import Controller
from wifi_bridge.network import DemoNetwork
from wifi_bridge.tls import CertificateState, HTTPSService
from wifi_bridge.web import WebServer

OPENSSL = shutil.which('openssl')


def execute(args, **kwargs):
    if args[0] == '/usr/bin/openssl': args = [OPENSSL, *args[1:]]
    return subprocess.run(args, **kwargs)


def diskless(root):
    for path in ('proc/self', 'dev/disk/by-label', 'var/lib/wifi-bridge'):
        (root / path).mkdir(parents=True, exist_ok=True)
    state = root / 'var/lib/wifi-bridge'; state.chmod(0o700)
    (root / 'dev/data').touch()
    (root / 'dev/disk/by-label/ASTRO_DATA').symlink_to('../../data')
    (root / 'proc/self/mountinfo').write_text('42 1 179:2 / /var/lib/wifi-bridge rw - ext4 /dev/data rw\n')
    store = Store(state, '00:11:22:12:ab:cd'); store.save(store.config)
    return state, store


@unittest.skipUnless(OPENSSL, 'OpenSSL required')
class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.state, self.store = diskless(self.root)
        self.helper = Certificates(self.root, run=execute)

    def prepare(self):
        return self.helper.prepare({'owner_confirmed_utc': int(time.time())})

    def generation(self, record):
        return self.state / 'tls/generations' / record['generation']

    def test_unique_constrained_p256_identity_and_private_durable_generation(self):
        result = self.prepare(); folder = self.generation(result)
        self.assertEqual(result['clock_evidence'], 'owner-confirmed-current-utc')
        self.assertEqual(len(result['fingerprint_sha256']), 64)
        ca = execute([OPENSSL, 'x509', '-in', str(folder / 'ca.pem'), '-text', '-noout'], capture_output=True, text=True, check=True).stdout
        leaf = execute([OPENSSL, 'x509', '-in', str(folder / 'server.pem'), '-text', '-noout'], capture_output=True, text=True, check=True).stdout
        for text in ('CA:TRUE, pathlen:0', 'X509v3 Name Constraints: critical', self.helper.hostname(), '192.168.77.1/255.255.255.255', 'ASN1 OID: prime256v1'):
            self.assertIn(text, ca)
        self.assertIn('CA:FALSE', leaf); self.assertIn('TLS Web Server Authentication', leaf)
        self.assertIn('DNS:' + self.helper.hostname() + ', IP Address:192.168.77.1', leaf)
        for file in folder.iterdir(): self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(Certificates(self.root, run=execute).status(), result)
        self.assertNotIn('PRIVATE KEY', json.dumps(result)); self.assertNotIn(str(self.root), json.dumps(result))
        with tempfile.TemporaryDirectory() as temporary:
            other = Path(temporary); diskless(other)
            different = Certificates(other, run=execute).prepare({'owner_confirmed_utc': int(time.time())})
            self.assertNotEqual(result['fingerprint_sha256'], different['fingerprint_sha256'])
        with self.assertRaises(ValueError): self.prepare()  # No silent CA rotation.

    def test_explicit_renewal_same_ca_and_reset_required_after_disabled_rename(self):
        first = self.prepare(); original = self.generation(first)
        second = self.helper.prepare({'owner_confirmed_utc': int(time.time())}, renew=True)
        self.assertNotEqual(first['generation'], second['generation'])
        self.assertEqual(first['fingerprint_sha256'], second['fingerprint_sha256'])
        self.assertEqual((original / 'ca.key').read_bytes(), (self.generation(second) / 'ca.key').read_bytes())
        self.helper.deactivate(); self.assertFalse(self.helper.status()['enabled'])
        enabled = self.helper.enable()
        self.assertTrue(enabled['enabled']); self.assertEqual(enabled['generation'], second['generation'])
        self.assertEqual(enabled['fingerprint_sha256'], second['fingerprint_sha256'])
        self.helper.deactivate()
        value = dict(self.store.config, hostname='renamed-bridge'); self.store.save(value)
        with self.assertRaises(ValueError): self.helper.prepare({'owner_confirmed_utc': int(time.time())}, renew=True)
        with self.assertRaises(ValueError): self.helper.enable()
        self.helper.deactivate(reset=True)
        new = self.prepare(); self.assertNotEqual(second['fingerprint_sha256'], new['fingerprint_sha256'])
        self.assertEqual(len(list((self.state / 'tls/generations').iterdir())), 1)

    def test_staged_failure_and_pointer_fsync_failure_preserve_previous_identity(self):
        first = self.prepare(); pointer = (self.state / 'tls/current.json').read_bytes()
        real = self.helper.command
        def fail(args):
            if 'genpkey' in args: raise ValueError('injected generation failure')
            return real(args)
        with patch.object(self.helper, 'command', fail), self.assertRaises(ValueError):
            self.helper.prepare({'owner_confirmed_utc': int(time.time())}, renew=True)
        self.assertEqual((self.state / 'tls/current.json').read_bytes(), pointer)
        atomic = self.helper.atomic; failed = False
        def fail_after_replace(path, content):
            nonlocal failed
            atomic(path, content)
            if path.name == 'current.json' and not failed:
                failed = True; raise OSError('injected pointer sync failure')
        with patch.object(self.helper, 'atomic', fail_after_replace), self.assertRaises(OSError):
            self.helper.prepare({'owner_confirmed_utc': int(time.time())}, renew=True)
        self.assertEqual(self.helper.status()['generation'], first['generation'])

    def test_strict_mount_private_shapes_and_nonblocking_fifo_locks(self):
        self.helper.mounted()
        mount = self.root / 'proc/self/mountinfo'; original = mount.read_text()
        for value in ('broken', original.replace(' rw ', ' ro '), original.replace('ext4', 'tmpfs')):
            mount.write_text(value)
            with self.assertRaises(ValueError): self.prepare()
        mount.write_text(original)
        for relative in ('updates/lock', 'tls/operation.lock', 'configuration.lock'):
            file = self.state / relative; file.parent.mkdir(mode=0o700, exist_ok=True); file.unlink(missing_ok=True); os.mkfifo(file, 0o600)
            start = time.monotonic()
            with self.assertRaises(ValueError): self.prepare()
            self.assertLess(time.monotonic() - start, 1); file.unlink()
        for value in ([], None, {'version': True}, {'version': 1, 'configured': True, 'enabled': True, 'generation': '../escape'}):
            self.helper.atomic(self.state / 'tls/current.json', json.dumps(value).encode())
            with self.assertRaises(ValueError): self.helper.status()
            with self.assertRaises(ValueError): self.helper.enable()
        self.helper.deactivate(reset=True)  # Corrupt TLS remains explicitly recoverable.
        identity = self.prepare(); folder = self.generation(identity)
        for metadata in ([], None, {'hostname': self.helper.hostname()}, {'hostname': self.helper.hostname(), 'expires': 'invalid', 'clock_evidence': 'owner-confirmed-current-utc'}):
            self.helper.atomic(folder / 'metadata.json', json.dumps(metadata).encode())
            with self.assertRaises(ValueError): self.helper.status()
            with self.assertRaises(ValueError): CertificateState(self.state, run=execute).context(identity, self.helper.hostname())

    def test_bounded_cli_input_rejects_extra_paths_oversize_and_unclosed_pipe(self):
        for value in (b'[]', b' ' * 1025):
            reader, writer = os.pipe()
            with os.fdopen(reader, 'rb') as stream:
                os.write(writer, value); os.close(writer)
                with self.assertRaises(ValueError): read_request(stream, timeout=.1)
        reader, writer = os.pipe()
        try:
            with os.fdopen(reader, 'rb') as stream:
                os.write(writer, b'{')
                with self.assertRaises(ValueError): read_request(stream, timeout=.1)
        finally: os.close(writer)

    def test_clock_requires_positive_external_tracking_or_single_operation_confirmation(self):
        now = int(time.time())
        tracking = f'C0A80101,192.168.1.1,2,{now-1},0.001,0.001,0.001,1,0.1,0.1,0.01,0.01,64,Normal'
        sources = '^,*,192.168.1.1,1,6,377,1,0.001,0.001,0.01'
        outputs = {'tracking': tracking, 'sources': sources}
        with patch.object(self.helper, 'command', lambda args: outputs[args[-1]]):
            self.assertEqual(self.helper.clock_evidence({}), 'chrony-synchronized')
            for field, replacements in ((13, ['Not synchronised', 'Insert second']), (3, [str(now-7200), 'nan']), (4, ['5', 'inf']), (0, ['7F7F0101', '00000000'])):
                for replacement in replacements:
                    parts = tracking.split(','); parts[field] = replacement; outputs['tracking'] = ','.join(parts)
                    with self.assertRaises(ValueError): self.helper.clock_evidence({})
            outputs['tracking'] = tracking
            for value in ('#,*,GPS,0,6,377,1,0,0,0', '^,*,127.0.0.1,1,6,377,1,0,0,0', '^,?,192.168.1.1,1,6,0,7200,0,0,0', '^,*,192.168.1.1,1,6,377,7200,0,0,0'):
                outputs['sources'] = value
                with self.assertRaises(ValueError): self.helper.clock_evidence({})
            self.assertEqual(self.helper.clock_evidence({'owner_confirmed_utc': now}), 'owner-confirmed-current-utc')
            with self.assertRaises(ValueError): self.helper.clock_evidence({})  # Not persisted/reused.
            for request in ({'owner_confirmed_utc': now-121}, {'owner_confirmed_utc': True}, {'path': '/tmp/arbitrary'}):
                with self.assertRaises(ValueError): self.helper.clock_evidence(request)
        with patch.object(self.helper, 'now', lambda: 0), self.assertRaises(ValueError):
            self.helper.clock_evidence({'owner_confirmed_utc': 0})

    def test_ota_and_configuration_exclusion_and_recheck_hostname_race(self):
        net = DemoNetwork(); controller = Controller(self.store, net, {'password': 'synthetic-password'}, demo=True)
        controller.update(devices=net.devices())
        self.helper.mounted()
        with self.helper.maintenance():
            self.assertFalse(controller.update_safe())
            with self.assertRaises(Invalid): controller.save({'hostname': 'renamed-bridge'})
            with self.assertRaises(ValueError): self.prepare()
        with controller.certificates.config_guard(), self.assertRaises(ValueError): self.prepare()
        self.helper.atomic(self.state / 'updates/transaction.json', b'[]')
        with self.assertRaises(ValueError): self.prepare()
        (self.state / 'updates/transaction.json').unlink()
        first = self.prepare(); old = (self.state / 'tls/current.json').read_bytes()
        real = self.helper.command; raced = False
        def edit_without_supported_lock(args):
            nonlocal raced
            result = real(args)
            if '-enddate' in args and not raced:
                raced = True
                # The supported Controller.save cannot race (above). A root text
                # editor can bypass it, so publication independently rechecks.
                self.store.save(dict(self.store.config, hostname='outside-lock-rename'))
            return result
        with patch.object(self.helper, 'command', edit_without_supported_lock), self.assertRaises(ValueError):
            self.helper.prepare({'owner_confirmed_utc': int(time.time())}, renew=True)
        self.assertEqual((self.state / 'tls/current.json').read_bytes(), old)
        self.assertEqual(json.loads(old)['generation'], first['generation'])


class TrustedConnection(http.client.HTTPSConnection):
    def __init__(self, port, context, hostname):
        super().__init__('127.0.0.1', port, context=context, timeout=5); self.name = hostname

    def connect(self):
        self.sock = self._context.wrap_socket(socket.create_connection(('127.0.0.1', self.port), 5), server_hostname=self.name)


@unittest.skipUnless(OPENSSL, 'OpenSSL required')
class HTTPSPairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.state, self.store = diskless(self.root)
        self.helper = Certificates(self.root, run=execute)
        self.identity = self.helper.prepare({'owner_confirmed_utc': int(time.time())})
        self.name = self.helper.hostname()
        net = DemoNetwork(); self.controller = Controller(self.store, net, {'password': 'synthetic-password'}, demo=True)
        self.controller.update(devices=net.devices()); self.controller.cycle(0)
        self.http = WebServer(('127.0.0.1', 0), self.controller)
        threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': .1}, daemon=True).start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
        self.service = HTTPSService(self.http, '127.0.0.1', 0, CertificateState(self.state, run=execute)); self.service.refresh()
        self.addCleanup(self.service.close)
        self.assertIsNotNone(self.service.server)
        ca = self.state / 'tls/generations' / self.identity['generation'] / 'ca.pem'
        self.context = ssl.create_default_context(cafile=ca)

    def request(self, secure, method='GET', path='/api/status', data=None, headers=None):
        server = self.service.server if secure else self.http
        c = TrustedConnection(server.server_port, self.context, self.name) if secure else http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        host = f'{self.name if secure else "127.0.0.1"}:{server.server_port}'
        h = {'Host': host, 'Content-Type': 'application/json'}
        if method != 'GET': h['Origin'] = ('https' if secure else 'http') + '://' + host
        h.update(headers or {})
        c.request(method, path, json.dumps(data) if data is not None else None, h)
        r = c.getresponse(); body = r.read(); values = dict(r.getheaders()); code = r.status; c.close()
        return code, json.loads(body) if values.get('Content-Type', '').startswith('application/json') else body, values

    def login(self, secure=True, native=False):
        code, data, headers = self.request(secure, 'POST', '/api/v1/auth/token' if native else '/api/login', {'password': 'synthetic-password'})
        self.assertEqual(code, 200)
        return data['access_token'] if native else headers['Set-Cookie'].split(';')[0], data

    def test_real_trusted_tls_cookie_bearer_transport_and_origin_boundaries(self):
        secure, info = self.login(); plain, _ = self.login(False)
        self.assertTrue(secure.startswith('__Host-bridge_session=')); self.assertTrue(plain.startswith('bridge_session='))
        code, _, headers = self.request(True, 'POST', '/api/login', {'password': 'synthetic-password'})
        self.assertIn('; Secure', headers['Set-Cookie']); self.assertIn('; Path=/', headers['Set-Cookie']); self.assertNotIn('Domain=', headers['Set-Cookie'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request(True, headers={'Cookie': secure})[0], 200)
        self.assertEqual(self.request(False, headers={'Cookie': plain})[0], 200)
        self.assertEqual(self.request(True, headers={'Cookie': plain})[0], 401)
        self.assertEqual(self.request(False, headers={'Cookie': secure})[0], 401)
        # Renaming the cookie cannot bypass the transport stored with the token.
        self.assertEqual(self.request(True, headers={'Cookie': plain.replace('bridge_session=', '__Host-bridge_session=')})[0], 401)
        tls_token, _ = self.login(True, True); http_token, _ = self.login(False, True)
        for transport, token, expected in ((True, tls_token, 200), (False, http_token, 200), (True, http_token, 401), (False, tls_token, 401)):
            self.assertEqual(self.request(transport, path='/api/v1/status', headers={'Authorization': 'Bearer ' + token})[0], expected)
        for headers in ({'Host': 'evil.local:443'}, {'Host': self.name + ':80'}, {'Host': self.name + ':0'}, {'Host': self.name + ':'}, {'Host': 'user@' + self.name}):
            self.assertEqual(self.request(True, headers=headers)[0], 421)
        for origin in ('http://' + self.name, 'https://evil.local', 'https://' + self.name + ':80', 'https://' + self.name + ':0'):
            self.assertEqual(self.request(True, 'POST', '/api/login', {'password': 'synthetic-password'}, {'Origin': origin})[0], 403)
        self.assertEqual(self.request(False, headers={'Cookie': plain, 'X-Forwarded-Proto': 'https'})[0], 200)
        self.assertEqual(self.request(True, 'POST', '/api/reboot', {}, {'Cookie': secure})[0], 403)
        self.assertNotIn('PRIVATE KEY', json.dumps(self.request(True, headers={'Cookie': secure})[1]))

    def test_shared_auth_failure_budget_and_sessions_across_transports(self):
        for i in range(5):
            self.assertEqual(self.request(i % 2 == 0, 'POST', '/api/login', {'password': 'wrong-password'})[0], 401)
        self.assertEqual(self.request(False, 'POST', '/api/login', {'password': 'synthetic-password'})[0], 429)
        self.assertEqual(self.request(True, 'POST', '/api/login', {'password': 'synthetic-password'})[0], 429)

    def test_successful_polling_over_sixty_connections_and_parallel_public_assets(self):
        cookie, _ = self.login()
        for _ in range(65):
            self.assertEqual(self.request(True, headers={'Cookie': cookie})[0], 200)
        failures = []
        def asset():
            try:
                if self.request(True, path='/style.css')[0] != 200: failures.append('status')
            except Exception as error: failures.append(type(error).__name__)
        workers = [threading.Thread(target=asset) for _ in range(11)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=5)
        self.assertEqual(failures, [])
        # Several real page-poll intervals; successful events/status traffic does
        # not enter the failed-handshake rate limiter.
        for _ in range(3):
            self.assertEqual(self.request(True, path='/api/events', headers={'Cookie': cookie})[0], 200)
            self.assertEqual(self.request(True, headers={'Cookie': cookie})[0], 200)
            time.sleep(3)
        self.assertEqual(self.service.server.handshake_attempts, {})

    def test_slow_and_malformed_handshakes_are_bounded_and_http_remains_available(self):
        server = self.service.server; server.handshake_timeout = .3
        slow = [socket.create_connection(server.server_address, timeout=2) for _ in range(24)]
        try:
            start = time.monotonic()
            self.assertEqual(self.request(False, path='/')[0], 200)
            self.assertLess(time.monotonic()-start, 1)
            self.assertEqual(len(server.handshake_workers), 2)
            self.assertLessEqual(server.handshake_queue.qsize(), 16)
            time.sleep(.5)
        finally:
            for sock in slow: sock.close()
        self.assertEqual(self.request(False, path='/')[0], 200)
        # Failure state is bounded and only failures enter it.
        self.assertLessEqual(len(server.handshake_attempts), 512)
        server.handshake_attempts.clear()
        bad = socket.create_connection(server.server_address, timeout=2); bad.sendall(b'not a TLS client\r\n'); bad.close()
        time.sleep(.1)
        self.assertEqual(self.request(False, path='/')[0], 200)

    def test_renewal_context_swap_failure_expiry_disable_and_http_recovery(self):
        original = self.service.server; context = original.context
        cookie, _ = self.login()
        old_ca = self.identity['fingerprint_sha256']
        renewed = self.helper.prepare({'owner_confirmed_utc': int(time.time())}, renew=True)
        self.service.refresh()
        self.assertIs(self.service.server, original); self.assertIsNot(original.context, context)
        self.assertEqual(renewed['fingerprint_sha256'], old_ca)
        self.assertEqual(self.request(True, headers={'Cookie': cookie})[0], 200)
        # Reset+Setup can happen between watcher ticks. Observing only the new
        # generation must still revoke the preceding CA's sessions.
        old_connection = TrustedConnection(original.server_port, self.context, self.name)
        old_connection.connect()
        self.helper.deactivate(reset=True)
        replaced = self.helper.prepare({'owner_confirmed_utc': int(time.time())})
        self.service.refresh()
        self.assertNotEqual(replaced['fingerprint_sha256'], old_ca)
        self.assertFalse(any(v.get('transport') == 'https' for v in self.http.sessions.values()))
        host = f'{self.name}:{original.server_port}'
        old_connection.request('POST', '/api/login', json.dumps({'password': 'synthetic-password'}),
                               {'Host': host, 'Origin': 'https://' + host, 'Content-Type': 'application/json'})
        response = old_connection.getresponse(); response.read(); self.assertEqual(response.status, 403); old_connection.close()
        with patch.object(self.service.state, 'context', side_effect=ValueError('candidate invalid')):
            self.service.generation = 'force-reload'; self.service.refresh()
            self.assertIs(self.service.server, original)
        self.service.expires = time.time()-1
        with patch.object(self.service.state, 'context', side_effect=ValueError('expired')):
            self.service.refresh(); self.assertIsNone(self.service.server)
        self.assertEqual(self.request(False, path='/')[0], 200)
        self.service.refresh(); self.assertIsNotNone(self.service.server)
        self.helper.deactivate(); self.service.refresh(); self.assertIsNone(self.service.server)
        self.assertFalse(any(v.get('transport') == 'https' for v in self.http.sessions.values()))
        self.assertEqual(self.request(False, path='/')[0], 200)
        # A valid active TLS state blocks supported hostname changes until disable.
        self.helper.deactivate(reset=True)
        self.helper.prepare({'owner_confirmed_utc': int(time.time())})
        rename = dict(self.store.config, hostname='new-host', home={'ssid': 'synthetic-home', 'open': True, 'mac': self.controller.state['devices'][0]['mac']}, telescopes=[])
        with self.assertRaisesRegex(Invalid, 'Disable HTTPS'): self.controller.save(rename)
        self.helper.deactivate()
        self.controller.save(rename)
        self.service.refresh(); self.assertIsNone(self.service.server)
