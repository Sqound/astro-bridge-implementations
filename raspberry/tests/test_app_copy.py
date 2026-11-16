"""All existing source installers must include package code, excluding caches."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('copy_app', ROOT / 'scripts/copy-app.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AppCopyTests(unittest.TestCase):
    def test_camera_tables_are_installed_validated_and_unknown_files_excluded(self):
        from wifi_bridge.cameras.registry import load_registry
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            tables = source / 'cameras' / 'tables'
            tables.mkdir(parents=True)
            table = json.loads((ROOT / 'cameras/uvc.json').read_text())
            (tables / 'uvc.json').write_text(json.dumps(table))
            (tables / 'notes.txt').write_text('excluded')
            (tables / '.hidden.json').write_text('excluded')
            module.copy_app(source, target)
            self.assertEqual(load_registry(target / 'cameras/tables').descriptors(), (table,))
            self.assertEqual([p.name for p in (target / 'cameras/tables').iterdir()], ['uvc.json'])
            (tables / 'uvc.json').write_text(json.dumps(dict(table, unknown='rejected')))
            with self.assertRaises(ValueError):
                module.copy_app(source, target)
            (tables / 'uvc.json').write_text(json.dumps(table))
            (target / 'cameras/tables/uvc.json').unlink()
            (target / 'cameras/tables/uvc.json').symlink_to(tables / 'uvc.json')
            with self.assertRaises(ValueError):
                module.copy_app(source, target)

    def test_subpackages_assets_private_modes_and_cache_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            files = {'__init__.py': '', 'nested/__init__.py': '', 'nested/store.py': 'VALUE = 1',
                     'static/nested/test.json': '{}', '__pycache__/cache.pyc': 'cache',
                     'nested/private.json': '{}', '.hidden.py': 'hidden'}
            for name, content in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            previous = os.umask(0o077)
            try: module.copy_app(source, target)
            finally: os.umask(previous)
            expected = {'__init__.py', 'nested/__init__.py', 'nested/store.py', 'static/nested/test.json'}
            self.assertEqual({str(path.relative_to(target)) for path in target.rglob('*') if path.is_file()}, expected)
            for path in target.rglob('*'):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755 if path.is_dir() else 0o644)
            with self.assertRaises(ValueError): module.copy_app(source, source / 'child')
            (source / 'linked.py').symlink_to(source / '__init__.py')
            with self.assertRaises(ValueError): module.copy_app(source, target)

    def test_zwo_source_install_has_reviewed_driver_and_table_only(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'wifi_bridge'
            module.copy_app(ROOT / 'wifi_bridge', target)
            for rel in ('zwo_asi.py', 'zwo_process.py', 'zwo_platform.py', 'tables/zwo_asi.json'):
                self.assertEqual((target/'cameras'/rel).read_bytes(), (ROOT/'wifi_bridge/cameras'/rel).read_bytes())
            self.assertFalse(list(target.rglob('libASICamera2*')))
            self.assertFalse(list(target.rglob('install-zwo*')))

    def test_existing_installers_share_the_same_copy_policy(self):
        for name in ('scripts/install.sh', 'image/alpine/customize.sh', 'image/alpine/standard/genapkovl-astrobridge.sh'):
            with self.subTest(script=name):
                content = (ROOT / name).read_text()
                self.assertIn('scripts/copy-app.py', content)
                self.assertNotIn('wifi_bridge/*.py', content)
