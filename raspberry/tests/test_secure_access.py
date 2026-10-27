"""HTTPS lifecycle API jobs use real temporary OS helper effects, never global trust."""
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.tls import Certificates, CertificateError
from wifi_bridge.api import ApiError
from wifi_bridge.controller import Controller
from wifi_bridge.network import DemoNetwork
from wifi_bridge.secure_access import SecureAccess
from wifi_bridge.tls import CertificateState, HTTPSService
from wifi_bridge.web import WebServer
from test_tls import diskless, execute, OPENSSL, TrustedConnection


@unittest.skipUnless(OPENSSL, 'OpenSSL required')
class SecureAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.state, self.store = diskless(self.root)
        self.helper = Certificates(self.root, run=execute)
        net = DemoNetwork(); self.controller = Controller(self.store, net, {'password': 'synthetic-password'}, demo=True)
        self.controller.update(devices=net.devices()); self.controller.cycle(0)
        self.http = WebServer(('127.0.0.1', 0), self.controller)
        self.http.secure_access.close()
        self.calls = []
        self.access = SecureAccess(self.controller, capable=lambda: True, invoke=self.invoke)
        self.http.secure_access = self.access
        self.service = HTTPSService(self.http, '127.0.0.1', 0, CertificateState(self.state, run=execute))
        threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': .1}, daemon=True).start()
        self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown); self.addCleanup(self.service.close)
        self.cookie = ''; self.csrf = ''; self.token = ''

    def invoke(self, action, request):
        self.calls.append((action, dict(request)))
        try:
            if action == 'diagnose': return self.helper.diagnose()
            if action in ('setup', 'renew'): return self.helper.prepare(request, action == 'renew')
            if action == 'enable': return self.helper.enable()
            return self.helper.deactivate(action == 'reset')
        except CertificateError as error: raise ApiError(409, error.code, 'Private helper action refused') from None
        except Exception: raise ApiError(409, 'HTTPS_OPERATION_FAILED', 'Private helper action failed') from None

    def request(self, method='GET', path='/api/https', data=None, auth=True, csrf=True):
        c = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        host = f'127.0.0.1:{self.http.server_port}'
        headers = {'Host': host, 'Origin': 'http://' + host, 'Content-Type': 'application/json'}
        if auth: headers.update(Cookie=self.cookie, Authorization='Bearer ' + self.token)
        if csrf: headers['X-CSRF-Token'] = self.csrf
        c.request(method, path, json.dumps(data) if data is not None else None, headers)
        r = c.getresponse(); body = r.read(); code = r.status; h = dict(r.getheaders()); c.close()
        return code, json.loads(body) if h.get('Content-Type', '').startswith('application/json') else body, h

    def login(self):
        code, result, headers = self.request('POST', '/api/login', {'password': 'synthetic-password'}, auth=False)
        self.assertEqual(code, 200); self.cookie = headers['Set-Cookie'].split(';')[0]; self.csrf = result['csrf']
        code, result, _ = self.request('POST', '/api/v1/auth/token', {'password': 'synthetic-password'}, auth=False)
        self.assertEqual(code, 200); self.token = result['access_token']

    def finish(self):
        self.access.thread.join(timeout=10)
        self.assertFalse(self.access.thread.is_alive())
        return self.access.status()

    def setup_identity(self):
        self.login()
        code, _, _ = self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password', 'owner_confirmed_utc': int(time.time())})
        self.assertEqual(code, 202); status = self.finish()
        self.assertEqual(status['operation']['state'], 'succeeded'); self.assertTrue(status['listener']['ready'])
        return status

    def test_authenticated_browser_and_native_contract_csrf_reauth_and_fields(self):
        for path in ('/api/https', '/api/https/certificate', '/api/v1/system/https', '/api/v1/system/https/certificate'):
            self.assertEqual(self.request(path=path, auth=False)[0], 401)
        self.login()
        self.assertEqual(self.request('POST', data={'action': 'setup'}, csrf=False)[0], 403)
        for value in ({'action': []}, {'action': 'arbitrary'}, {'action': 'setup', 'path': '/tmp/keys'}, {'action': 'reset', 'current_password': 'synthetic-password'}, {'action': 'setup', 'owner_confirmed_utc': True}):
            self.assertEqual(self.request('POST', data=value)[0], 400)
        code, value, _ = self.request('POST', '/api/v1/system/https', {'action': 'setup', 'current_password': 'wrong-password'})
        self.assertEqual(code, 403); self.assertEqual(value['error']['code'], 'REAUTHENTICATION_FAILED')
        self.assertEqual(self.calls, [])
        for _ in range(4): self.assertEqual(self.request('POST', data={'action': 'setup', 'current_password': 'wrong-password'})[0], 403)
        self.assertEqual(self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password'})[0], 429)
        self.assertEqual(self.controller.certificate_job_pending, False)

    def test_real_setup_ca_download_fresh_tls_login_and_native_disable_enable(self):
        status = self.setup_identity()
        self.assertEqual(status['identity']['issuance_clock_evidence'], 'owner-confirmed-current-utc')
        self.assertIsNotNone(status['identity']['ca_expires']); self.assertIsNotNone(status['identity']['leaf_expires'])
        ca_code, ca, headers = self.request(path='/api/https/certificate')
        self.assertEqual(ca_code, 200); self.assertIn(b'BEGIN CERTIFICATE', ca); self.assertNotIn(b'PRIVATE KEY', ca)
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertEqual(headers['Content-Type'], 'application/x-pem-file')
        import plistlib
        profile_code, profile, profile_headers = self.request(path='/api/https/profile')
        self.assertEqual(profile_code, 200)
        document = plistlib.loads(profile)
        self.assertEqual(document['PayloadType'], 'Configuration')
        self.assertEqual(len(document['PayloadContent']), 1)
        public = document['PayloadContent'][0]
        self.assertEqual(public['PayloadType'], 'com.apple.security.root')
        self.assertEqual(public['PayloadContent'], ssl.PEM_cert_to_DER_cert(ca.decode()))
        self.assertNotIn(b'PRIVATE KEY', profile)
        self.assertEqual(profile_headers['Content-Type'], 'application/x-apple-aspen-config')
        self.assertIn('astro-bridge.mobileconfig', profile_headers['Content-Disposition'])

        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(self.request(path='/api/v1/system/https/certificate')[1], ca)
        client = ssl.create_default_context(cadata=ca.decode())
        c = TrustedConnection(self.service.server.server_port, client, self.helper.hostname())
        host = self.helper.hostname() + ':' + str(c.port)
        c.request('GET', '/api/status', headers={'Host': host, 'Cookie': self.cookie})
        r = c.getresponse(); r.read(); self.assertEqual(r.status, 401); c.close()
        original = status['identity']['fingerprint_sha256']
        for action, ready in (('disable', False), ('enable', True)):
            code, body, _ = self.request('POST', '/api/v1/system/https', {'action': action, 'current_password': 'synthetic-password'})
            self.assertEqual(code, 202); self.assertEqual(body['api_version'], '1')
            value = self.finish(); self.assertEqual(value['operation']['state'], 'succeeded')
            self.assertEqual(value['listener']['ready'], ready); self.assertEqual(value['identity']['fingerprint_sha256'], original)
        for _, request in self.calls: self.assertNotIn('current_password', request)

    def test_push_browser_requires_authentication_csrf_and_actual_https(self):
        self.setup_identity()
        self.http.push.available = True
        self.http.push.invoke = lambda request: {'private_key': 'synthetic-private', 'public_key': 'synthetic-public'}
        self.assertEqual(self.request(path='/api/push', auth=False)[0], 401)
        self.assertEqual(self.request('POST', '/api/push', {'action': 'prepare'}, csrf=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/push', {'action': 'prepare'})[0], 409)
        ca = self.access.certificate()
        context = ssl.create_default_context(cadata=ca.decode())
        def request(path, data, cookie='', csrf=''):
            connection = TrustedConnection(self.service.server.server_port, context, self.helper.hostname())
            host = self.helper.hostname() + ':' + str(connection.port)
            connection.request('POST', path, json.dumps(data), {'Host': host, 'Origin': 'https://' + host,
                'Content-Type': 'application/json', 'Cookie': cookie, 'X-CSRF-Token': csrf})
            response = connection.getresponse(); body = json.loads(response.read()); headers = dict(response.getheaders())
            code = response.status; connection.close(); return code, body, headers
        code, result, headers = request('/api/login', {'password': 'synthetic-password'})
        self.assertEqual(code, 200)
        cookie = headers['Set-Cookie'].split(';')[0]; csrf = result['csrf']
        self.assertEqual(request('/api/push', {'action': 'prepare'}, cookie, csrf)[0], 200)
        from test_push import subscription
        self.assertEqual(request('/api/push', {'action': 'subscribe', 'subscription': subscription(), 'locale': 'fr'}, cookie, csrf)[0], 200)
        self.assertEqual(self.http.push.status()['device_count'], 1)
        self.assertEqual(request('/api/logout', {}, cookie, csrf)[0], 200)
        self.assertEqual(self.http.push.status()['device_count'], 0)
        self.assertEqual(request('/api/push', {'action': 'prepare'}, cookie, csrf)[0], 401)

    def test_single_pending_job_coalescing_rate_limit_ota_and_failure_gate(self):
        self.login(); entered = threading.Event(); release = threading.Event(); original = self.access.invoke
        def blocked(action, request): entered.set(); release.wait(5); return original(action, request)
        self.access.invoke = blocked
        code, accepted, _ = self.request('POST', data={'action': 'refresh'}); self.assertEqual(code, 202)
        self.assertTrue(entered.wait(2)); self.assertFalse(self.controller.update_safe())
        code, coalesced, _ = self.request('POST', data={'action': 'refresh'}); self.assertEqual(code, 202)
        self.assertEqual(coalesced['operation']['id'], accepted['operation']['id'])
        self.assertEqual(self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password'})[0], 409)
        self.assertEqual(self.request('POST', '/api/reboot', {})[0], 409)
        self.assertEqual(self.request('POST', '/api/v1/system/reboot', {})[0], 409)
        self.assertIsNone(self.controller.action_at)
        release.set(); self.finish(); self.access.invoke = original
        self.assertTrue(self.controller.update_safe()); self.assertEqual(self.request('POST', data={'action': 'refresh'})[0], 429)
        for stopping in ('reboot', 'stop'):
            if stopping == 'reboot': self.controller.action_at = time.monotonic() + 4
            else: self.controller.stop.set()
            code, body, _ = self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password'})
            self.assertEqual(code, 409); self.assertEqual(body['code'], 'SYSTEM_SHUTTING_DOWN')
            self.controller.action_at = None; self.controller.stop.clear()
        before = len(self.calls)
        for _ in range(10): self.assertEqual(self.request()[0], 200)
        self.assertEqual(len(self.calls), before)  # Polling cannot invoke crypto.
        self.assertGreaterEqual(self.request()[1]['diagnostics_age_seconds'], 0)
        updates = self.state / 'updates'; updates.mkdir(mode=0o700, exist_ok=True)
        self.helper.atomic(updates / 'transaction.json', b'{"phase":"verifying"}')
        self.assertEqual(self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password'})[0], 409)
        (updates / 'transaction.json').unlink()
        self.access.invoke = lambda *args: (_ for _ in ()).throw(TimeoutError('PRIVATE CANARY'))
        self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password'})
        failed = self.finish(); self.assertEqual(failed['operation']['code'], 'HTTPS_OPERATION_FAILED')
        self.assertFalse(self.controller.certificate_job_pending); self.assertNotIn('PRIVATE CANARY', json.dumps(failed))
        with patch('wifi_bridge.secure_access.threading.Thread.start', side_effect=RuntimeError('thread failed')):
            with self.assertRaises(ApiError): self.access.action({'action': 'setup', 'current_password': 'synthetic-password'})
        self.assertFalse(self.controller.certificate_job_pending)

    def test_initial_diagnostics_wait_for_ota_verification_without_blocking_health(self):
        updates = self.state / 'updates'; updates.mkdir(mode=0o700, exist_ok=True)
        journal = updates / 'transaction.json'
        self.helper.atomic(journal, b'{"phase":"verifying"}')
        self.access.initialize()
        self.assertIsNone(self.access.status()['identity'])
        self.assertFalse(self.controller.certificate_job_pending)
        self.assertEqual(self.calls, [])
        self.helper.atomic(journal, b'{"phase":"complete"}')
        self.access.bootstrap_thread.join(timeout=5)
        status = self.finish()
        self.assertIsNotNone(status['identity'])
        self.assertEqual(status['operation']['state'], 'succeeded')
        self.assertEqual(self.calls, [('diagnose', {})])
        for _ in range(10): self.access.status()
        self.assertEqual(self.calls, [('diagnose', {})])

    def test_unsupported_capability_and_old_helper_do_not_offer_mutations(self):
        self.login(); self.access.capable = lambda: False
        code, body, _ = self.request(); self.assertEqual(code, 200); self.assertFalse(body['supported'])
        code, body, _ = self.request('POST', '/api/v1/system/https', {'action': 'setup', 'current_password': 'synthetic-password'})
        self.assertEqual(code, 409); self.assertEqual(body['error']['code'], 'HTTPS_UNSUPPORTED')
        self.assertEqual(self.calls, []); self.assertEqual(self.request(path='/api/https/certificate')[0], 409)

    def test_expired_and_renamed_identity_public_diagnostics_without_private_key_reads(self):
        status = self.setup_identity(); fingerprint = status['identity']['fingerprint_sha256']
        self.helper.deactivate(); self.service.refresh()
        record = self.helper.record(); folder = self.helper.generation(record)
        # Produce a genuinely expired leaf using the real CA, without changing OS time.
        execute([OPENSSL, 'x509', '-req', '-sha256', '-days', '0', '-in', str(folder / 'server.csr'),
                 '-CA', str(folder / 'ca.pem'), '-CAkey', str(folder / 'ca.key'), '-set_serial', '0x1234',
                 '-extfile', str(folder / 'leaf.conf'), '-out', str(folder / 'server.pem')], check=True, capture_output=True, text=True)
        (folder / 'server.pem').chmod(0o600)
        read = self.helper.read; names = []
        def public_read(path): names.append(path.name); return read(path)
        with patch.object(self.helper, 'read', public_read): diagnosis = self.helper.diagnose()
        self.assertEqual(diagnosis['state'], 'leaf_expired'); self.assertEqual(diagnosis['fingerprint_sha256'], fingerprint)
        self.assertNotIn('ca.key', names); self.assertNotIn('server.key', names)
        self.access.last_refresh = None
        self.request('POST', data={'action': 'refresh'}); value = self.finish()
        self.assertFalse(value['listener']['ready']); self.assertEqual(self.request(path='/api/https/certificate')[0], 200)
        self.request('POST', data={'action': 'enable', 'current_password': 'synthetic-password'})
        self.assertEqual(self.finish()['operation']['state'], 'failed')
        self.store.save(dict(self.store.config, hostname='renamed-fixture'))
        self.assertEqual(self.helper.diagnose()['state'], 'hostname_mismatch')
        self.assertEqual(self.helper.diagnose()['fingerprint_sha256'], fingerprint)

    def test_clock_error_is_explicit_and_reset_requires_new_identity(self):
        self.login()
        self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password'})
        value = self.finish(); self.assertEqual(value['operation']['state'], 'failed')
        self.assertEqual(value['operation']['code'], 'HTTPS_CLOCK_UNVERIFIED'); self.assertFalse(value['listener']['ready'])
        self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password', 'owner_confirmed_utc': int(time.time())})
        before = self.finish()['identity']['fingerprint_sha256']
        self.request('POST', data={'action': 'reset', 'current_password': 'synthetic-password', 'confirm_reset': True})
        self.assertFalse(self.finish()['identity']['configured']); self.assertIsNone(self.service.server)
        self.request('POST', data={'action': 'setup', 'current_password': 'synthetic-password', 'owner_confirmed_utc': int(time.time())})
        self.assertNotEqual(self.finish()['identity']['fingerprint_sha256'], before)
