"""Manage the isolated, local Skyvern services prepared for this workspace."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
WORK=Path.home()/'.cache/dropspot-skyvern-work'
PYTHON=Path.home()/'.cache/dropspot-skyvern/bin/python'
PG=Path.home()/'.cache/dropspot-postgres/pgserver/pginstall/bin/pg_ctl'
DB=Path.home()/'.cache/dropspot-skyvern-db'
STATE=WORK/'dropspot-processes.json'


def reachable(url):
    try:
        with urlopen(url,timeout=2) as r:return r.status==200
    except Exception:return False


def start():
    if not PYTHON.is_file() or not (WORK/'.env').is_file():
        raise RuntimeError('Skyvern isolated installation is missing; see integrations/skyvern/README.md')
    db_status=subprocess.run([str(PG),'-D',str(DB),'status'],capture_output=True).returncode
    if db_status:
        subprocess.run([str(PG),'-D',str(DB),'-l',str(WORK/'postgres.log'),'-o','-h 127.0.0.1 -p 55432','start'],check=True,stdout=subprocess.DEVNULL)
    state=json.loads(STATE.read_text()) if STATE.is_file() else {}
    env={**os.environ,'SKYVERN_TELEMETRY':'false','PYTHONUNBUFFERED':'1'}
    commands={
        'api':([str(PYTHON),str(ROOT/'integrations/skyvern/run_server.py')],WORK,'http://127.0.0.1:8000/api/v1/heartbeat'),
        'ui':(['node','start.mjs'],WORK/'ui','http://127.0.0.1:8080'),
    }
    for name,(command,cwd,url) in commands.items():
        if reachable(url):continue
        with (WORK/(name+'-managed.log')).open('a') as log:
            process=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=log,start_new_session=True)
        state[name]={'pid':process.pid,'command':command}
    STATE.write_text(json.dumps(state,indent=2));STATE.chmod(0o600)
    deadline=time.monotonic()+75
    while time.monotonic()<deadline:
        if all(reachable(item[2]) for item in commands.values()):break
        time.sleep(1)
    return status()


def status():
    result={'api':reachable('http://127.0.0.1:8000/api/v1/heartbeat'),'ui':reachable('http://127.0.0.1:8080'),
        'model_credentials':any(os.environ.get(k) for k in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','GOOGLE_API_KEY'])}
    print(json.dumps(result,indent=2))
    return 0 if result['api'] and result['ui'] else 2


def stop():
    if STATE.is_file():
        for item in json.loads(STATE.read_text()).values():
            proc=Path('/proc')/str(item['pid'])/'cmdline'
            try:
                cmdline=proc.read_bytes().replace(b'\0',b' ').decode()
                if 'run_server.py' in cmdline or 'start.mjs' in cmdline:os.kill(item['pid'],signal.SIGTERM)
            except (OSError,ProcessLookupError):pass
        STATE.unlink()
    print('Stopped only service processes recorded by this launcher. Database retained.')
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','status','stop'])
    args=parser.parse_args()
    try:raise SystemExit({'start':start,'status':status,'stop':stop}[args.action]())
    except RuntimeError as exc:print(str(exc));raise SystemExit(2)
