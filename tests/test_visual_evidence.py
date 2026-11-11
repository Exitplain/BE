import struct
from threading import Thread
from playwright.async_api import async_playwright,expect
from agent.browser import observe
from agent.visual_evidence import annotation
from agent.console import make_server
from agent.persona_dashboard import PROFILES
from agent.frustration import PARAMETERS


async def test_observed_bboxes_match_css_screenshot_and_skip_hidden_targets():
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':800,'height':600},device_scale_factor=2)
        try:
            await page.set_content('<label>Email<input required></label><input required style="display:none"><div class="fee-warning">배송비 3,500원이 추가됩니다.</div>')
            seen=await observe(page)
            assert struct.unpack('>II',seen['screenshot'][16:24])==(800,600)
            fields=annotation(seen,'input_burden')
            assert len(fields['regions'])==1
            box=await page.locator('input:visible').bounding_box()
            assert fields['regions'][0]['box']==box
            assert fields['viewport']=={'width':800,'height':600}
            assert len(annotation(seen,'cost')['regions'])==1
            assert not annotation(seen,'navigation')['regions']
        finally:await browser.close()


async def test_report_bbox_viewer_and_generated_mockup_are_distinct(tmp_path):
    server=make_server(0,tmp_path);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1200,'height':900})
            try:
                await page.set_content('<label>Email<input required></label><label>Name<input required></label>')
                seen=await observe(page);image=tmp_path/'actual.png';image.write_bytes(seen['screenshot'])
                server.store.personas.state={'id':'bbox_fixture','status':'completed','variant':'problem','human_observations':[],
                    'profiles':PROFILES,'frustration_model':PARAMETERS,'sessions':[
                        {'id':'quick','name':'빠른 구매','status':'simulated_dropout','task_id':'order','task_name':'첫 주문',
                         'terminal_stage':'signup','issue':{'category':'input_burden'},'events':[],
                         'trace':[{'stage':'signup','step':1,'crossed':True,'score':60,'threshold':32,'required_fields':2,
                                  'signals':{},'url':base+'/signup','artifact':str(image),
                                  'visual_evidence':{'input_burden':annotation(seen,'input_burden')}}]}]}
                await page.goto(base+'/#report/bbox_fixture');f=page.frame_locator('iframe[title="진단 리포트"]')
                await expect(f.locator('.finding-overview .bbox')).to_have_count(2)
                await expect(f.get_by_text('AI 생성 개선 목업 · 실제 적용 전',exact=True)).to_be_visible()
                mockup=f.get_by_role('img',name='AI가 생성한 가입 단계 개선 목업')
                await expect(mockup).to_be_visible()
                assert await mockup.evaluate('(e)=>e.complete&&e.naturalWidth>100')
                await f.locator('.finding-overview .screen-button').click()
                await expect(f.locator('#screen-dialog')).to_be_visible()
                assert await f.locator('#screen-dialog .bbox').count()==2
                await f.get_by_role('button',name='200%',exact=True).click()
                assert await f.locator('#viewer-scroll svg').evaluate('(e)=>parseFloat(e.style.width)')==2400
                await f.get_by_role('button',name='닫기 ×').click()
                await expect(f.locator('#screen-dialog')).not_to_be_visible()
            finally:await browser.close()
    finally:server.shutdown();server.server_close();thread.join()
