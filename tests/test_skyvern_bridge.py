from pathlib import Path
import json
import pytest
from agent.skyvern_bridge import SkyvernBridge,SkyvernUnavailable
from agent.console import ConsoleStore


def test_skyvern_connection_reuses_session_and_hides_credentials(tmp_path,monkeypatch):
    bridge=SkyvernBridge(tmp_path/'artifacts',tmp_path/'config')
    calls=[]
    def fake_api(path,body=None):
        calls.append((path,body))
        if body is not None and path=='/v1/browser_sessions':return {'browser_session_id':'pbs_1234'}
        return {'completed_at':None,'browser_address':'ws://127.0.0.1:9223/devtools/browser/test'}
    monkeypatch.setattr(bridge,'_api',fake_api)
    first=bridge.start();second=bridge.start()
    assert first['session_id']==second['session_id']=='pbs_1234'
    assert len([p for p,b in calls if p=='/v1/browser_sessions'])==1
    assert calls[0][1]=={'url':'http://127.0.0.1:4173/','timeout':30,'needs_live_view':True,'generate_browser_profile':False}
    assert 'browser_address' not in json.dumps(second)
    assert 'api_key' not in json.dumps(second).lower()
    assert second['iframe_url']=='http://localhost:8080/browser-session/pbs_1234'
    reloaded=SkyvernBridge(tmp_path/'artifacts',tmp_path/'config')
    assert reloaded.public()['session_id']=='pbs_1234'


def test_skyvern_missing_installation_is_explicit(tmp_path):
    bridge=SkyvernBridge(tmp_path/'artifacts',tmp_path/'no-config')
    with pytest.raises(SkyvernUnavailable):bridge.start()
    assert bridge.public()['status']=='disconnected'


def test_skyvern_checkpoint_refuses_nonlocal_cdp(tmp_path,monkeypatch):
    bridge=SkyvernBridge(tmp_path/'artifacts',tmp_path/'config')
    bridge.connection={'status':'connected','session_id':'pbs_1234','checkpoint':None}
    monkeypatch.setattr(bridge,'_api',lambda *args:{'browser_address':'ws://other.example:9222/devtools/browser/test'})
    with pytest.raises(ValueError,match='라이브 화면'):bridge.checkpoint()


def test_checkpoint_response_does_not_mutate_stored_evidence(tmp_path):
    store=ConsoleStore(tmp_path)
    folder=tmp_path/'skyvern';folder.mkdir(exist_ok=True)
    image=folder/'start.png';image.write_bytes(b'png')
    store.skyvern.connection={'status':'captured','session_id':'pbs_1234','checkpoint':{'artifact':str(image)}}
    response=store.connection_state()
    assert 'artifact' not in response['checkpoint']
    assert response['checkpoint']['artifact_id']
    assert store.skyvern.connection['checkpoint']['artifact']==str(image)
