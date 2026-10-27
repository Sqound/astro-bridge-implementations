"""Release trust boundaries and rollback, including interrupted transactions."""
import copy
import hashlib
import io
import importlib.util
import os
import subprocess
import sys
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from wifi_bridge import __version__
from scripts import ota
from wifi_bridge.updates import Updates
from wifi_bridge.config import Invalid

SAFE = {"mode":"setup","management_ready":True,"telescopes_connected":False,"update_safe":True}


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.SYMTYPE
                info.linkname = '/etc/passwd'
                tar.addfile(info)
            else:
                data = data.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def payload():
    data = archive({'wifi_bridge/__init__.py': '__version__ = "1.2.3"',
                    'wifi_bridge/__main__.py': '', 'wifi_bridge/static/index.html': 'new'})
    return data, {'version': '1.2.3', 'format': 1, 'platform': 'linux-raspios-armhf',
                  'sha256': hashlib.sha256(data).hexdigest()}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        deployment = patch.dict(os.environ, {"ASTRO_BRIDGE_PLATFORM":"raspberry"})
        deployment.start(); self.addCleanup(deployment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / 'state/updates'
        self.app = self.root / 'opt/wifi_bridge'
        self.app.mkdir(parents=True)
        (self.app / '__init__.py').write_text('__version__ = "1.0.0"')
        self.state.mkdir(parents=True, mode=0o700)
        (self.state.parent / 'config.json').write_text('{"retained":true}')
        self.patch = patch.multiple(ota, STATE=self.state, APP=self.app)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)

    def test_valid_archive_and_reject_tamper_platform_traversal_symlink(self):
        data, manifest = payload()
        ota.unpack(data, manifest, self.root / 'valid')
        for change in ({'sha256': '0' * 64}, {'platform': 'other'}, {'version': '2.0.0'}):
            with self.assertRaises(ValueError):
                ota.unpack(data, dict(manifest, **change), self.root / 'bad')
        for name, content in [('../escape', 'bad'), ('wifi_bridge/link', None), ('/etc/file', 'bad')]:
            unsafe = archive({name: content})
            with self.assertRaises(ValueError):
                ota.unpack(unsafe, dict(manifest, sha256=hashlib.sha256(unsafe).hexdigest()), self.root / 'bad')
        self.assertFalse((self.root / 'escape').exists())

    def test_only_pinned_repository_stable_complete_release(self):
        base = {'id': 7, 'tag_name': 'v1.2.3', 'assets': [
            {'name': name, 'browser_download_url': f'https://github.com/{ota.REPOSITORY}/releases/download/v1.2.3/{name}'}
            for name in ['ota-manifest.json', 'astro-bridge-1.2.3-ota.tar.gz']]}
        with patch.object(ota, 'fetch', return_value=json.dumps(base).encode()):
            self.assertTrue(ota.release('1.0.0')['available'])
            self.assertFalse(ota.release('2.0.0')['available'])
        for change in ({'prerelease': True}, {'tag_name': 'v1.2.3/../../x'}, {'assets': []}):
            with patch.object(ota, 'fetch', return_value=json.dumps(dict(base, **change)).encode()):
                with self.assertRaises(ValueError):
                    ota.release('1.0.0')
        evil = copy.deepcopy(base)
        evil['assets'][0]['browser_download_url'] = 'https://example.com/ota-manifest.json'
        with patch.object(ota, 'fetch', return_value=json.dumps(evil).encode()):
            with self.assertRaises(ValueError):
                ota.release('1.0.0')

    def run_install(self, healthy=True):
        data, manifest = payload()
        candidate = {'version': '1.2.3', 'manifest_url': 'manifest', 'archive_url': 'archive'}
        checks = [SAFE] + [{'version': '1.2.3', 'mode': 'waiting_telescope', 'management_ready': True}] * 3
        clock = [0, 0, 1, 2] if healthy else [0, 999]
        with patch.object(ota, 'fetch', side_effect=[json.dumps(manifest).encode(), data]), \
                patch.object(ota, 'service') as services, patch.object(ota.subprocess, 'run'), \
                patch.object(ota, 'health', side_effect=checks), \
                patch.object(ota.time, 'sleep'), patch.object(ota.time, 'monotonic', side_effect=clock):
            if healthy:
                ota.install(candidate)
            else:
                with self.assertRaises(ValueError):
                    ota.install(candidate)
            return services

    def test_install_preserves_config_and_commits_only_after_network_health(self):
        self.run_install()
        self.assertIn('1.2.3', (self.app / '__init__.py').read_text())
        self.assertEqual(json.loads((self.state / 'transaction.json').read_text())['phase'], 'complete')
        self.assertEqual((self.state.parent / 'config.json').read_text(), '{"retained":true}')
        self.assertIn('1.0.0', (self.app.parent / 'wifi_bridge.previous/__init__.py').read_text())

    def test_failed_health_restores_old_code_and_config(self):
        services = self.run_install(False)
        self.assertIn('1.0.0', (self.app / '__init__.py').read_text())
        self.assertEqual(json.loads((self.state / 'transaction.json').read_text())['phase'], 'rolled_back')
        self.assertEqual(services.call_args_list[-1].args, ('start',))

    def test_boot_recovers_interrupted_directory_switch(self):
        self.app.replace(self.app.parent / 'wifi_bridge.previous')
        ota.atomic(self.state / 'transaction.json', {'phase': 'applying'})
        self.assertTrue(ota.rollback())
        self.assertTrue((self.app / '__init__.py').exists())
        self.assertFalse(ota.rollback())

    def test_canonical_paths_flat_assets_and_minimum_updater(self):
        for name in ['wifi_bridge//bad.py','wifi_bridge/./bad.py','wifi_bridge/static/nested/file.js']:
            data=archive({'wifi_bridge/__init__.py':'__version__="1.2.3"','wifi_bridge/__main__.py':'',
                          'wifi_bridge/static/index.html':'ok',name:'bad'})
            with self.assertRaises(ValueError): ota.unpack(data,dict(payload()[1],sha256=hashlib.sha256(data).hexdigest()),self.root/'bad')
        data,manifest=payload()
        with patch.object(ota,'PLATFORM','linux-alpine-armhf'),self.assertRaises(ValueError):
            ota.unpack(data,dict(manifest,platform='linux-alpine-armhf'),self.root/'bad')
        with self.assertRaises(ValueError): ota.unpack(data,dict(manifest,minimum_updater=3),self.root/'bad')
        with self.assertRaises(ValueError): ota.unpack(data,dict(manifest,format=True),self.root/'bad')

    def test_release_selects_explicit_alpine_assets_with_raspi_coexistence(self):
        assets=[]
        for platform in ['linux-raspios-armhf','linux-alpine-armhf']:
            for name in ota.asset_names(platform,'1.2.3'):
                assets.append({'name':name,'browser_download_url':f'https://github.com/{ota.REPOSITORY}/releases/download/v1.2.3/{name}'})
        with patch.object(ota,'PLATFORM','linux-alpine-armhf'),patch.object(ota,'fetch',return_value=json.dumps({'id':3,'tag_name':'v1.2.3','assets':assets}).encode()):
            candidate=ota.release('1.0.0')
            self.assertIn('linux-alpine-armhf',candidate['archive_url'])
            self.assertIn('linux-alpine-armhf',candidate['manifest_url'])

    def test_reproducible_packages_both_platforms_and_loaded_build_identity(self):
        source=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('package_release',source/'tools/package-release.py')
        packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)
        output=self.root/'dist';packager.package(source,output)
        first={p.name:p.read_bytes() for p in output.iterdir()};packager.package(source,output)
        self.assertEqual(first,{p.name:p.read_bytes() for p in output.iterdir()})
        for platform in ['linux-raspios-armhf','linux-alpine-armhf']:
            name,asset=ota.asset_names(platform,__version__);manifest=json.loads((output/name).read_text())
            destination=self.root/platform;ota.unpack((output/asset).read_bytes(),manifest,destination,platform=platform)
            self.assertTrue((destination/'wifi_bridge/static/events.js').is_file())
            self.assertTrue((destination/'wifi_bridge/static/i18n.js').is_file())
            namespace={};exec((destination/'wifi_bridge/build.py').read_text(),namespace)
            loaded=namespace['BUILD_ID'];self.assertEqual(loaded,manifest['build_id'])
            # This value is imported at process start, not reread from mutable disk.
            (destination/'wifi_bridge/build.py').write_text('BUILD_ID="changed"')
            self.assertEqual(loaded,manifest['build_id'])
            self.assertEqual(manifest['minimum_updater'],2 if 'alpine' in platform else 1)
            if 'raspios' in platform:
                with tarfile.open(output/asset) as tar:
                    for member in tar:
                        parts=Path(member.name).parts
                        self.assertTrue(member.isfile())
                        self.assertTrue(len(parts)==2 and member.name.endswith('.py') or len(parts)==3 and parts[1]=='static')

    def test_interrupted_phases_and_each_directory_rename_are_recoverable(self):
        for phase,switched in [('prepared',0),('applying',0),('applying',1),('applying',2),('verifying',2)]:
            with self.subTest(phase=phase,switched=switched):
                previous=ota.tree_identity(self.app)
                journal={'phase':phase,'previous_build':previous,'snapshot_ready':True,'config_existed':True}
                ota.atomic_bytes(self.state/'config-backup.json',b'{"retained":true}')
                if switched:
                    self.app.replace(self.app.parent/'wifi_bridge.previous')
                if switched==2:
                    self.app.mkdir();(self.app/'__init__.py').write_text('__version__="broken"')
                (self.state.parent/'config.json').write_text('modified-by-new-app')
                ota.atomic(self.state/'transaction.json',journal)
                with patch.object(ota,'service') as service:
                    self.assertTrue(ota.rollback());service.assert_not_called()
                self.assertEqual(ota.tree_identity(self.app),previous)
                self.assertEqual((self.state.parent/'config.json').read_text(),'{"retained":true}')
                self.assertFalse(ota.rollback())

    def test_rollback_remembers_absent_config(self):
        ota.atomic(self.state/'transaction.json',{'phase':'verifying','snapshot_ready':True,'config_existed':False,'previous_build':ota.tree_identity(self.app)})
        ota.rollback();self.assertFalse((self.state.parent/'config.json').exists())

    def test_stop_failure_still_recovers_and_keeps_rolled_back_state(self):
        data,manifest=payload()
        with patch.object(ota,'service',side_effect=RuntimeError('service unavailable')),patch.object(ota.subprocess,'run'),patch.object(ota,'health',return_value=SAFE),self.assertRaises(ota.UpdateRolledBack):
            ota.install({'version':'1.2.3'},(manifest,data))
        status=json.loads((self.state/'status.json').read_text())
        self.assertEqual(status['state'],'rolled_back');self.assertFalse(status['rollback_service_ready'])
        self.assertIn('1.0.0',(self.app/'__init__.py').read_text())

    def test_start_failure_after_switch_restores_files_and_reports_restart_failure(self):
        data,manifest=payload()
        with patch.object(ota,'service',side_effect=[None,RuntimeError('start failed'),None,RuntimeError('rollback restart failed')]),patch.object(ota.subprocess,'run'),patch.object(ota,'health',return_value=SAFE),self.assertRaises(ota.UpdateRolledBack):
            ota.install({'version':'1.2.3'},(manifest,data))
        self.assertIn('1.0.0',(self.app/'__init__.py').read_text())
        status=json.loads((self.state/'status.json').read_text())
        self.assertEqual(status['state'],'rolled_back');self.assertFalse(status['rollback_service_ready'])

    def test_same_version_and_wrong_loaded_build_cannot_pass(self):
        data,manifest=payload()
        with self.assertRaises(ValueError): ota.install({'version':'1.0.0'},(dict(manifest,version='1.0.0'),data))
        files={'wifi_bridge/__init__.py':'__version__="1.2.3"','wifi_bridge/__main__.py':'','wifi_bridge/static/index.html':'new'}
        identity=ota.source_identity({k:v.encode() for k,v in files.items()});files['wifi_bridge/build.py']=f'BUILD_ID = "{identity}"\n'
        data=archive(files);manifest.update(sha256=hashlib.sha256(data).hexdigest(),build_id=identity)
        with patch.object(ota,'service'),patch.object(ota.subprocess,'run'),patch.object(ota,'health',side_effect=[SAFE,{'version':'1.2.3','build_id':'wrong','mode':'setup','management_ready':True}]),patch.object(ota.time,'monotonic',side_effect=[0,0,999]),patch.object(ota.time,'sleep'),self.assertRaises(ota.UpdateRolledBack):
            ota.install({'version':'1.2.3'},(manifest,data))

    def test_living_lock_skips_prestart_without_deadlock_and_stale_file_does_not_block(self):
        with ota.worker_lock() as acquired:
            self.assertTrue(acquired)
            with patch.object(sys,'argv',['ota','recover']),patch.object(ota,'require_root'),patch.object(ota,'rollback') as rollback:
                ota.main();rollback.assert_not_called()
        (self.state/'lock').write_text('dead-worker-pid-is-not-a-lock')
        with ota.worker_lock() as acquired:self.assertTrue(acquired)

    def test_automatic_gate_requires_explicit_management_and_idle_health(self):
        for value in [{},{'management_ready':True},{'mode':'setup','telescopes_connected':False},
                      {'mode':'setup','management_ready':True,'telescopes_connected':True},
                      {'mode':'starting','management_ready':True,'telescopes_connected':False},
                      {'mode':'unknown','management_ready':True,'telescopes_connected':False}]:
            self.assertFalse(ota.idle_health(value))
        self.assertTrue(ota.idle_health(SAFE))
        self.assertFalse(ota.idle_health(dict(SAFE, update_safe=False)))
        self.assertFalse(ota.idle_health({k:v for k,v in SAFE.items() if k!='update_safe'}))
        ota.atomic(self.state/'preferences.json',{'automatic':True})
        candidate={'release_id':1,'version':'1.2.3','available':True}
        with patch.object(sys,'argv',['ota','check']),patch.object(ota,'require_root'),patch.object(ota,'release',return_value=candidate),patch.object(ota,'health',return_value={}),patch.object(ota,'install') as install:
            ota.main();install.assert_not_called()
        self.assertEqual(json.loads((self.state/'status.json').read_text())['state'],'deferred')

    def test_stream_budget_rejects_oversized_headers_before_decompression(self):
        # PAX metadata is parsed internally before a normal member is yielded.
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            entry=tarfile.TarInfo('wifi_bridge/bad.py')
            entry.pax_headers={'comment':'x'*(33*1024*1024)}
            archive.addfile(entry,io.BytesIO(b''))
        data=output.getvalue()
        with self.assertRaisesRegex(ValueError,'Decompressed'):
            ota.unpack(data,dict(payload()[1],sha256=hashlib.sha256(data).hexdigest()),self.root/'bad')

    def test_fifo_import_does_not_block(self):
        folder=self.state/'import';folder.mkdir(mode=0o700)
        os.mkfifo(folder/'archive.tar.gz',0o600)
        with self.assertRaises(ValueError):ota.local_file(folder/'archive.tar.gz',1024)

    def test_owner_local_import_approved_digest_and_detached_execution(self):
        folder=self.state/'import';folder.mkdir(mode=0o700)
        data,manifest=payload()
        for name,value in [('archive.tar.gz',data),('manifest.json',json.dumps(manifest).encode())]:
            ota.atomic_bytes(folder/name,value)
        candidate,local=ota.local_candidate(manifest['sha256'])
        self.assertEqual(candidate['source'],'owner-local');self.assertEqual(local[1],data)
        with self.assertRaises(ValueError):ota.local_candidate('0'*64)
        (folder/'archive.tar.gz').chmod(0o644)
        with self.assertRaises(ValueError):ota.local_candidate(manifest['sha256'])
        with patch.object(ota.subprocess,'Popen') as spawn:
            ota.queue_local(manifest['sha256'])
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertNotIn('shell',spawn.call_args.kwargs)
            self.assertIn('import-local',spawn.call_args.args[0])
        self.assertEqual((self.state/'import-local.log').stat().st_mode&0o777,0o600)

    def test_valid_json_wrong_shapes_fail_closed_for_transaction_but_other_reads_recover(self):
        facade=Updates(self.state.parent)
        for value in ['null','[]','{}','{"phase":[]}','{"phase":"unknown"}','broken']:
            (self.state/'transaction.json').write_text(value)
            self.assertTrue(facade.busy());self.assertTrue(facade.recovery_required())
            self.assertEqual(facade.status()['state'],'recovery_required')
            with self.assertRaises(Invalid):facade.action({'action':'preferences','automatic':True})
            with self.assertRaises(ota.RecoveryRequired):ota.rollback()
        (self.state/'transaction.json').unlink()
        for name in ['status.json','preferences.json']:(self.state/name).write_text('null')
        self.assertFalse(facade.busy());self.assertEqual(facade.status()['state'],'unchecked')
        self.assertFalse(facade.status()['automatic'])

    def test_preferences_and_demo_never_start_updates(self):
        facade = Updates(self.root, demo=True)
        with self.assertRaises(Invalid):
            facade.action({'action': 'check'})
        facade = Updates(self.root)
        facade.action({'action': 'preferences', 'automatic': True})
        self.assertTrue(facade.status()['automatic'])
        with self.assertRaises(Invalid):
            facade.action({'action': 'preferences', 'automatic': 'true'})
        with self.assertRaises(Invalid):
            facade.action({'action': 'install', 'release_id': 99})


if __name__ == '__main__':
    unittest.main()
