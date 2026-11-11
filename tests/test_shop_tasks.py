from pathlib import Path
from urllib.parse import urlsplit
from threading import Thread
import pytest
from playwright.async_api import async_playwright
from agent.shop import shop_server
from agent.shop_tasks import TASKS,DEFAULT_TASK_IDS,ShopTaskPolicy,task_succeeded
from agent.persona_dashboard import PROFILES,PersonaDashboard
from agent.frustration import FrustrationMeter,STAGES
from agent.browser import observe,act
from agent.schemas import Persona


async def run_task(browser,base,task,profile,variant):
    context=await browser.new_context(viewport={'width':profile['width'],'height':900})
    try:
        page=await context.new_page();await page.goto(base+f"/?variant={variant}&case={task['case']}")
        policy=ShopTaskPolicy(task['id']);meter=FrustrationMeter(profile);history=[];points=[]
        for step in range(40):
            observed=await observe(page);path=urlsplit(page.url).path;stage=STAGES.get(path,'other')
            point=meter.observe(stage,observed['required_fields'],
                search_scroll=bool(history and history[-1]['action']=='scroll' and stage==meter.previous_stage),
                unexpected_cost='배송비 3,500원이 추가됩니다.' in observed['text'])
            points.append(point)
            if task_succeeded(task,path,observed['text']):return 'success',stage,points
            if point['crossed']:return 'simulated_dropout',stage,points
            action,_=await policy.decide(observed,Persona(id=profile['id']),task['goal'],history)
            await act(page,action,observed);history.append({'action':action.kind})
        return 'budget_exceeded',stage,points
    finally:await context.close()


async def test_multi_task_normal_controls_and_distinct_problem_stops(tmp_path):
    with shop_server() as base:
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            try:
                # Tolerant control must finish every supported task, including coupon application.
                for task in TASKS.values():
                    status,stage,points=await run_task(browser,base,task,PROFILES[2],'fixed')
                    assert status=='success',(task['id'],stage,points[-1])
                observed_stops={}
                for task_id,profile in [('order',PROFILES[0]),('delivery',PROFILES[0]),
                                        ('subscription',PROFILES[1]),('coupon',PROFILES[1])]:
                    status,stage,points=await run_task(browser,base,TASKS[task_id],profile,'problem')
                    assert status=='simulated_dropout',(task_id,status,stage)
                    observed_stops[task_id]=stage
                    if task_id=='coupon':assert points[-1]['terms']['cost']>0
                assert observed_stops=={'order':'signup','delivery':'delivery','subscription':'subscription','coupon':'checkout'}
            finally:await browser.close()


def test_suite_allocates_distinct_sessions_and_rejects_unknown_task(tmp_path,monkeypatch):
    monkeypatch.setattr(Thread,'start',lambda self:None)
    manager=PersonaDashboard(tmp_path)
    with pytest.raises(ValueError):manager.start('fixed','external')
    state=manager.start('fixed','all')
    assert len(state['sessions'])==12
    assert len({s['id'] for s in state['sessions']})==12
    assert all(sum(s['task_id']==t for s in state['sessions'])==3 for t in DEFAULT_TASK_IDS)


def test_search_cost_is_persona_specific_and_fee_not_double_counted():
    quick=FrustrationMeter(PROFILES[0]);patient=FrustrationMeter(PROFILES[2])
    assert quick.observe('benefits',0,search_scroll=True)['terms']['search']==16
    assert patient.observe('benefits',0,search_scroll=True)['terms']['search']==6
    first=patient.observe('checkout',4,unexpected_cost=True)
    repeat=patient.observe('checkout',4,unexpected_cost=True)
    assert first['terms']['cost']==20 and repeat['terms']['cost']==0
