"""Pi 5 first boot without a home network: the hotspot must come back on its own.

The Pi 5 image gives the onboard radio to the hotspot when no home network was
prepared. These tests cover what happens when that access point stops on its own
after starting: the bridge must hand its radio back, restart the hotspot, move it
to another free adapter if the onboard radio keeps failing, and say so in the
service log. They use simulated system commands and never touch a host network.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wifi_bridge.config import Store
from wifi_bridge.controller import Controller, HOTSPOT_UNCONFIRMED_GRACE, hotspot_log
from wifi_bridge.network import DemoNetwork, LinuxNetwork, NetworkError

spec = importlib.util.spec_from_file_location('pi5_firstboot', Path(__file__).resolve().parents[1] / 'image/pi5/firstboot.py')
firstboot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firstboot)

ONBOARD = '02:00:00:aa:bb:01'


class StoppedOnboardAccessPointTests(unittest.TestCase):
    """Network layer: an AP that stopped on its own leaves its radio unmanaged."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.linux = LinuxNetwork(self.tmp.name)
        self.marker = Path(self.tmp.name, 'hotspot.json')
        self.devices = [{'name': 'wlan0', 'mac': ONBOARD, 'usb': False},
                        {'name': 'wlan1', 'mac': '02:00:00:aa:bb:02', 'usb': True}]
        self.calls = []

    def system(self, services='inactive\ninactive\n', state='10 (unmanaged)'):
        def run(args, **kwargs):
            self.calls.append(args)
            if args[0] == 'systemctl': return services
            if args[:2] == ['nmcli', '-g']: return state
            if args[-1] == 'link': return 'Not connected.'
            if args[-1] == 'info': return 'Interface wlan0\n\ttype managed\n'
            return ''
        return run

    def test_stopped_ap_on_unmanaged_owned_radio_is_handed_back(self):
        # State read on the bench console: wlan0 unmanaged, both USB radios
        # disconnected, the bridge service running and its log silent.
        self.marker.write_text(json.dumps({'interface': 'wlan0', 'mac': ONBOARD}))
        with patch('wifi_bridge.network.run', side_effect=self.system()):
            self.assertTrue(self.linux.release_stale_hotspot(self.devices))
        self.assertFalse(self.marker.exists())
        self.assertIn(['ip', 'address', 'del', '192.168.77.1/24', 'dev', 'wlan0'], self.calls)
        self.assertIn(['nmcli', 'device', 'set', 'wlan0', 'managed', 'yes'], self.calls)
        self.assertFalse(any('stop' in c or 'restart' in c for c in self.calls))

    def test_record_without_matching_identity_or_running_service_is_kept(self):
        for record, services in [({'interface': 'wlan0', 'mac': '02:00:00:aa:bb:09'}, 'inactive\ninactive\n'),
                                 ({'interface': 'wlan0'}, 'inactive\ninactive\n'),
                                 ({'interface': 'wlan0', 'mac': ONBOARD}, 'active\ninactive\n'),
                                 ({'interface': 'wlan0', 'mac': ONBOARD}, 'inactive\nactive\n')]:
            with self.subTest(record=record, services=services):
                self.calls.clear()
                self.marker.write_text(json.dumps(record))
                with patch('wifi_bridge.network.run', side_effect=self.system(services)):
                    self.assertFalse(self.linux.release_stale_hotspot(self.devices))
                self.assertTrue(self.marker.exists())
                self.assertFalse(any(c[:3] == ['nmcli', 'device', 'set'] or c[:2] == ['ip', 'address'] for c in self.calls))

    def test_recorded_owner_refuses_every_other_radio(self):
        # Why the 1.1.0 bridge stayed without a hotspot: while the stopped AP is
        # still recorded, a start on a free USB radio is refused every cycle.
        self.marker.write_text(json.dumps({'interface': 'wlan0', 'mac': ONBOARD}))
        with patch('wifi_bridge.network.run', side_effect=self.system()):
            with self.assertRaises(NetworkError):
                self.linux.hotspot(self.devices[1], 'Astro-Bridge-AABB01', '12345678')
        self.assertEqual(self.linux.hotspot_owner(), 'wlan0')


