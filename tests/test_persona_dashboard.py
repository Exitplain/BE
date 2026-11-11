import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import pytest
from agent.console import make_server
from agent.persona_dashboard import PersonaDashboard,PROFILES
from agent.frustration import FrustrationMeter


def test_persona_budgets_change_outcomes_without_using_variant():
    # The exact same visible checkout leads to different configured outcomes.
    assert FrustrationMeter(PROFILES[0]).observe('checkout',4)['crossed']
    assert not FrustrationMeter(PROFILES[1]).observe('checkout',4)['crossed']
    assert not FrustrationMeter(PROFILES[2]).observe('checkout',4)['crossed']
    assert all(FrustrationMeter(p).observe('signup',9)['crossed'] for p in PROFILES)


def test_persona_start_rejects_overlap_and_snapshot_is_isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(Thread,'start',lambda self:None)
    manager=PersonaDashboard(tmp_path)
    with pytest.raises(ValueError):manager.start('https://unapproved.example')
    state=manager.start('fixed')
    assert len(state['sessions'])==3
    assert len({s['id'] for s in state['sessions']})==3
    assert state['policy']=='scripted_fixture' and state['model_calls']==0
    state['sessions'][0]['status']='success'
    assert manager.snapshot()['sessions'][0]['status']=='queued'
    with pytest.raises(RuntimeError):manager.start('problem')


def test_persona_api_rejects_customer_target_and_hides_local_paths(tmp_path,monkeypatch):
    from agent.persona_dashboard import PersonaDashboard
    monkeypatch.setattr(PersonaDashboard,'worker',lambda *args:None)
    server=make_server(0,tmp_path)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def post(body,origin=None):
        return urlopen(Request(base+'/api/personas/start',data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json',**({'Origin':origin} if origin else {})}))
    try:
        with pytest.raises(HTTPError) as bad:post({'variant':'fixed','url':'https://unapproved.example'})
        assert bad.value.code==400
        with pytest.raises(HTTPError) as bad:post({'variant':'fixed'},'https://other.example')
        assert bad.value.code==403
        with post({'variant':'fixed'}) as response:
            result=json.load(response)
            assert response.status==202 and len(result['sessions'])==3
            assert all('artifact' not in s and 'browser_address' not in s for s in result['sessions'])
        with urlopen(base+'/personas.html') as response:
            assert '실행할 과업' in response.read().decode()
    finally:server.shutdown();server.server_close();thread.join()
