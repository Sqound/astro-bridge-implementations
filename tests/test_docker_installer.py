"""A Docker installation plan must be inert; verified extraction must stay confined."""
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def installer():
    spec=importlib.util.spec_from_file_location('docker_installer',ROOT/'tools/install-docker.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def archive(name,kind=None):
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w:gz') as tar:
        item=tarfile.TarInfo(name)
        if kind:item.type=kind;item.linkname='/outside'
        else:item.size=4
        tar.addfile(item,None if kind else io.BytesIO(b'demo'))
    return data.getvalue()


class DockerInstallerTests(unittest.TestCase):
    def test_plan_performs_no_commands_download_or_installation(self):
        tool=installer()
        with tempfile.TemporaryDirectory() as folder:
            prefix=Path(folder)/'new'
            with patch.object(sys,'argv',['installer','--prefix',str(prefix)]),patch.object(tool,'run') as command,patch.object(tool,'download') as fetch:
                tool.main()
            command.assert_not_called();fetch.assert_not_called();self.assertFalse(prefix.exists())

    def test_changed_source_is_rejected_before_dependency_installation(self):
        tool=installer()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.tar.gz';source.write_bytes(b'changed source')
            with patch.object(sys,'argv',['installer','--apply','--source',str(source),'--sha256','0'*64,'--prefix',str(root/'new')]),patch.object(tool,'install_engine') as dependencies:
                with self.assertRaises(SystemExit):tool.main()
            dependencies.assert_not_called();self.assertFalse((root/'new').exists())

    def test_extraction_rejects_parent_escape_symlink_and_other_root(self):
        tool=installer()
        for name,kind in [('astro_bridge_implementations-1.0.1/../escape',None),('astro_bridge_implementations-1.0.1/link',tarfile.SYMTYPE),('another-root/file',None)]:
            with tempfile.TemporaryDirectory() as folder:
                prefix=Path(folder)/'new'
                with self.assertRaises(ValueError):tool.unpack(archive(name,kind),prefix)
                self.assertFalse(prefix.exists())

    def test_verified_layout_is_extracted_without_existing_prefix_overwrite(self):
        tool=installer()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'new';data=archive('astro_bridge_implementations-1.0.1/README.md')
            tool.unpack(data,root);self.assertEqual((root/'README.md').read_text(),'demo')
            with self.assertRaises(FileExistsError):tool.unpack(data,root)
            self.assertEqual((root/'README.md').read_text(),'demo')

    def test_real_networking_is_refused_on_non_linux_before_changes(self):
        tool=installer()
        with patch.object(tool.platform,'system',return_value='Windows'),patch.object(sys,'argv',['installer','--mode','linux','--apply']),patch.object(tool,'install_engine') as dependencies:
            with self.assertRaises(SystemExit):tool.main()
        dependencies.assert_not_called()