class RadioLeavesAccessPointModeTests(unittest.TestCase):
    """Bench boot: hostapd reported AP-ENABLED, then the radio was a station again."""

    def start(self, types):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        net = LinuxNetwork(tmp.name)
        observed = iter(types)
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ['iw', 'dev'] and args[-1] == 'info':
                return 'Interface wlan0\n\ttype ' + next(observed, 'AP') + '\n'
            return ''
        device = {'name': 'wlan0', 'mac': ONBOARD, 'usb': False}
        with patch('wifi_bridge.network.run', side_effect=run), patch('wifi_bridge.network.time.sleep'), \
                patch.object(net, '_wait_nm_unmanaged'), patch.object(net, '_wait_hotspot_ready'), \
                patch.object(net, 'hotspot_dhcp'), patch.object(net, 'connected', return_value=False):
            try:
                net.hotspot(device, 'Astro-Bridge-AABB01', '12345678')
                return calls, None
            except NetworkError as error:
                return calls, error

    def test_radio_back_to_station_mode_restarts_the_access_point_once(self):
        calls, error = self.start(['AP', 'AP', 'managed'])
        self.assertIsNone(error)
        self.assertEqual(calls.count(['systemctl', 'restart', 'wifi-bridge-ap']), 1)

    def test_second_loss_is_a_start_failure_and_releases_the_radio(self):
        calls, error = self.start(['managed'] + ['AP'] * 3 + ['managed'])
        self.assertIsInstance(error, NetworkError)
        self.assertIn(['nmcli', 'device', 'set', 'wlan0', 'managed', 'yes'], calls)

    def test_stable_access_point_is_not_restarted(self):
        calls, error = self.start([])
        self.assertIsNone(error)
        self.assertNotIn(['systemctl', 'restart', 'wifi-bridge-ap'], calls)


