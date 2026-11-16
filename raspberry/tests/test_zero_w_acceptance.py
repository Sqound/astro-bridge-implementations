"""Zero W acceptance evaluation never promotes missing evidence or rounded boundary values."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1]/'tools'/name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


summary = load('zero-w-summary.py')
bench = load('load-bench.py')


class ZeroWAcceptance(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.build = 'a' * 64
        self.identity = {'version':'0.6.35', 'build_id':self.build}
        window = dict(window_start_utc='2026-01-01T00:00:00Z', observing_seconds=600, identity=self.identity)
        self.put('manifest', dict(format=3, minimum_updater=3, scope='application-only',
                                  platform='linux-alpine-armhf', **self.identity))
        self.put('owner', dict.fromkeys(('candidate_verified', 'whole_session', 'simulator_conditions_matched',
            'controller_list_unchanged', 'connected_devices_unchanged', 'singularity_session_unchanged',
            'discovery_ios', 'discovery_android', 'hotspot_outage_replay'), True) | window | {'delivery':'already-running'})
        for name in ('idle', 'session'):
            self.put(name, dict(kind='process', demo=False, build_id=self.build, bridge_version=self.identity['version'],
                                samples=[dict(seconds=i*5, sequence=i+1, age=0, rss=2**22, cpu=10) for i in range(121)]))
        for name, replies in (('relay-off', 1000), ('relay-on', 980)):
            self.put(name, dict(measurement_source='owner-simulator', simulator_only=True, demo=False, build_id=self.build, bridge_version=self.identity['version'],
                    path='home', transport='udp', flows=1, duration_seconds=125, rate_per_flow=10, payload_bytes=256,
                    elapsed_seconds=125, results=[dict(sent=1000, replies=replies, failures=1000-replies)]))
        self.put('telemetry', dict(**window, basis='counted', whole_session=True, expected=10000, missing=99))
        for role in ('home', 'hotspot'):
            self.put('native-'+role, dict(role=role, passed=True, identity_before=self.identity, identity_after=self.identity, timings_ms=dict.fromkeys(summary.TIMINGS, 90)))
        self.put('replay', dict(role='hotspot', passed=True, identity_before=self.identity, identity_after=self.identity, timings_ms={'replay':90000}))

    def put(self, name, value):
        (self.folder/(name+'.json')).write_text(json.dumps(value))

    def get(self, name):
        return summary.read(self.folder/(name+'.json'))

    def report(self):
        return summary.summarize(self.folder, self.folder/'manifest.json')

    def test_thresholds_and_demonstration_boundary(self):
        self.assertEqual(self.report()['status'], 'pass')  # exactly two percentage points is allowed
        for field, value in (('rss', 2**22 + 12 * 2**20), ('cpu', 60)):
            session = self.get('session')
            for row in session['samples']: row.update(rss=2**22, cpu=10); row[field] = value
            self.put('session', session)
            self.assertEqual(self.report()['checks']['resources']['status'], 'fail')
        self.put('telemetry', dict(self.get('telemetry'), missing=100))
        self.assertEqual(self.report()['checks']['telemetry_gaps']['status'], 'fail')
        self.put('relay-on', dict(self.get('relay-on'), results=[dict(sent=1000, replies=979, failures=21)]))
        self.assertEqual(self.report()['checks']['relay_loss']['status'], 'fail')
        self.put('relay-on', dict(self.get('relay-on'), measurement_source='loopback-demo'))
        self.assertEqual(self.report()['checks']['relay_loss']['status'], 'incomplete')

    def test_owner_missing_clock_reset_and_approximations_stay_open(self):
        self.put('telemetry', dict(self.get('telemetry'), basis='estimated'))
        self.put('owner', dict(self.get('owner'), discovery_ios=None))
        session = self.get('session')
        session['samples'][5]['sequence'] = 1
        self.put('session', session)
        self.assertEqual(self.report()['status'], 'incomplete')
        self.put('native-home', dict(self.get('native-home'), passed=False))
        self.assertEqual(self.report()['status'], 'fail')

    def test_native_replay_and_manual_counts_must_belong_to_this_candidate_and_session(self):
        for name in ('native-home', 'native-hotspot', 'replay'):
            self.put(name, dict(self.get(name), identity_after=dict(self.identity, build_id='b'*64)))
        self.put('telemetry', dict(self.get('telemetry'), window_start_utc='2026-02-01T00:00:00Z'))
        checks = self.report()['checks']
        for name in ('native_home', 'native_hotspot', 'outage_replay', 'telemetry_gaps'):
            self.assertEqual(checks[name]['status'], 'incomplete')
        self.put('telemetry', dict(self.get('telemetry'), window_start_utc='2026-01-01T00:00:00Z',
                                   identity=dict(self.identity, build_id='c'*64)))
        self.assertEqual(self.report()['checks']['telemetry_gaps']['status'], 'incomplete')

    def test_metrics_only_preserves_null_and_excludes_network_metadata(self):
        from unittest.mock import Mock
        api = Mock()
        api.request.side_effect = [{'demo':True, 'current_build_id':None, 'current_version':'0'},
            {'sample_sequence':1,'sample_age_seconds':0,'process':{'rss_bytes':None,'cpu_percent':{'5s':None}},
             'relays':[{'host':'sensitive-address'}]}]
        record = bench.metrics_window(api, 0)
        self.assertIsNone(record['samples'][0]['rss'])
        self.assertNotIn('sensitive-address', json.dumps(record))
        self.assertEqual([call.args[0] for call in api.request.call_args_list],
                         ['/system/updates', '/system/metrics'])
