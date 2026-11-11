from copy import deepcopy
from threading import Thread
import pytest
from playwright.async_api import async_playwright,expect
from agent.console import make_server
from agent.frustration import FrustrationMeter,PARAMETERS
from agent.human_alignment import summarize_alignment
from agent.persona_dashboard import PersonaDashboard,PROFILES


def test_frustration_terms_no_double_count_and_revisit():
    meter=FrustrationMeter(PROFILES[1])
    first=meter.observe('checkout',4)
    assert first['score']==60 and first['terms']=={'retained':0,'input':64,'revisit':0,'progress_reward':4,'search':0,'cost':0}
    repeat=meter.observe('checkout',4)
    assert repeat['score']==51 and repeat['signals']['new_required_fields']==0
    forward=meter.observe('cart',0)
    back=meter.observe('checkout',4)
    assert back['signals']['revisit']==1 and back['terms']['input']==0
    assert back['score']==round(.85*forward['score']+8,3)
    assert meter.observe('checkout',5)['terms']['input']==16


def test_alignment_denominators_and_technical_exclusion():
    sessions=[{'id':'quick','status':'simulated_dropout','terminal_stage':'signup','issue':{'category':'input_burden'}},
              {'id':'careful','status':'success'}, {'id':'patient','status':'agent_failure'}]
    def row(pid,outcome,stage,cause,participant):return dict(persona_id=pid,outcome=outcome,stage=stage,cause=cause,participant_id=participant)
    assert summarize_alignment(sessions,[])['metrics']['outcome']['rate'] is None
    rows=[row('quick','dropout','signup','input_burden','P1'),row('careful','dropout','checkout','unknown','P2'),
          row('patient','dropout','cart','navigation','P3'),row('careful','success','complete','none','P4')]
    report=summarize_alignment(sessions,rows)
    assert report['participants']==4 and report['excluded']==1
    assert report['metrics']['outcome']=={'matched':2,'total':3,'rate':.6667}
    assert report['metrics']['stage']=={'matched':1,'total':2,'rate':.5}
    assert report['metrics']['cause']=={'matched':1,'total':1,'rate':1.0}


def completed_manager(tmp_path):
    manager=PersonaDashboard(tmp_path)
    manager.state.update(status='completed',id='test_run',variant='fixed',sessions=[
        {**deepcopy(PROFILES[0]),'status':'simulated_dropout','terminal_stage':'checkout',
         'issue':{'category':'input_burden'},'steps':4,'events':[],'trace':[],'reason':'test fixture','artifact':None,'viewer_url':None}])
    return manager


def test_human_observations_stale_duplicate_and_persistence(tmp_path):
    manager=completed_manager(tmp_path)
    row={'run_id':'test_run','persona_id':'quick','participant_id':'P01','outcome':'dropout',
         'stage':'checkout','cause':'input_burden','source':'synthetic test fixture; not real human data'}
    manager.record_human(row);manager.record_human(row)
    assert manager.snapshot()['alignment']['participants']==1
    assert manager.snapshot()['human_observations'][0]['independently_verified'] is False
    for patch in [{'run_id':'other'},{'independently_verified':True},{'outcome':'success'},{'source':''}]:
        with pytest.raises(ValueError):manager.record_human({**row,**patch})
    restored=PersonaDashboard(tmp_path)
    assert restored.snapshot()['alignment']['participants']==1
    assert restored.snapshot()['human_observations'][0]['source']==row['source']