class StationManagerStillHoldsTheRadioTests(unittest.TestCase):
    """Clean boot seen on the bench: keys refused at the first start, then a silent station radio."""

    def start(self, listings):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        net = LinuxNetwork(tmp.name)
        observed = iter(listings)
        calls, sleeps = [], []
        def run(args, **kwargs):
            calls.append(args)
            if args == ['iw', 'dev']: return next(observed, 'phy#0\n\tInterface wlan0\n\t\ttype managed\n')
            if args[:2] == ['iw', 'dev'] and args[-1] == 'info': return 'Interface wlan0\n\ttype AP\n'
            return ''
        with patch('wifi_bridge.network.run', side_effect=run), patch('wifi_bridge.network.time.sleep', side_effect=sleeps.append), \
                patch.object(net, '_wait_nm_unmanaged'), patch.object(net, '_wait_hotspot_ready'), \
                patch.object(net, 'hotspot_dhcp'), patch.object(net, 'connected', return_value=False):
            net.hotspot({'name': 'wlan0', 'mac': ONBOARD, 'usb': False}, 'Astro-Bridge-AABB01', '12345678')
        start = calls.index(['systemctl', 'restart', 'wifi-bridge-ap', 'wifi-bridge-dhcp'])
        return calls[:start].count(['iw', 'dev']), calls

    def test_access_point_starts_only_after_the_peer_device_of_the_station_manager_is_gone(self):
        held = 'phy#0\n\tInterface p2p-dev-wlan0\n\t\ttype P2P-device\n\tInterface wlan0\n\t\ttype managed\n'
        readings, _ = self.start([held, held, held])
        self.assertEqual(readings, 4)

    def test_wait_is_bounded_and_other_radios_do_not_count(self):
        held = 'phy#0\n\tInterface p2p-dev-wlan0\n\t\ttype P2P-device\n'
        readings, calls = self.start([held] * 40)
        self.assertEqual(readings, 16)
        self.assertIn(['systemctl', 'restart', 'wifi-bridge-ap', 'wifi-bridge-dhcp'], calls)
        other = 'phy#1\n\tInterface p2p-dev-wlan1\n\t\ttype P2P-device\n'
        readings, _ = self.start([other] * 40)
        self.assertEqual(readings, 1)

    def test_enabled_hostapd_on_a_station_radio_is_not_a_hotspot(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        net = LinuxNetwork(tmp.name)
        (Path(tmp.name) / 'hotspot.json').write_text(json.dumps({'interface': 'wlan0', 'mac': ONBOARD}))
        answers = {'type': 'managed'}
        def run(args, **kwargs):
            if args[0] == 'systemctl': return 'active\nactive'
            if args[:3] == ['ip', '-j', '-4']: return json.dumps([{'addr_info': [{'scope': 'global', 'local': '192.168.77.1', 'prefixlen': 24}]}])
            if args[:2] == ['iw', 'dev']: return 'Interface wlan0\n\ttype ' + answers['type'] + '\n'
            if args[0] == 'hostapd_cli': return 'state=ENABLED\n'
            return ''
        with patch('wifi_bridge.network.run', side_effect=run):
            self.assertFalse(net.connected('setup', {'name': 'wlan0', 'mac': ONBOARD}))
            answers['type'] = 'AP'
            self.assertTrue(net.connected('setup', {'name': 'wlan0', 'mac': ONBOARD}))


class FlakyAccessPoints(DemoNetwork):
    """Simulated radios whose AP can stop by itself while its record stays."""

    def __init__(self):
        super().__init__(radios=3)
        self.owner = ''
        self.refuse = set()
        self.starts = []
        self.stops = 0

    def devices(self):
        devices = super().devices()
        devices[0]['mac'] = ONBOARD
        return devices

    def hotspot_owner(self):
        return self.owner

    def hotspot(self, device, ssid, password, targets=()):
        if self.owner and 'setup' not in self.active:
            raise NetworkError('The previous hotspot owner is not safely released.')
        if device['name'] in self.refuse:
            raise NetworkError('The hotspot or its DHCP server did not start.')
        super().hotspot(device, ssid, password, targets)
        self.owner = device['name']
        self.starts.append(device['name'])

    def radio_idle(self, device):
        return super().radio_idle(device) and device['name'] != self.owner

    def disconnect(self, role):
        super().disconnect(role)
        if role == 'setup' and self.owner:
            self.owner = ''
            self.stops += 1

    def die(self):
        # hostapd stopped: the AP is gone but the owner record remains.
        self.active.discard('setup')
        self.associations.pop('setup', None)


class FirstBootHotspotRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.net = FlakyAccessPoints()
        devices = self.net.devices()
        self.store = Store(self.tmp.name, ONBOARD)
        # Exactly the identity written by the Pi 5 first boot without a Wi-Fi seed.
        self.store.save(firstboot.initial_config(ONBOARD, None))
        self.controller = Controller(self.store, self.net, {'password': '12345678'}, demo=True)
        self.controller.update(devices=devices)
        # Demo controllers stay silent; this one writes the real service-log lines.
        self.controller.log = hotspot_log
        self.log = io.StringIO()

    def cycle(self, now):
        with contextlib.redirect_stdout(self.log):
            self.controller.cycle(now)

    def test_first_boot_starts_the_hotspot_on_the_onboard_radio(self):
        self.cycle(0)
        self.assertEqual(self.net.starts, ['wlan0'])
        self.assertTrue(self.controller.status()['hotspot']['active'])
        self.assertIn('Astro Bridge hotspot: the hotspot started on wlan0.', self.log.getvalue())

    def test_stopped_hotspot_is_restarted_then_moved_to_a_usb_adapter(self):
        self.cycle(0)
        self.net.die()
        self.cycle(5)
        self.assertFalse(self.controller.status()['hotspot']['active'])
        self.assertIn('the hotspot on wlan0 stopped', self.log.getvalue())
        # The record is kept during a grace period, then released and restarted.
        self.cycle(5 + HOTSPOT_UNCONFIRMED_GRACE - 1)
        self.assertEqual(self.net.stops, 0)
        self.cycle(5 + HOTSPOT_UNCONFIRMED_GRACE + 1)
        self.assertEqual(self.net.stops, 1)
        self.assertTrue(self.controller.status()['hotspot']['active'])
        self.assertEqual(self.net.owner, 'wlan0')
        # A second stop on the onboard radio sends the hotspot to a USB adapter,
        # without rewriting the saved hotspot adapter.
        self.net.die()
        self.cycle(100)
        self.cycle(100 + HOTSPOT_UNCONFIRMED_GRACE + 1)
        self.assertEqual(self.net.owner, 'wlan1')
        self.assertTrue(self.controller.status()['hotspot']['active'])
        self.assertEqual(self.store.config['hotspot']['mac'], ONBOARD)
        self.assertIn('Another free Wi-Fi adapter is tried first', self.log.getvalue())
        # The working fallback is kept while the onboard radio is free again.
        self.cycle(200)
        self.cycle(205)
        self.assertEqual(self.net.owner, 'wlan1')
        self.assertEqual(self.net.starts, ['wlan0', 'wlan0', 'wlan1'])

    def test_onboard_radio_that_cannot_start_falls_back_in_the_same_cycle(self):
        self.net.refuse.add('wlan0')
        self.cycle(0)
        self.assertEqual(self.net.owner, 'wlan1')
        self.assertIn('the hotspot could not start on wlan0', self.log.getvalue())
        self.assertIn('the hotspot started on wlan1.', self.log.getvalue())

    def test_demo_mode_writes_no_hotspot_log_line(self):
        quiet = Controller(self.store, FlakyAccessPoints(), {'password': '12345678'}, demo=True)
        quiet.update(devices=self.net.devices())
        with contextlib.redirect_stdout(self.log):
            quiet.cycle(0)
        self.assertEqual(self.log.getvalue(), '')

    def test_log_lines_carry_no_network_name_or_password(self):
        self.net.refuse.add('wlan0')
        self.cycle(0)
        self.net.die()
        for now in (5, 30, 60):
            self.cycle(now)
        text = self.log.getvalue()
        self.assertNotIn('12345678', text)
        self.assertNotIn('Astro-Bridge-', text)
        self.assertLess(len(text.splitlines()), 12)


if __name__ == '__main__':
    unittest.main()
