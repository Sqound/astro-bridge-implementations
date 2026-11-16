"""Summarise Zero W acceptance evidence. Exit 0 pass, 1 fail, 2 incomplete."""
import argparse
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import statistics
import sys


def read(path):
    with path.open('rb') as stream:
        data = stream.read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        raise ValueError
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError
    return value


def require(condition):
    if not condition:
        raise ValueError


def number(value):
    require(type(value) in (int, float) and 0 <= value <= 2**53 and math.isfinite(value))
    return value


def count(value):
    require(type(value) is int)
    return number(value)


def outcome(passed, **values):
    return dict(status='pass' if passed else 'fail', **values)


def process(record, identity, minimum):
    require(record['kind'] == 'process' and record['demo'] is False and record['build_id'] == identity['build_id']
            and record['bridge_version'] == identity['version'])
    rows = record['samples']
    require(isinstance(rows, list) and 2 <= len(rows) <= 10600)
    rss, cpu = [], []
    last_time, last_seq, last_fresh = 0, None, 0
    for row in rows:
        now, seq = number(row['seconds']), count(row['sequence'])
        require(last_time <= now <= last_time + 10 and number(row['age']) <= 6)
        if last_seq is None or seq != last_seq:
            require(last_seq is None or seq == last_seq + 1)
            require(now - last_fresh <= 11)
            rss.append(count(row['rss']))
            cpu.append(number(row['cpu']))
            last_fresh = now
        last_time, last_seq = now, seq
    require(last_time >= minimum and last_time - last_fresh <= 6 and len(rss) >= 2)
    return rss, cpu


def loss(run, identity):
    require(run['measurement_source'] == 'owner-simulator' and run['simulator_only'] is True
            and run['build_id'] == identity['build_id'] and run['demo'] is False
            and run['bridge_version'] == identity['version'])
    params = tuple(run[k] for k in ('path', 'transport', 'flows', 'duration_seconds', 'rate_per_flow', 'payload_bytes'))
    require(params == ('home', 'udp', 1, 125, 10, 256) and number(run['elapsed_seconds']) >= 125)
    rows = run['results']
    require(len(rows) == 1)
    sent, replies = count(rows[0]['sent']), count(rows[0]['replies'])
    require(0 < sent and replies <= sent and count(rows[0]['failures']) == sent - replies)
    return Fraction(100 * (sent - replies), sent)


TIMINGS = ('pair', 'list', 'connect_endpoint', 'lost', 'restored', 'restored_endpoint', 'release')


def summarize(folder, manifest):
    checks = {}
    def check(name, operation):
        try:
            checks[name] = operation()
        except (OSError, KeyError, TypeError, ValueError, IndexError, RecursionError, OverflowError):
            checks[name] = {'status': 'incomplete'}
    def data(name):
        return read(folder / (name + '.json'))
    def build():
        value = read(manifest)
        require(value['format'] == value['minimum_updater'] == 3 and value['scope'] == 'application-only'
                and value['platform'] == 'linux-alpine-armhf' and re.fullmatch('[0-9a-f]{64}', value['build_id']))
        require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value['version']))
        return {'version':value['version'], 'build_id':value['build_id']}
    def observed(name):
        owner = data('owner')
        require(owner['identity'] == build())
        value = owner[name]
        require(type(value) is bool)
        return outcome(value)
    def delivery():
        method = data('owner')['delivery']
        require(method in ('ota-installed', 'already-running'))
        return outcome(True, method=method, ota_install_exercised=method == 'ota-installed')
    def replay():
        row = data('replay')
        require(row['identity_before'] == row['identity_after'] == build())
        require(row['role'] == 'hotspot' and type(row['passed']) is bool)
        if not row['passed']: return outcome(False)
        return outcome(True, replay_ms=count(row['timings_ms']['replay']))
    def resources():
        idle, _ = process(data('idle'), build(), 600)
        duration = number(data('owner')['observing_seconds'])
        require(duration > 0)
        loaded, cpu = process(data('session'), build(), max(5, duration))
        delta = max(loaded) - statistics.median(idle)
        p95 = sorted(cpu)[math.ceil(.95 * len(cpu)) - 1]
        return outcome(delta < 12 * 1024**2 and p95 < 60, rss_delta_mib=delta / 1024**2,
                       cpu_p95_percent=p95, idle_samples=len(idle), session_samples=len(loaded))
    def relay():
        before, after = (loss(data(name), build()) for name in ('relay-off', 'relay-on'))
        return outcome(after-before <= 2, off_percent=float(before), on_percent=float(after),
                       increase_points=float(after-before))
    def telemetry():
        row = data('telemetry')
        owner = data('owner')
        require(row['identity'] == owner['identity'] == build())
        require(row['window_start_utc'] == owner['window_start_utc'] and
                re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', row['window_start_utc']))
        require(number(owner['observing_seconds']) > 0 and number(row['observing_seconds']) == owner['observing_seconds'])
        require(row['basis'] == 'counted' and row['whole_session'] is True)
        expected, missed = count(row['expected']), count(row['missing'])
        require(0 < expected and missed <= expected)
        return outcome(100*missed < expected, expected=expected, missing=missed,
                       gap_percent=100*missed/expected)
    def native(role):
        row = data('native-'+role)
        require(row['identity_before'] == row['identity_after'] == build())
        require(row['role'] == role and type(row['passed']) is bool)
        if not row['passed']: return outcome(False)
        timings = {name: count(row['timings_ms'][name]) for name in TIMINGS}
        return outcome(True, timings_ms=timings)
    check('delivery', delivery)
    check('outage_replay', replay)
    check('resources', resources)
    check('relay_loss', relay)
    check('telemetry_gaps', telemetry)
    for role in ('hotspot', 'home'):
        check('native_'+role, lambda: native(role))
    for name in ('candidate_verified', 'whole_session', 'simulator_conditions_matched',
                 'controller_list_unchanged', 'connected_devices_unchanged', 'singularity_session_unchanged',
                 'discovery_ios', 'discovery_android', 'hotspot_outage_replay'):
        check(name, lambda: observed(name))
    states = {row['status'] for row in checks.values()}
    return {'status': 'fail' if 'fail' in states else 'incomplete' if 'incomplete' in states else 'pass',
            'checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('alpine_manifest', type=Path)
    args = parser.parse_args()
    report = summarize(args.folder, args.alpine_manifest)
    print(json.dumps(report, indent=2, allow_nan=False))
    return {'pass': 0, 'fail': 1, 'incomplete': 2}[report['status']]


if __name__ == '__main__':
    sys.exit(main())
