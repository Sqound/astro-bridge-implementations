"""Exercise actual artifact boundaries using synthetic in-memory secrets."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from helpers.maker_keys import pem
from scripts import ota
from wifi_bridge.privacy import public_artifact

ROOT=Path(__file__).resolve().parents[1]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class ExclusionTests(unittest.TestCase):


    def test_ota_rejects_secret_paths_and_pem_before_unpack(self):
        for name,data in (('wifi_bridge/static/maker-keys.bin',b'opaque'),('wifi_bridge/static/maker-keys.kek',b'opaque'),('wifi_bridge/fixture.py',pem())):
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                info=tarfile.TarInfo(name);info.size=len(data);archive.addfile(info,io.BytesIO(data))
            content=stream.getvalue()
            manifest={'format':2,'platform':'linux-alpine-armhf','minimum_updater':2,'scope':'application-only','version':'1.0.0','sha256':hashlib.sha256(content).hexdigest()}
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):ota.unpack(content,manifest,Path(folder),'linux-alpine-armhf')
                self.assertFalse(list(Path(folder).iterdir()))

    def test_ota_packager_refuses_secret_files_in_source(self):
        packager=module('maker_test_packager','tools/package-release.py')
        for name in ('maker-keys.kek','maker-keys.bin'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);app=root/'wifi_bridge';app.mkdir()
                (app/'__init__.py').write_text('__version__ = "1.0.0"\n')
                (app/name).write_bytes(b'opaque')
                with self.assertRaises(ValueError):packager.package(root)
                self.assertFalse(list((root/'dist').iterdir()))
