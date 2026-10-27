# Shared local/CI build specification. Metadata and notices precede BUNDLE signing.
from pathlib import Path
import os
import runpy

tools = Path(SPECPATH)
bundle = runpy.run_path(str(tools / 'flasher-macos.py'))
notices = Path(os.environ['ASTRO_FLASHER_NOTICES']).resolve()
datas = bundle['validated_datas'](notices)
app_version = bundle['version']()

a = Analysis([str(tools / 'astro-bridge-flasher.py')], pathex=[], binaries=[],
             datas=datas, hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Astro Bridge Flasher',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=False, disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[],
               name='Astro Bridge Flasher')
app = BUNDLE(coll, name='Astro Bridge Flasher.app', icon=None,
             bundle_identifier='space.planastro.astrobridge.flasher',
             version=app_version, info_plist={'CFBundleVersion': app_version})
