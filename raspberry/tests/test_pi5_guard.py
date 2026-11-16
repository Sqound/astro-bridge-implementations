"""File and simulated-command tests for onboard first boot and restart guards."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'image/pi5'
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

guard = load('pi5_guard', 'guard.py')
release = load('pi5_release', 'hotspot-release.py')
firstboot = load('pi5_firstboot_guard_test', 'firstboot.py')


class GuardTests(unittest.TestCase):
    mac = 'aa:bb:cc:dd:ee:ff'
    def seeded(self):
        seed = {'ssid':'Home', 'psk':'a'*64, 'hidden':False, 'country':'JP'}
        raw = firstboot.SEED_MAGIC + json.dumps(seed).encode() + b'\n'
        raw += bytes(firstboot.SEED_SIZE-len(raw))
        return firstboot.initial_config(self.mac, raw)

    def test_guard_accepts_seed_and_only_our_connected_home(self):
        config = self.seeded()
        self.assertTrue(guard.owner_shape(config, self.mac))
        default = firstboot.initial_config(self.mac, None)
        self.assertTrue(guard.owner_shape(default, self.mac))
        self.assertFalse(guard.owner_shape(config, '11:22:33:44:55:66'))
        def calls(connection='wifi-bridge-home', ssid='Home', state='100 (connected)'):
            return [Mock(returncode=0, stdout=state), Mock(returncode=0, stdout=connection),
                    Mock(returncode=0, stdout='Connected to 12:34:56:78:90:ab (on wlan0)\n\tSSID: ' + ssid + '\n')]
        with patch.object(guard.subprocess, 'run', side_effect=calls()): self.assertTrue(guard.nm_ready(config))
        with patch.object(guard.subprocess, 'run', side_effect=calls(connection='other')): self.assertFalse(guard.nm_ready(config))
        with patch.object(guard.subprocess, 'run', side_effect=calls(ssid='Other')): self.assertFalse(guard.nm_ready(config))
        with patch.object(guard.subprocess, 'run', return_value=Mock(returncode=0, stdout='30 (disconnected)')):
            self.assertTrue(guard.nm_ready(config))

    def test_second_radio_marker_identity_and_missing_radio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root/'state'; state.mkdir()
            net = root/'net'; radio = net/'wlan1'; radio.mkdir(parents=True)
            mac = '11:22:33:44:55:66'; (radio/'address').write_text(mac)
            marker = state/'hotspot.json'
            (state/'hostapd.conf').write_text('interface=wlan1\n')
            def path_override(path): return net if path == '/sys/class/net' else Path(path)
            calls = []
            def call(args, check=True):
                calls.append(args); return Mock(returncode=3 if args[0] == 'rc-service' else 0, stdout='')
            with patch.object(release, 'STATE', state), patch.object(release, 'Path', side_effect=path_override), patch.object(release, 'call', side_effect=call):
                marker.write_text(json.dumps({'interface':'wlan1', 'mac':mac}))
                release.release(); self.assertIn(['nmcli','device','set','wlan1','managed','yes'], calls)
                for record in ({'interface':'wlan1'}, {'interface':'wlan1', 'mac':'aa:bb:cc:dd:ee:ff'}):
                    calls.clear(); marker.write_text(json.dumps(record)); release.release()
                    self.assertFalse(marker.exists()); self.assertTrue(all(c[0] == 'rc-service' for c in calls))
                (radio/'address').unlink(); radio.rmdir(); calls.clear()
                marker.write_text(json.dumps({'interface':'wlan1', 'mac':mac})); release.release()
                self.assertFalse(marker.exists()); self.assertTrue(all(c[0] == 'rc-service' for c in calls))
                marker.write_text(json.dumps({'interface':'wlan1', 'mac':mac}))
                with patch.object(release, 'call', return_value=Mock(returncode=0)):
                    with self.assertRaises(ValueError): release.release()
                self.assertTrue(marker.exists())

    def test_explicit_second_ap_and_home_role_survive_guard(self):
        config = self.seeded(); other = '11:22:33:44:55:66'
        config['interface_roles'] = {self.mac:'home', other:'hotspot'}
        config['hotspot']['mac'] = other
        self.assertTrue(guard.owner_shape(config, self.mac))
        calls = [Mock(returncode=0, stdout='100 (connected)'), Mock(returncode=0, stdout='wifi-bridge-home'),
                 Mock(returncode=0, stdout='Connected to 12:34:56:78:90:ab (on wlan0)\n\tSSID: Home\n')]
        with patch.object(guard.subprocess, 'run', side_effect=calls): self.assertTrue(guard.nm_ready(config, self.mac))

    def test_stopped_recovery_ap_releases_only_proven_onboard_radio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root/'state'; state.mkdir()
            net = root/'net'; radio = net/'wlan0'; (radio/'device').mkdir(parents=True)
            (radio/'address').write_text(self.mac)
            driver = root/'brcmfmac'; driver.mkdir(); subsystem = root/'sdio'; subsystem.mkdir()
            (radio/'device/driver').symlink_to(driver); (radio/'device/subsystem').symlink_to(subsystem)
            (state/'config.json').write_text(json.dumps(self.seeded()))
            marker = state/'hotspot.json'; marker.write_text('{"interface":"wlan0"}')
            def path_override(path): return net if path == '/sys/class/net' else Path(path)
            commands = []
            def fake_call(args, check=True):
                commands.append(args)
                if args[0] == 'rc-service': return Mock(returncode=3)
                if args[:4] == ['ip','-o','-4','address']:
                    return Mock(returncode=0, stdout='inet 192.168.77.1/24 scope global wlan0')
                return Mock(returncode=0, stdout='')
            with patch.object(release, 'STATE', state), patch.object(release, 'Path', side_effect=path_override), patch.object(release, 'call', side_effect=fake_call):
                release.release()
                self.assertFalse(marker.exists())
                self.assertIn(['ip','address','del','192.168.77.1/24','dev','wlan0'], commands)
                self.assertIn(['nmcli','device','set','wlan0','managed','yes'], commands)
                marker.write_text('{"interface":"wlan0"}')
                with patch.object(release, 'call', return_value=Mock(returncode=0)):
                    with self.assertRaises(ValueError): release.release()
                self.assertTrue(marker.exists())
                with patch.object(release, 'call', return_value=Mock(returncode=1)):
                    with self.assertRaises(ValueError): release.release()
                config = self.seeded(); config['home']['mac'] = '11:22:33:44:55:66'
                (state/'config.json').write_text(json.dumps(config))
                release.release(); self.assertFalse(marker.exists())
