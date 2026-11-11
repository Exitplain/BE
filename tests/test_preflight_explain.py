import base64
from copy import deepcopy
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import pytest
from playwright.async_api import async_playwright,expect
from agent.console import make_server
from agent.preflight import PreflightPlanner,discover_site
from agent.persona_dashboard import PersonaDashboard,PROFILES
from agent.frustration import PARAMETERS
from agent.session_explain import explain_session
from agent.shop_tasks import DEFAULT_TASK_IDS


def seed(store,tmp_path):
    image=tmp_path/'frame.png';image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6eB8AAAAASUVORK5CYII='))
    store.personas.state.update(status='completed',id='questions_fixture',variant='problem',human_observations=[],
        sessions=[{**deepcopy(PROFILES[0]),'task_id':'order','task_name':'첫 주문','goal':'모의 주문 완료',
                   'status':'simulated_dropout','reason':'가입 단계에서 모의 중단','steps':2,'viewer_url':None,'artifact':str(image),
                   'terminal_stage':'signup','issue':{'category':'input_burden'},
                   'events':[{'step':1,'action':'click','description':'주문하기 선택'},{'step':2,'action':'scroll','description':'가입 화면 탐색'}],
                   'trace':[{'step':3,'stage':'signup','crossed':True,'required_fields':9,'score':140,'threshold':32,
                             'signals':{'new_required_fields':9},'terms':{'retained':0,'input':144,'revisit':0,'progress_reward':4},'artifact':str(image),'url':'http://test/signup'}]}])


async def test_preflight_discovers_real_local_entries_without_running_personas():
    result=await discover_site('fixed')
    assert {f['id'] for f in result['features']}=={'shopping','delivery','coupon','subscription'}
    assert all(f['verification']=='visible_entry_only' and f['evidence']['label'] for f in result['features'])
    assert {t['id'] for t in result['tasks'] if t['suggested']}==set(DEFAULT_TASK_IDS)
    assert {t['id'] for t in result['tasks']}==set(DEFAULT_TASK_IDS)|{'cart','benefit'}


def test_preflight_confirmation_scope_and_age_not_stereotyped(tmp_path,monkeypatch):
    planner=PreflightPlanner();result=planner.discover('fixed')
    monkeypatch.setattr(Thread,'start',lambda self:None)
    body={'plan_id':result['plan_id'],'feature_ids':['shopping'],'task_ids':['cart'],'ages':['20s','30s'],
          'context':'출근길에 쓰는 고객','additional_request':'목록 밖 과업은 검토 메모'}
    variant,tasks,audience=planner.confirm(body)
    manager=PersonaDashboard(tmp_path);state=manager.start(variant,'all',tasks,audience)
    assert len(state['sessions'])==3 and all(s['task_id']=='cart' for s in state['sessions'])
    assert state['audience']['ages']==['20s','30s']
    assert [s['max_required_fields'] for s in state['sessions']]==[p['max_required_fields'] for p in PROFILES]
    for changed in [{'task_ids':['subscription']},{'ages':['any','20s']},{'plan_id':'expired'},{'url':'https://unapproved.example'}]:
        with pytest.raises(ValueError):planner.confirm({**body,**changed})


def test_session_answers_grounded_and_persisted(tmp_path):
    server=make_server(0,tmp_path);seed(server.store,tmp_path)
    state=server.store.persona_state()
    answer=explain_session(state,'quick','왜 입력하다 멈췄나요?')
    assert answer['kind']=='model_stop' and '140' in answer['answer'] and '32' in answer['answer']
    assert answer['sources'][0]['artifact_id']
    action=explain_session(state,'quick','왜 이 행동을 했나요?',1)
    assert action['kind']=='recorded_action' and '주문하기 선택' in action['answer']
    unknown=explain_session(state,'quick','실제 고객의 구매 의사가 얼마나 높나요?')
    assert not unknown['supported_by_record'] and not unknown['sources']
    server.store.personas.record_answer(state['id'],'quick',answer)
    restored=PersonaDashboard(tmp_path)
    assert restored.snapshot()['sessions'][0]['answers'][0]['id']==answer['id']
    with pytest.raises(ValueError):server.store.personas.record_answer('old','quick',answer)
    server.server_close()


