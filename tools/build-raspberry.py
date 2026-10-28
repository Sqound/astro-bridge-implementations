#!/usr/bin/env python3
"""Stage pinned core + Raspberry recipe and build the existing inspected SD format."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('core',ROOT/'tools/core-artifact.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


def main():
    p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);p.add_argument('--stage',type=Path,default=ROOT/'build/raspberry');p.add_argument('--build',action='store_true');args=p.parse_args()
    core.stage(args.wheel,args.stage)
    if args.build:subprocess.run(['bash',str(args.stage/'image/alpine/standard/build.sh'),'--output',str(args.stage/'dist/standard-1.0.1')],check=True)
    else:print('Staged only. Add --build with a new --stage to build the disk image.')


if __name__=='__main__':main()
