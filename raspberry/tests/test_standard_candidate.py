"""Functional candidate inspection/stamping, without Docker/build/physical disks."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('candidate',ROOT/'image/alpine/standard/candidate.py')
candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)


def cpio(entries):
    value=bytearray()
    for name,content in list(entries.items())+[('TRAILER!!!',b'')]:
        fields=[0,0o100644,0,0,1,0,len(content),0,0,0,0,len(name.encode())+1,0]
        value.extend(b'070701'+''.join(f'{field:08x}' for field in fields).encode()+name.encode()+b'\0')
        value.extend(b'\0'*((-len(value))%4));value.extend(content);value.extend(b'\0'*((-len(value))%4))
    return gzip.compress(value,mtime=0)


class StandardCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.boot=self.root/'boot';self.boot.mkdir();self.package_root=self.root/'packages';self.package_root.mkdir()
        self.source_build=(ROOT/'wifi_bridge/build.py').read_bytes()
        subprocess.run(['sh',str(ROOT/'image/alpine/standard/genapkovl-astrobridge.sh'),'synthetic'],
            cwd=self.boot,env={'PATH':__import__('os').environ['PATH'],'ASTRO_BRIDGE_PROJECT':str(ROOT)},check=True,capture_output=True,timeout=15)
        self.world,self.identity=candidate.overlay(self.boot)
        for name in ('bootcode.bin','start.elf','fixup.dat','cmdline.txt','boot/vmlinuz-rpi','boot/modloop-rpi','bcm2835-rpi-zero-w.dtb'):
            p=self.boot/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(b'public synthetic payload')
        (self.boot/'config.txt').write_text('kernel=boot/vmlinuz-rpi\ninitramfs boot/initramfs-rpi\narm_64bit=0\ninclude usercfg.txt\n')
        (self.boot/'.alpine-release').write_text('synthetic-release')
        (self.boot/'boot/config-6.18-test-rpi').write_text('synthetic kernel config')
        self.key=self.root/'synthetic.rsa.pub';self.key.write_bytes(b'public key fixture')
        (self.boot/'boot/initramfs-rpi').write_bytes(cpio({'etc/apk/keys/'+self.key.name:self.key.read_bytes()}))
        for name in ('usr/bin/nmcli','usr/bin/chronyc','usr/bin/openssl','sbin/fsck.ext4','bin/busybox',
                     'lib/firmware/brcm/brcmfmac43430-sdio.bin','lib/firmware/rtw88/rtw8821c_fw.bin','etc/crontabs/root',
                     *('lib/firmware/brcm/brcmfmac43430-sdio.raspberrypi,model-zero-w.'+suffix for suffix in ('bin','txt','clm_blob'))):
            p=self.package_root/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(b'rfkill\0 /etc/periodic/daily')
        self.packages=[{'name':name,'version':'1-r0','license':'fixture','arch':'armhf'} for name in self.world]
        self.modules='modules/6.18-test-rpi/kernel/brcmfmac.ko.gz\nmodules/6.18-test-rpi/kernel/rtw88_8821cu.ko.gz\n'

    def inspect(self):return candidate.inspect_boot(self.boot,self.package_root,self.packages,self.modules,[self.key])

    def test_real_overlay_stamps_same_ota_identity_and_preserves_source(self):
        from wifi_bridge.ota import tree_identity
        self.assertEqual(self.identity,tree_identity(ROOT/'wifi_bridge'))
        self.assertEqual((ROOT/'wifi_bridge/build.py').read_bytes(),self.source_build)
        self.assertEqual(self.source_build, f'BUILD_ID = "{self.identity}"\n'.encode())
        value=self.inspect();self.assertEqual(value['app_build_id'],self.identity)
        self.assertEqual(value['kernel_release'],'6.18-test-rpi')
        self.assertEqual(value['trust_keys'][self.key.name],hashlib.sha256(self.key.read_bytes()).hexdigest())
        self.assertIn('bcm2835-rpi-zero-w.dtb',value['boot_files'])
        output=subprocess.check_output(['sh','-c','profile_rpi() { apks=alpine-base; }\n. "$1"\nprofile_astrobridge\nprintf "%s\\n" "$apks"','sh',str(ROOT/'image/alpine/standard/profile-astrobridge.sh')],text=True)
        self.assertLessEqual(set(self.world),set(output.split()))

    def test_actual_missing_dtb_config_module_firmware_key_and_closure_fail(self):
        dtb=self.boot/'bcm2835-rpi-zero-w.dtb';content=dtb.read_bytes();dtb.unlink()
        with self.assertRaisesRegex(ValueError,'Missing firmware boot'):self.inspect()
        dtb.write_bytes(content)
        original=(self.boot/'config.txt').read_text()
        (self.boot/'config.txt').write_text(original.replace('kernel=boot/','kernel='))
        with self.assertRaisesRegex(ValueError,'firmware configuration'):self.inspect()
        (self.boot/'config.txt').write_text(original)
        self.modules=self.modules.replace('6.18-test-rpi/kernel/brcmfmac','another-kernel/brcmfmac')
        with self.assertRaisesRegex(ValueError,'matching Wi-Fi'):self.inspect()
        self.modules=self.modules.replace('another-kernel','6.18-test-rpi/kernel')
        firmware=self.package_root/'lib/firmware/rtw88/rtw8821c_fw.bin';firmware.unlink()
        with self.assertRaisesRegex(ValueError,'actual package payload'):self.inspect()
        firmware.write_bytes(b'firmware');self.packages.pop()
        with self.assertRaisesRegex(ValueError,'closure'):self.inspect()
        self.packages=[{'name':name,'version':'1-r0','license':'fixture','arch':'armhf'} for name in self.world]
        self.key.write_bytes(b'changed public key')
        with self.assertRaisesRegex(ValueError,'actual initramfs'):self.inspect()

    def test_actual_zstd_firmware_requires_kernel_support_and_resolved_board_payloads(self):
        # A real zstd frame generated once from public synthetic bytes; no decoder
        # dependency is needed for the portable kernel-format/provenance checks.
        frame=bytes.fromhex('28b52ffd201ad1000073796e746865746963206669726d776172652066697874757265')
        firmware=self.package_root/'lib/firmware';cypress=firmware/'cypress';cypress.mkdir()
        generic=firmware/'brcm/brcmfmac43430-sdio.bin';generic.unlink()
        (cypress/'generic.bin.zst').write_bytes(frame);generic.with_suffix('.bin.zst').symlink_to('../cypress/generic.bin.zst')
        board=firmware/'brcm/brcmfmac43430-sdio.raspberrypi,model-zero-w.bin';board.unlink()
        (cypress/'rpiltd.bin.zst').write_bytes(frame);board.with_suffix('.bin.zst').symlink_to('../cypress/rpiltd.bin.zst')
        config=self.boot/'boot/config-6.18-test-rpi'
        with self.assertRaisesRegex(ValueError,'cannot load Zstandard'):self.inspect()
        config.write_text('CONFIG_FW_LOADER_COMPRESS=y\nCONFIG_FW_LOADER_COMPRESS_ZSTD=y\n')
        value=self.inspect()['firmware'];model=value['brcm/brcmfmac43430-sdio.raspberrypi,model-zero-w.bin']
        self.assertEqual(model['resolved'],'lib/firmware/cypress/rpiltd.bin.zst');self.assertEqual(model['compression'],'zstd')
        self.assertEqual(model['sha256'],hashlib.sha256(frame).hexdigest());self.assertNotEqual(model['resolved'],value['brcm/brcmfmac43430-sdio.bin']['resolved'])
        (cypress/'rpiltd.bin.zst').unlink()
        with self.assertRaisesRegex(ValueError,'actual package payload'):self.inspect()
        link=board.with_suffix('.bin.zst');link.unlink();link.symlink_to('../../../../../outside')
        with self.assertRaisesRegex(ValueError,'escapes root'):self.inspect()
        link.unlink();link.symlink_to('../cypress/rpiltd.bin.zst');(cypress/'rpiltd.bin.zst').write_bytes(b'not-zstd')
        with self.assertRaisesRegex(ValueError,'firmware frame'):self.inspect()

    def test_package_absolute_symlink_resolves_only_inside_target_root(self):
        p=self.package_root/'sbin/fsck.ext4';p.unlink();p.symlink_to('/sbin/e2fsck')
        self.assertFalse(candidate.root_file(self.package_root,'sbin/fsck.ext4').exists())
        with self.assertRaisesRegex(ValueError,'actual package payload'):self.inspect()
        p.with_name('e2fsck').write_bytes(b'fixture');self.inspect()
        p.unlink();p.symlink_to('fsck.ext4')
        with self.assertRaisesRegex(ValueError,'symlink loop'):self.inspect()


    def test_committed_snapshot_excludes_private_ignored_and_working_changes(self):
        repository=self.root/'repository';repository.mkdir()
        def git(*args):
            return subprocess.check_output(['git',*args],cwd=repository,stderr=subprocess.DEVNULL,text=True).strip()
        git('init','-q')
        content={'wifi_bridge/__init__.py':'__version__ = "0.6.1"\n',
                 'wifi_bridge/build.py':'BUILD_ID = None\n',
                 'image/alpine/standard/candidate.py':'# committed recipe\n',
                 'image/alpine/standard/inputs.json':(ROOT/'image/alpine/standard/inputs.json').read_text(),
                 '.gitignore':'private/\n','unrelated.txt':'not a build input\n'}
        for name,value in content.items():
            path=repository/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
        git('add','.')
        git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Synthetic fixture')
        (repository/'private').mkdir();(repository/'private/canary').write_text('synthetic private canary')
        (repository/'wifi_bridge/__init__.py').write_text('__version__ = "9.9.9"\n')
        snapshot=self.root/'snapshot';snapshot.mkdir();output=repository/'dist/candidate'
        with patch.object(candidate,'ROOT',repository), patch('shutil.disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
            candidate.prepare(output,snapshot)
            with self.assertRaises(ValueError):candidate.prepare(output,snapshot)
            with self.assertRaises(ValueError):candidate.prepare(repository/'elsewhere',snapshot)
            linked=repository/'dist/linked';linked.symlink_to(self.root/'foreign')
            with self.assertRaises(ValueError):candidate.prepare(linked,snapshot)
        self.assertEqual((snapshot/'wifi_bridge/__init__.py').read_text(),content['wifi_bridge/__init__.py'])
        self.assertFalse((snapshot/'.git').exists());self.assertFalse((snapshot/'private').exists());self.assertFalse((snapshot/'unrelated.txt').exists())
        metadata=(output/'source.json').read_text();value=json.loads(metadata)
        self.assertEqual(value['version'],'0.6.1');self.assertTrue(value['working_tree_dirty'])
        self.assertEqual(value['source_commit'],git('rev-parse','HEAD'))
        self.assertNotIn(str(repository),metadata);self.assertNotIn('fixture@example.invalid',metadata)

    def test_snapshot_stage_is_private_dist_child_not_unshared_tmpdir_or_symlink(self):
        repository=self.root/'stage-repository';repository.mkdir()
        with patch.object(candidate,'ROOT',repository):
            snapshot=candidate.create_snapshot()
        self.assertEqual(snapshot.parent,repository/'dist');self.assertEqual(snapshot.stat().st_mode&0o777,0o700)
        self.assertTrue(snapshot.name.startswith('.astro-standard-source-'))
        foreign=self.root/'foreign';foreign.mkdir();linked=self.root/'linked-repository';linked.mkdir();(linked/'dist').symlink_to(foreign,target_is_directory=True)
        with patch.object(candidate,'ROOT',linked):
            with self.assertRaisesRegex(ValueError,'real dist'):candidate.create_snapshot()
        self.assertEqual(list(foreign.iterdir()),[])
        regular=self.root/'file-repository';regular.mkdir();(regular/'dist').write_text('must not replace')
        with patch.object(candidate,'ROOT',regular):
            with self.assertRaisesRegex(ValueError,'real dist'):candidate.create_snapshot()
        self.assertEqual((regular/'dist').read_text(),'must not replace')

    def test_loop_guard_refuses_physical_foreign_offset_and_size(self):
        backing=self.root/'raw.img';backing.write_bytes(b'not a disk')
        item={'name':'/dev/loop31','back-file':str(backing),'offset':1048576,'sizelimit':536870912}
        def run(*args,**kwargs):return json.dumps({'loopdevices':[item]})
        candidate.guard_loop('/dev/loop31',backing,1048576,536870912,run=run)
        with self.assertRaises(ValueError):candidate.guard_loop('/dev/sda',backing,1048576,536870912,run=run)
        for field,bad in [('back-file',str(self.root/'foreign.img')),('offset',0),('sizelimit',0)]:
            previous=item[field];item[field]=bad
            with self.assertRaises(ValueError):candidate.guard_loop('/dev/loop31',backing,1048576,536870912,run=run)
            item[field]=previous

    def test_explicit_sh_invocation_stops_at_first_apk_or_copy_failure(self):
        tools=self.root/'fake-tools';tools.mkdir();trace=self.root/'trace'
        def tool(name,body):
            path=tools/name;path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o755)
        tool('id','printf "0\\n"')
        tool('df','printf "Filesystem blocks used available capacity mount\\nfake 10485760 0 10485760 0%% /\\n"')
        tool('apk','printf "apk\\n" >> "$TEST_TRACE"; exit 73')
        for name in ('git','install','sed','sha256sum','abuild-keygen','find','cp','python3','sh','truncate','parted','mountpoint','findmnt','umount','losetup','mkfs.vfat','mkfs.ext4','blkid','fsck.fat','e2fsck','mount','tar','unsquashfs','sync'):
            tool(name,'printf "later-command\\n" >> "$TEST_TRACE"; exit 95')
        work=self.root/'work';work.mkdir();out=self.root/'out';out.mkdir();(out/'source.json').write_text('{}')
        source=(ROOT/'image/alpine/standard/build-inside.sh').read_text()
        # The actual script body/interpreter, with only fixed container roots
        # relocated into this unprivileged temporary test root. All dangerous
        # later tools are stubs even if errexit regresses again.
        script=self.root/'inside.sh';script.write_text(source.replace('/project',str(ROOT)).replace('/work',str(work)).replace('/out',str(out)))
        environment={**os.environ,'PATH':str(tools)+os.pathsep+os.environ['PATH'],'TEST_TRACE':str(trace)}
        result=subprocess.run(['/bin/sh',str(script)],env=environment,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,73,result.stderr);self.assertEqual(trace.read_text().splitlines(),['apk'])
        self.assertFalse((out/'builder-packages.json').exists());self.assertFalse((work/'job/aports').exists())
        trace.unlink();tool('cp','printf "cp\\n" >> "$TEST_TRACE"; exit 71')
        tmp=self.root/'generator-tmp';tmp.mkdir()
        environment.update(ASTRO_BRIDGE_PROJECT=str(ROOT),TMPDIR=str(tmp))
        result=subprocess.run(['/bin/sh',str(ROOT/'image/alpine/standard/genapkovl-astrobridge.sh'),'synthetic-failfast'],cwd=out,env=environment,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,71,result.stderr);self.assertEqual(trace.read_text().splitlines(),['cp'])
        self.assertFalse((out/'synthetic-failfast.apkovl.tar.gz').exists());self.assertEqual(list(tmp.iterdir()),[])

    def test_fresh_data_root_is_private_only_on_exact_owned_mount(self):
        data=self.root/'data';data.mkdir(mode=0o755);data.chmod(0o755)
        backing=self.root/'candidate.img';backing.write_bytes(b'fixture')
        item={'name':'/dev/loop31','back-file':str(backing),'offset':537919488,'sizelimit':1072693248}
        mount_source='/dev/loop32'
        def run(command,**kwargs):
            return json.dumps({'loopdevices':[item]}) if command[0]=='losetup' else mount_source+'\n'
        with self.assertRaisesRegex(ValueError,'DATA mount ownership'):
            candidate.initialize_data(data,'/dev/loop31',backing,537919488,1072693248,run=run)
        self.assertEqual(data.stat().st_mode&0o777,0o755)
        mount_source='/dev/loop31';item['back-file']=str(self.root/'foreign.img')
        with self.assertRaisesRegex(ValueError,'Loop ownership'):
            candidate.initialize_data(data,'/dev/loop31',backing,537919488,1072693248,run=run)
        self.assertEqual(data.stat().st_mode&0o777,0o755)
        item['back-file']=str(backing)
        value=candidate.initialize_data(data,'/dev/loop31',backing,537919488,1072693248,run=run)
        self.assertEqual(data.stat().st_mode&0o7777,0o700);self.assertEqual(value['data_root_mode'],'0700')
        self.assertEqual(value['data_root_uid'],__import__('os').geteuid())

    def test_compressed_candidate_mbr_and_raw_identity_are_measured(self):
        inputs={'image_bytes':8192,'boot_offset':512,'boot_bytes':2048,'data_offset':2560}
        raw=bytearray(inputs['image_bytes']);raw[510:512]=b'\x55\xaa'
        for index,offset,size,kind in ((0,512,2048,0x0c),(1,2560,5632,0x83)):
            start=446+index*16;raw[start]=0x80 if index==0 else 0;raw[start+4]=kind;struct.pack_into('<II',raw,start+8,offset//512,size//512)
        image=self.root/'raw.img';image.write_bytes(raw)
        out=self.root/'out';out.mkdir();(out/'source.json').write_text(json.dumps({'version':'0.6.1','inputs':inputs}))
        for name,data in [('assembled',{'app_build_id':self.identity}),('container',{}),('filesystems',{'data_root_mode':'0700','data_root_uid':0,'data_root_gid':0}),('builder-packages',[]),('package-lock',{}),('kernel-packages',[])]:
            (out/(name+'.json')).write_text(json.dumps(data))
        (out/'mkimage-patched.sha256').write_text('a'*64)
        proof=out/'assembled.json';saved=proof.read_bytes();proof.unlink()
        with self.assertRaises(FileNotFoundError):candidate.finish(image,out)
        self.assertFalse(list(out.glob('*.img.xz')));self.assertFalse(list(out.glob('*.partial')))
        proof.write_bytes(saved)
        candidate.finish(image,out);value=json.loads((out/'alpine-standard-manifest.json').read_text());compressed=out/value['asset']
        self.assertEqual(compressed.read_bytes()[:6],b'\xfd7zXZ\x00');candidate.verify_image(compressed,value)
        (out/'filesystems.json').write_text(json.dumps({'data_root_mode':'0755','data_root_uid':0,'data_root_gid':0}))
        with self.assertRaisesRegex(ValueError,'DATA initialization'):candidate.finish(image,out)
        value['raw_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'Raw candidate identity'):candidate.verify_image(compressed,value)
        raw[450]=0x83
        with self.assertRaisesRegex(ValueError,'partition geometry'):candidate.partition_header(raw[:512],inputs)

    def test_initramfs_reader_rejects_unsafe_duplicate_and_truncated_data(self):
        path=self.root/'initramfs'
        for data in (cpio({'../outside':b'x'}),gzip.compress(b'bad header'),cpio({'safe':b'data'})[:12]):
            path.write_bytes(data)
            with self.assertRaises((ValueError,EOFError)):candidate.cpio_files(path)


if __name__=='__main__':unittest.main()
