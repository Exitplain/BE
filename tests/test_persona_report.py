import base64
from copy import deepcopy
import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen
import pytest
from playwright.async_api import async_playwright,expect
from agent.console import make_server
from agent.persona_dashboard import PROFILES
from agent.frustration import PARAMETERS
from agent.persona_report import build_report


def report_state(tmp_path):
    image=tmp_path/'trace.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6eB8AAAAASUVORK5CYII='))
    sessions=[]
    for index in range(2):
        sessions.append({**deepcopy(PROFILES[index]),'task_id':'order','task_name':'첫 주문','status':'simulated_dropout',
            'terminal_stage':'signup','issue':{'category':'input_burden'},'artifact':str(image),'events':[{'step':1,'description':'주문하기 선택'}],
            'trace':[{'stage':'signup','crossed':True,'required_fields':9,'step':2,'score':140,'threshold':64,
                     'signals':{},'artifact':str(image),'url':'http://127.0.0.1:12345/signup?variant=problem'}]})
    sessions.append({'id':'failed','name':'실행 오류','task_id':'order','task_name':'첫 주문','status':'agent_failure','reason':'Fixture failure','issue':None,'trace':[]})
    return {'status':'completed','id':'report_fixture','sessions':sessions,'profiles':deepcopy(PROFILES),
        'human_observations':[],'variant':'problem','frustration_model':deepcopy(PARAMETERS),'policy':'scripted_fixture','model_calls':0}


def test_report_groups_locations_and_keeps_technical_failure_separate(tmp_path):
    server=make_server(0,tmp_path);server.store.personas.state=report_state(tmp_path)
    try:
        report=server.store.persona_report()
        assert report['summary']=={'sessions':3,'success':0,'simulated_dropout':2,'technical':1,'findings':1,'missing_evidence':0}
        finding=report['findings'][0]
        assert finding['affected_sessions']==2 and finding['task_sessions']==3
        assert finding['stage']=='signup' and finding['evidence'][0]['path']=='/signup'
        assert finding['evidence'][0]['steps_before']==['주문하기 선택']
        assert finding['evidence'][0]['artifact_id']
        assert report['technical_failures'][0]['session_id']=='failed'
        assert report['alignment']['participants']==0
        assert str(tmp_path) not in json.dumps(report)
        # Missing problem-step evidence must stay missing; never substitute a final screen.
        server.store.personas.state['sessions'][0]['trace'][0].pop('artifact')
        missing=server.store.persona_report()
        assert missing['summary']['missing_evidence']==1
    finally:server.server_close()


def test_report_endpoints_historical_download_and_incomplete_guard(tmp_path):
    server=make_server(0,tmp_path);state=report_state(tmp_path);server.store.personas.state=state
    folder=tmp_path/'persona-runs'/state['id'];folder.mkdir(parents=True);(folder/'summary.json').write_text(json.dumps(state))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base+'/api/personas/report.md?run_id=report_fixture') as response:
            text=response.read().decode();assert '회원가입에서 필수 입력 부담' in text
            assert '/api/artifacts/' in text and '사람 관측 0명' in text
            assert 'text/markdown' in response.headers['Content-Type']
        with urlopen(base+'/api/personas/reports') as response:
            assert json.load(response)['reports'][0]['run_id']=='report_fixture'
        server.store.personas.state={'id':'new','status':'running','sessions':[],'human_observations':[]}
        with urlopen(base+'/api/personas/report?run_id=report_fixture') as response:
            assert json.load(response)['summary']['findings']==1
        for suffix in ['', '?run_id=..%2F..%2Fsecret','?run_id=missing']:
            with pytest.raises(HTTPError) as bad:urlopen(base+'/api/personas/report'+suffix)
            assert bad.value.code in {404,409}
    finally:server.shutdown();server.server_close();thread.join()


async def test_report_ui_problem_evidence_and_markdown_download(tmp_path):
    server=make_server(0,tmp_path);server.store.personas.state=report_state(tmp_path)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1300,'height':1000})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                await page.goto(base+'/#report/report_fixture')
                frame=page.frame_locator('iframe[title="진단 리포트"]')
                await expect(frame.get_by_role('heading',name='1. 회원가입에서 필수 입력 부담',exact=True)).to_be_visible()
                await expect(frame.locator('.finding')).to_have_count(1)
                await expect(frame.get_by_text('현재 화면의 필수 입력 9개',exact=True)).to_be_visible()
                img=frame.get_by_role('img',name='회원가입에서 필수 입력 부담 당시 실제 화면')
                await expect(img).to_be_visible()
                assert await img.evaluate('(e)=>e.complete&&e.naturalWidth>0')
                await frame.get_by_text('영향받은 2개 세션의 경로·점수·화면',exact=True).click()
                await expect(frame.locator('.session-evidence')).to_contain_text('주문하기 선택')
                async with page.expect_download() as pending:
                    await frame.get_by_role('link',name='Markdown 다운로드').click()
                download=await pending.value
                assert download.suggested_filename=='dropspot-report-report_fixture.md'
                assert '회원가입에서 필수 입력 부담' in Path(await download.path()).read_text()
                assert not errors
                # A new running job must not hide the last completed report.
                server.store.personas.persist(server.store.personas.state)
                server.store.personas.state={'id':'new','status':'running','sessions':[],'human_observations':[]}
                await page.goto(base+'/#report')
                await expect(frame.locator('.finding')).to_have_count(1)
                await expect(frame.get_by_label('리포트 실행 선택')).to_have_value('report_fixture')
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()
