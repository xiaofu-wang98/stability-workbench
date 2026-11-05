import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import pytest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('launcher',['start.py'])
def test_launcher_allocates_real_isolated_port_and_identifies_preview(launcher):
    home = ROOT/'outputs/v5-acceptance'/('launcher-'+uuid4().hex)
    log = ROOT/'outputs/v5-acceptance'/('launcher-'+uuid4().hex+'.log')
    log.parent.mkdir(parents=True,exist_ok=True)
    env = {**os.environ,'PYTHONPATH':str(ROOT/'src'),'PYTHONIOENCODING':'utf-8'}
    with log.open('w',encoding='utf-8') as stream:
        process = subprocess.Popen([sys.executable,str(ROOT/launcher),'--home',str(home),'--port','0']+['--no-browser'],
            env=env,stdout=stream,stderr=stream,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline = time.monotonic()+15
            port = None
            while time.monotonic()<deadline and process.poll() is None:
                match = re.search(r'http://127\.0\.0\.1:(\d+)/docs',log.read_text(encoding='utf-8'))
                if match:
                    port = int(match[1])
                    break
                time.sleep(.05)
            assert port,log.read_text(encoding='utf-8')
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            health = None
            while time.monotonic()<deadline:
                try:
                    with opener.open(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                        health = json.load(response)
                    break
                except OSError:
                    time.sleep(.05)
            assert health and health['delivery_state']=='release' and health['version']=='0.1.5'
            with opener.open(f'http://127.0.0.1:{port}/api/v1/projects',timeout=2) as response:
                assert json.load(response)==[]
            with opener.open(f'http://127.0.0.1:{port}/',timeout=2) as response:
                assert 'V5 worksheet commands' in response.read().decode('utf-8')
            assert (home/'history.sqlite').is_file()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
