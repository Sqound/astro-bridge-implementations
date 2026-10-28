#!/usr/bin/env python3
"""Deploy a verified offline source using the installer, then authenticate over HTTP."""
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]


def main():
    version=(ROOT/'VERSION').read_text().strip();project='astro-installer-test-'+uuid.uuid4().hex[:10]
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='astro-docker-installer-') as folder:
        base=Path(folder);release=base/'release';prefix=base/'deployment'
        subprocess.run([sys.executable,str(ROOT/'tools/package-release.py'),'--output',str(release)],check=True)
        record=json.loads((release/'release.json').read_text());asset=f'astro_bridge_implementations-{version}.tar.gz'
        env=dict(os.environ,ASTRO_BRIDGE_PORT=str(port));compose=prefix/'docker/compose.demo.yaml'
        try:
            subprocess.run([sys.executable,str(release/'install-docker.py'),'--apply','--mode','demo','--source',str(release/asset),'--sha256',record['artifacts'][asset],'--prefix',str(prefix),'--project',project,'--port',str(port)],check=True)
            url='http://127.0.0.1:'+str(port)
            client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def request(path,data=None,csrf=None):
                headers={'Origin':url,'Content-Type':'application/json'}
                if csrf:headers['X-CSRF-Token']=csrf
                with client.open(urllib.request.Request(url+path,headers=headers,data=json.dumps(data).encode() if data else None),timeout=3) as response:return json.load(response)
            for _ in range(100):
                try:request('/api/status');raise AssertionError('Anonymous private status')
                except urllib.error.HTTPError as error:
                    if error.code==401:break
                    time.sleep(.2)
                except OSError:time.sleep(.2)
            else:raise AssertionError('Installed deployment did not become ready')
            session=request('/api/login',{'password':'demo-telescope-2026'})
            status=request('/api/status');assert status['demo'] and request('/api/updates')['current_version']==version
            assert session['csrf'] and (prefix/'core.lock.json').is_file()
            print('PASS Docker installer: verified source/core, image build, Compose deployment and real browser-origin authentication on the configured port')
        finally:
            if compose.exists():subprocess.run(['docker','compose','-p',project,'-f',str(compose),'down','--volumes'],env=env,check=True)


if __name__=='__main__':main()