async def test_frustration_alignment_ui_evidence_and_manual_entry(tmp_path):
    server=make_server(0,tmp_path);server.store.personas=completed_manager(tmp_path)
    session=server.store.personas.state['sessions'][0]
    meter=FrustrationMeter(PROFILES[0])
    point=meter.observe('checkout',4);point.update(step=5,artifact=None,url='http://test/checkout')
    session['trace']=[point]
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1100,'height':1000})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                await page.goto(base+'/personas.html')
                await page.get_by_text('Human Alignment',exact=True).click()
                await expect(page.get_by_text('미검증 · 사람 관측 없음',exact=True)).to_be_visible()
                await expect(page.get_by_role('heading',name='배송 입력에서 멈췄어요')).to_be_visible()
                await expect(page.locator('.journey-insight')).to_contain_text('새 필수 입력 4개')
                await page.get_by_text('점수 그래프와 계산 근거 보기',exact=True).click()
                await expect(page.get_by_role('img',name='빠른 구매 좌절 점수 추이와 임계치 32')).to_be_visible()
                await page.get_by_role('button',name='5. 배송 입력 60.0').click()
                await expect(page.locator('.calculation')).to_contain_text('60.00')
                await page.get_by_text('실제 사람의 관측 등록',exact=True).click()
                await page.get_by_label('익명 참여자 ID').fill('P01')
                # Polling must preserve a partially entered observation.
                await page.wait_for_timeout(1800)
                await expect(page.get_by_label('익명 참여자 ID')).to_have_value('P01')
                await page.get_by_label('종료 단계',exact=True).select_option('checkout')
                await page.get_by_label('관측 출처·기록').fill('Synthetic UI test fixture, not actual human observations')
                await page.get_by_role('button',name='사람 관측 저장').click()
                await expect(page.locator('.alignment-metrics .stat strong').first).to_have_text('100%')
                await expect(page.locator('.human-records')).to_contain_text('P01')
                assert not errors
                response=await page.request.get(base+'/api/personas')
                assert 'artifact' not in (await response.json())['sessions'][0]['trace'][0]
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()


def test_human_observations_are_unique_per_task(tmp_path):
    manager=completed_manager(tmp_path)
    first=manager.state['sessions'][0];first.update(id='quick_order',task_id='order')
    second=deepcopy(first);second.update(id='quick_delivery',task_id='delivery',terminal_stage='delivery')
    manager.state['sessions'].append(second)
    row={'run_id':'test_run','persona_id':'quick_order','participant_id':'P01','outcome':'dropout',
         'stage':'checkout','cause':'input_burden','source':'Synthetic test fixture only'}
    manager.record_human(row)
    other={**row,'persona_id':'quick_delivery','stage':'delivery'}
    manager.record_human(other);manager.record_human(other)
    result=manager.snapshot()
    assert len(result['human_observations'])==2
    assert result['alignment']['participants']==1 and result['alignment']['observations']==2


async def test_workspace_review_filter_and_accessible_selection(tmp_path):
    server=make_server(0,tmp_path);server.store.personas=completed_manager(tmp_path)
    session=server.store.personas.state['sessions'][0]
    session.update(task_id='order',task_name='첫 주문',profile_id='quick')
    point=FrustrationMeter(PROFILES[0]).observe('checkout',4)
    point.update(step=5,artifact=None,url='http://test/checkout');session['trace']=[point]
    other=deepcopy(session);other.update(id='careful',name='꼼꼼한 첫 방문',status='success',issue=None,terminal_stage='complete')
    server.store.personas.state['sessions'].append(other)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':900})
            try:
                await page.goto(base+'/personas.html')
                await expect(page.locator('.persona')).to_have_count(2)
                await page.get_by_role('button',name='가상 이탈 1',exact=True).click()
                await expect(page.locator('.persona')).to_have_count(1)
                await expect(page.get_by_role('button',name='가상 이탈 1',exact=True)).to_have_attribute('aria-pressed','true')
                await page.locator('.review-item').click()
                await expect(page.locator('#detail-title')).to_contain_text('빠른 구매')
                await expect(page.locator('.journey-summary')).to_contain_text('배송 입력에서 멈췄어요')
                assert await page.locator('.persona.selected').count()==1
                await page.get_by_role('button',name='전체 2',exact=True).click()
                await expect(page.locator('.persona')).to_have_count(2)
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()
