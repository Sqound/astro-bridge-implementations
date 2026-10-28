#!/usr/bin/env python3
"""Run platform contracts against the pinned wheel in a disposable staged project."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('core',ROOT/'tools/core-artifact.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


def main():
    p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);args=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='astro-platform-tests-') as folder:
        stage=Path(folder)/'project';core.stage(args.wheel,stage)
        shutil.copytree(ROOT/'linux',stage/'linux')
        for name in ('install-docker.py','install-docker.sh','build-flasher.py','collect-image-sources.py'):
            shutil.copyfile(ROOT/'tools'/name,stage/'tools'/name)
        for test in (ROOT/'tests').glob('test_*.py'):shutil.copyfile(test,stage/'tests'/test.name)
        subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=stage,check=True)


if __name__=='__main__':main()
