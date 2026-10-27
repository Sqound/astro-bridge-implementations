#!/usr/bin/env python3
"""Fail closed for modified core artifacts and staged build inputs."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('core',ROOT/'tools/core-artifact.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


def main():
    p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);args=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='astro-staging-guard-') as folder:
        root=Path(folder);bad=root/args.wheel.name;shutil.copyfile(args.wheel,bad)
        with bad.open('ab') as stream:stream.write(b'modified')
        try:core.verified(bad)
        except ValueError:pass
        else:raise AssertionError('Modified wheel accepted')
        stage=root/'project';core.stage(args.wheel,stage)
        candidate_spec=importlib.util.spec_from_file_location('candidate',stage/'image/alpine/standard/candidate.py');candidate=importlib.util.module_from_spec(candidate_spec);candidate_spec.loader.exec_module(candidate)
        source=stage/'wifi_bridge/__init__.py';source.write_text('__version__ = "9.9.9"\n')
        snapshot=root/'snapshot';snapshot.mkdir()
        with patch('shutil.disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
            try:candidate.prepare(stage/'dist/changed',snapshot)
            except ValueError as error:assert 'Staged source changed' in str(error)
            else:raise AssertionError('Changed staged core accepted')
    print('PASS modified wheel and staged core are refused before image assembly')


if __name__=='__main__':main()