async def test_card_popup_questions_and_escape_restore_selection(tmp_path):
    server=make_server(0,tmp_path);seed(server.store,tmp_path)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1300,'height':900})
            try:
                await page.goto(base+'/#personas');f=page.frame_locator('iframe[title="페르소나 실행 대시보드"]')
                await f.get_by_role('button',name='빠른 구매 상세 보기',exact=True).click()
                popup=f.get_by_role('dialog',name='빠른 구매 · 첫 주문')
                await expect(popup).to_be_visible()
                await expect(popup.locator('#agent-popup-image')).to_be_visible()
                await expect(popup.locator('.session-answer')).to_have_count(1)
                await expect(popup.locator('.session-answer')).to_contain_text('140')
                assert await popup.locator('.session-answer p').evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)')>=15
                assert await popup.evaluate('(e)=>e.getBoundingClientRect().width')<=1160
                await page.keyboard.press('Escape')
                await f.get_by_role('button',name='빠른 구매 상세 보기',exact=True).click()
                await expect(popup.locator('.session-answer')).to_have_count(1)
                await popup.get_by_label('세션에 대한 질문').fill('실제 고객의 감정을 알 수 있나요?')
                await popup.get_by_role('button',name='질문하기',exact=True).click()
                await expect(popup.locator('.session-answer')).to_have_count(2)
                await expect(popup.locator('.session-answer').last).to_contain_text('기록으로 확인할 수 없음')
                await page.keyboard.press('Escape');await expect(popup).not_to_be_visible()
                assert await page.locator('html').evaluate('(e)=>e.style.overflow')!='hidden'
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()


async def test_guided_setup_confirms_features_tasks_age_before_start(tmp_path,monkeypatch):
    monkeypatch.setattr(PersonaDashboard,'worker',lambda *args:None)
    server=make_server(0,tmp_path);thread=Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1100,'height':900})
            try:
                await page.goto(base+'/personas.html');await page.get_by_role('button',name='실행 준비',exact=True).click()
                dialog=page.locator('#setup-dialog')
                await expect(dialog.locator('[data-feature]')).to_have_count(4,timeout=15000)
                assert server.store.personas.snapshot()['status']=='idle'
                await dialog.get_by_role('button',name='다음',exact=True).click()
                await dialog.locator('[data-task=subscription]').uncheck()
                await dialog.locator('[data-task=cart]').check()
                await dialog.get_by_label('20대',exact=True).check();await dialog.get_by_label('30대',exact=True).check()
                await dialog.locator('#target-context').fill('출근길 첫 방문 고객')
                assert server.store.personas.snapshot()['status']=='idle'
                await dialog.get_by_role('button',name='다음',exact=True).click()
                await expect(dialog.get_by_role('heading',name='퍼널 · 페르소나',exact=True)).to_be_visible()
                assert server.store.personas.snapshot()['status']=='idle'
                await dialog.get_by_role('button',name='다음',exact=True).click()
                await expect(dialog.get_by_role('heading',name='시작 전 확인',exact=True)).to_be_visible()
                assert server.store.personas.snapshot()['status']=='idle'
                async with page.expect_response(lambda r:r.url.endswith('/api/personas/confirm')) as pending:
                    await dialog.get_by_role('button',name='확인하고 실행',exact=True).click()
                assert (await pending.value).status==202
                result=server.store.personas.snapshot()
                assert {s['task_id'] for s in result['sessions']}=={'order','delivery','coupon','cart'}
                assert result['audience']['ages']==['20s','30s'] and len(result['sessions'])==12
                await expect(dialog).not_to_be_visible()
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()


async def test_embedded_mobile_setup_keeps_navigation_in_view(tmp_path, monkeypatch):
    monkeypatch.setattr(PersonaDashboard, 'worker', lambda *args: None)
    server = make_server(0, tmp_path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 390, 'height': 844})
                await page.goto(f'http://127.0.0.1:{server.server_port}/#personas')
                frame = page.frame_locator('iframe[title="페르소나 실행 대시보드"]')
                await frame.get_by_role('button', name='실행 준비', exact=True).click()
                await expect(frame.locator('[data-feature]')).to_have_count(4, timeout=15000)
                for _ in range(3):
                    await frame.locator('#setup-next').click()
                await expect(frame.get_by_role('heading', name='시작 전 확인', exact=True)).to_be_visible()
                assert server.store.personas.snapshot()['status'] == 'idle'
                button = await frame.locator('#setup-next').bounding_box()
                assert button['y'] + button['height'] <= 844
                await frame.locator('[data-edit-step="2"]').first.click()
                await frame.locator('#target-context').fill('모바일 첫 방문 고객')
                await frame.locator('#setup-next').click()
                await frame.locator('#setup-next').click()
                await expect(frame.locator('#setup-content')).to_contain_text('모바일 첫 방문 고객')
                async with page.expect_response(lambda r: r.url.endswith('/api/personas/confirm')) as response:
                    await frame.get_by_role('button', name='확인하고 실행', exact=True).click()
                assert (await response.value).status == 202
                assert server.store.personas.snapshot()['audience']['context'] == '모바일 첫 방문 고객'
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
