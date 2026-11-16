"""Offline checks for the bundled graphic and supervisor; no hardware involved."""
import subprocess
import time
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLASH = ROOT / 'image/splash'

class SplashTest(unittest.TestCase):
    def test_asset_is_bundled_and_has_exact_label(self):
        svg = (SPLASH/'boot-splash.svg').read_text()
        self.assertIn('Astro Bridge is booting', svg)
        brand = (ROOT/'tools/brand/astro-bridge.svg').read_text()
        self.assertIn(brand[brand.index('>')+1:brand.rindex('</svg>')], svg)
        data = (SPLASH/'boot-splash.ppm').read_bytes()
        self.assertTrue(data.startswith(b'P6\n480 270\n255\n'))
        self.assertEqual(len(data), len(b'P6\n480 270\n255\n') + 480*270*3)
        self.assertIn(bytes.fromhex('d7a24e'), data)

    def test_worker_waits_off_boot_path_and_respects_diagnostic_vt(self):
        for diagnostic in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in ('sys/class/tty/tty0', 'sys/class/drm/card0-HDMI-A-1', 'dev', 'run'):
                    (root/name).mkdir(parents=True, exist_ok=True)
                (root/'run/astro-display-claim').mkdir()
                (root/'sys/class/tty/tty0/active').write_text('tty2' if diagnostic else 'tty1')
                (root/'sys/class/drm/card0-HDMI-A-1/status').write_text('connected')
                (root/'dev/tty7').touch()
                fake = root/'supervisor'
                fake.write_text('#!/bin/sh\necho launched > "'+str(root/'launched')+'"\n')
                fake.chmod(0o755)
                source = (ROOT/'image/pi5/astro-display-splash-worker').read_text()
                for path in ('/sys/class/tty/tty0/active', '/sys/class/drm/card*-HDMI-A-*/status', '/dev/fb0', '/dev/tty7', '/run/astro-display-claim', '/run/astro-display-owned'):
                    source = source.replace(path, str(root)+path)
                source = source.replace('/usr/local/sbin/astro-graphic-splash-supervisor', str(fake)).replace('[ -c ', '[ -f ')
                worker = root/'worker'; worker.write_text(source)
                proc = subprocess.Popen(['sh', str(worker)])
                if not diagnostic:
                    time.sleep(0.1)
                    self.assertIsNone(proc.poll())
                    (root/'dev/fb0').touch()
                self.assertEqual(proc.wait(timeout=4), 0)
                self.assertEqual((root/'launched').exists(), not diagnostic)
                self.assertFalse((root/'run/astro-display-claim').exists())

    def test_supervisor_recovers_after_renderer_failure(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d/'log'
            fake = d/'renderer'
            fake.write_text('#!/bin/sh\nif [ "$1" = --recover ]; then echo recover >> "'+str(log)+'"; exit 0; fi\nexit 1\n')
            fake.chmod(0o755)
            wrapper = d/'supervisor'
            wrapper.write_text((SPLASH/'graphic-splash-supervisor').read_text().replace('/usr/local/sbin/astro-graphic-splash',str(fake)))
            result = subprocess.run(['sh',str(wrapper),'--handoff'])
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(log.read_text(),'recover\n')

if __name__ == '__main__': unittest.main()
