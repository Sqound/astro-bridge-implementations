#!/usr/bin/env python3
"""Disposable real Docker demo smoke test, including saved state after recreation."""
import http.cookiejar
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def command(*args):return subprocess.check_output(['docker',*args],text=True).strip()


def main():
    name='astro-release-test-'+uuid.uuid4().hex[:10];volume=name+'-state'
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url='http://127.0.0.1:'+str(port);command('volume','create',volume)
    try:
        for cycle in range(2):
            command('run','-d','--name',name,'--cap-drop','ALL','--security-opt','no-new-privileges','-p',f'127.0.0.1:{port}:8787','-v',volume+':/var/lib/wifi-bridge','astro-bridge:1.0.0-demo')
            client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def call(path,data=None,csrf=None):
                headers={'Host':'127.0.0.1:8787','Origin':'http://127.0.0.1:8787','Content-Type':'application/json'}
                if csrf:headers['X-CSRF-Token']=csrf
                with client.open(urllib.request.Request(url+path,headers=headers,data=json.dumps(data).encode() if data is not None else None),timeout=3) as response:return json.load(response)
            for _ in range(150):
                try:public=call('/api/status');raise AssertionError('Unauthenticated status is accessible')
                except urllib.error.HTTPError as error:
                    if error.code==401:break
                    time.sleep(.2)
                except (OSError,urllib.error.URLError):time.sleep(.2)
            else:raise AssertionError(command('logs',name))
            csrf=call('/api/login',{'password':'demo-telescope-2026'})['csrf']
            status=call('/api/status');assert status['demo']
            assert status['deployment']['platform']=='demo'
            if cycle:assert status['config']['hostname']=='docker-persisted'
            config=status['config'];config.update(hostname='docker-persisted',admin_password='')
            call('/api/config',config,csrf);assert call('/api/status')['config']['hostname']=='docker-persisted'
            command('stop','--time','30',name)
            inspected=json.loads(command('inspect',name))[0]
            assert inspected['State']['ExitCode']==0
            command('rm',name)
        print('PASS real Docker demo: unauthenticated isolation, login, config write, clean stop and volume persistence after recreation')
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['docker','volume','rm',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':main()
