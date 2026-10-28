#!/usr/bin/env python3
"""Build Docker from a verified local core artifact, never a copied app tree."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('core_artifact',ROOT/'tools/core-artifact.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


def main():
    p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);p.add_argument('--target',choices=('demo','linux'),default='demo');p.add_argument('--tag');args=p.parse_args()
    core.verified(args.wheel)
    with tempfile.TemporaryDirectory(prefix='astro-docker-') as temp:
        context=Path(temp)
        for source,name in ((args.wheel,'core.whl'),(ROOT/'core.lock.json','core.lock.json'),(ROOT/'docker/Dockerfile','Dockerfile'),(ROOT/'linux/tls.py','tls.py'),(ROOT/'docker/entrypoint.py','entrypoint.py'),(ROOT/'linux/build-conntrack.py','build-conntrack.py')):shutil.copyfile(source,context/name)
        subprocess.run(['docker','build','--target',args.target,'--tag',args.tag or 'astro-bridge:1.0.1-'+args.target,str(context)],check=True)


if __name__=='__main__':main()
