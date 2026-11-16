#!/usr/bin/env python3
"""No-device SDK load probe. Vendor calls run only in disposable CI children."""
import argparse
import ctypes
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import time

STRATEGIES = ('direct', 'preload', 'loader')


def rss():
    try:
        fields = pathlib.Path('/proc/self/statm').read_text().split()
        return int(fields[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return None


def zwo_version(pointer):
    # The SDK returns a NUL-terminated char*. c_char_p would scan to NUL
    # before we could enforce the length bound. Stop at the first terminator.
    value = bytearray()
    if pointer:
        for index in range(129):
            byte = pointer[index]
            if byte == 0:
                # SDK headers document comma-separated decimal fields. Keep
                # their original spacing and leading zeroes in the evidence.
                if re.fullmatch(rb'[0-9]{1,8}(?:(?:, ?|\.)[0-9]{1,8}){1,7}', value):
                    return value.decode('ascii')
                break
            value.append(byte)
    raise ValueError('invalid_version')


def call_api(library, vendor):
    """Only these five entry points; no camera opens, IDs or image operations."""
    if vendor == 'zwo':
        version = library.ASIGetSDKVersion
        version.argtypes, version.restype = [], ctypes.POINTER(ctypes.c_ubyte)
        count = library.ASIGetNumOfConnectedCameras
        count.argtypes, count.restype = [], ctypes.c_int
        return {'version': zwo_version(version()), 'cameras': count()}
    init, scan, release = (getattr(library, name) for name in
                           ('InitQHYCCDResource', 'ScanQHYCCD', 'ReleaseQHYCCDResource'))
    for method in (init, scan, release):
        method.argtypes, method.restype = [], ctypes.c_uint32
    initialized = init()
    if initialized != 0:
        return {'init_result': initialized, 'cameras': None}
    try:
        result = {'init_result': 0, 'cameras': scan()}
    finally:
        released = release()
    result['release_result'] = released
    return result


def load_error(exc):
    # No raw loader/vendor output: report only a constrained ELF symbol name.
    match = re.search(r'(?:symbol not found|undefined symbol):?\s*([A-Za-z_][A-Za-z0-9_@.]{0,255})', str(exc))
    if not match:
        match = re.search(r'([A-Za-z_][A-Za-z0-9_@.]{0,255}): symbol not found', str(exc))
    return {'error': 'load_failed', 'missing_symbol': match.group(1) if match else None}


def child(library, vendor):
    if (sys.platform != 'linux' or os.environ.get('SDK_PROBE_CONTAINER') != '1'
            or pathlib.Path('/dev/bus/usb').exists()):
        return {'error': 'disposable_no_usb_container_required'}
    before, start = rss(), time.monotonic()
    try:
        lib = ctypes.CDLL(str(pathlib.Path(library).resolve()), mode=os.RTLD_NOW | os.RTLD_LOCAL)
        loaded = time.monotonic()
        after = rss()
        result = call_api(lib, vendor)
        result.update(load_ms=round((loaded - start) * 1000, 3),
                      rss_delta_bytes=None if before is None or after is None else after - before)
        result['ok'] = result.get('cameras') == 0 and result.get('release_result', 0) == 0
        return result
    except OSError as exc:
        return load_error(exc)
    except AttributeError:
        return {'error': 'missing_sdk_entry_point'}
    except ValueError as exc:
        return {'error': 'invalid_version' if exc.args == ('invalid_version',) else 'invalid_sdk_api'}


def command(library, vendor, strategy, machine):
    argv = [sys.executable, str(pathlib.Path(__file__).resolve()), vendor, str(library), '--child']
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    env.pop('LD_LIBRARY_PATH', None)
    if strategy == 'preload':
        env['LD_PRELOAD'] = '/lib/libgcompat.so.0'
    elif strategy == 'loader':
        loader = '/lib/ld-linux-aarch64.so.1' if machine == 'aarch64' else '/lib/ld-linux-armhf.so.3'
        argv.insert(0, loader)
    return argv, env


def probe(library, vendor, run=subprocess.run):
    results = []
    for strategy in STRATEGIES:
        argv, env = command(library, vendor, strategy, platform.machine())
        try:
            proc = run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=30, check=False)
            if proc.returncode:
                result = {'error': 'child_failed', 'returncode': proc.returncode}
            elif len(proc.stdout) > 4096:
                result = {'error': 'invalid_child_result'}
            else:
                result = json.loads(proc.stdout)
                if not isinstance(result, dict):
                    raise ValueError('invalid_child_result')
        except subprocess.TimeoutExpired:
            result = {'error': 'timeout'}
        except OSError:
            result = {'error': 'strategy_unavailable'}
        except (ValueError, UnicodeDecodeError):
            result = {'error': 'invalid_child_result'}
        results.append(dict(strategy=strategy, **result))
    return {'vendor': vendor, 'machine': platform.machine(), 'qemu_cpu': os.environ.get('QEMU_CPU'),
            'measurements': 'indicative_under_emulation', 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vendor', choices=('zwo', 'qhy'))
    parser.add_argument('library')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        # Suppress even C-level stdout/stderr before dlopen constructors execute.
        output = os.dup(1)
        with open(os.devnull, 'wb') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        result = child(args.library, args.vendor)
        os.write(output, (json.dumps(result) + '\n').encode())
        os.close(output)
    else:
        print(json.dumps(probe(args.library, args.vendor), sort_keys=True))


if __name__ == '__main__':
    main()
