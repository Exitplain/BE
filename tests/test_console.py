from contextlib import contextmanager
from threading import Thread
from pathlib import Path
from playwright.async_api import async_playwright, expect
from agent.console import make_server, ConsoleStore


@contextmanager
def console_server(tmp_path):
    server = make_server(0, tmp_path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', server.store
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def start_diagnosis(page, variant):
    await page.get_by_role('button', name='새 진단 시작', exact=True).click()
    dialog = page.get_by_role('dialog')
    await dialog.get_by_label('프로젝트',exact=True).select_option('banchan')
    await dialog.get_by_label('사이트 버전').select_option(variant)
    await dialog.get_by_label('점검 조건').select_option('signup')
    async with page.expect_response(lambda r: r.url.endswith('/api/runs') and r.request.method=='POST') as pending:
        await dialog.get_by_role('button',name='진단 시작',exact=True).click()
    response = await pending.value
    assert response.status == 202
    job = (await response.json())['run_id']
    await expect(page).to_have_url(f'{page.url.split("#")[0]}#run/{job}')
    await expect(page.get_by_role('heading',name='가상 세션 실행 결과')).to_be_visible(timeout=20000)
    return job


async def test_console_real_run_evidence_and_comparison(tmp_path):
    with console_server(tmp_path) as (base,store):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width':1440,'height':1000})
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(base)
                await expect(page.get_by_role('heading',name='대시보드')).to_be_visible()
                job = await start_diagnosis(page,'problem')
                assert await page.get_by_text('가상 이탈',exact=True).count() == 2
                await page.get_by_role('button',name='해당 화면 확인').first.click()
                image = page.get_by_role('dialog').get_by_role('img')
                await expect(image).to_be_visible()
                assert await image.evaluate('(el)=>el.complete && el.naturalWidth>0')
                await page.get_by_role('button',name='창 닫기').click()
                await page.get_by_role('link',name='진단 기록',exact=True).click()
                fixed = await start_diagnosis(page,'fixed')
                assert await page.locator('.badge').filter(has_text='목표 도달').count() == 2
                await page.locator('.account-menu > summary').click()
                await page.get_by_role('link',name='변경 비교',exact=True).click()
                assert '0%' in await page.locator('#compare-before').inner_text()
                assert '100%' in await page.locator('#compare-after').inner_text()
                assert not errors
                # Stored job identity survives a console restart/reload of persisted results.
                reloaded = ConsoleStore(tmp_path)
                assert job in reloaded.runs and fixed in reloaded.runs
            finally:
                await browser.close()


async def test_console_project_settings_and_mobile(tmp_path):
    with console_server(tmp_path) as (base,store):
        async with async_playwright() as pw:
            browser=await pw.chromium.launch()
            try:
                page=await browser.new_page(viewport={'width':390,'height':844})
                await page.goto(base)
                await expect(page.get_by_role('heading',name='대시보드')).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                await page.get_by_role('button',name='사이트 등록',exact=True).click()
                dialog=page.get_by_role('dialog')
                await dialog.get_by_label('프로젝트 이름').fill('우리 팀 서비스')
                await dialog.get_by_label('사이트 URL').fill('https://example.com')
                await dialog.get_by_label('점검할 고객 여정').fill('첫 가입 완료')
                await dialog.get_by_role('button',name='사이트 등록',exact=True).click()
                await expect(page.get_by_role('heading',name='우리 팀 서비스')).to_be_visible()
                assert len(store.snapshot()['projects'])==1 # registration is browser-local, no hidden API behavior
                await page.reload()
                await expect(page.get_by_role('heading',name='우리 팀 서비스')).to_be_visible()
                await page.get_by_role('button',name='메뉴 열기').click()
                await page.locator('.account-menu > summary').click()
                await page.get_by_role('link',name='워크스페이스 설정').click()
                await page.get_by_label('워크스페이스 이름').fill('DropSpot 팀')
                await page.get_by_label('표시 이름').fill('팀원')
                await page.get_by_role('button',name='변경사항 저장').click()
                await page.reload()
                await expect(page.get_by_label('워크스페이스 이름')).to_have_value('DropSpot 팀')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            finally:
                await browser.close()


async def test_console_rejects_unapproved_targets_and_foreign_origin(tmp_path):
    with console_server(tmp_path) as (base,store):
        async with async_playwright() as pw:
            api=await pw.request.new_context(base_url=base)
            try:
                response=await api.post('/api/runs',data={'variant':'external','case':'all','url':'https://example.com'})
                assert response.status==400
                response=await api.post('/api/runs',headers={'Origin':'https://other.example'},data={'variant':'fixed','case':'all'})
                assert response.status==403
                assert (await api.post('/api/runs',data={'project_id':'unapproved','variant':'fixed','case':'all'})).status==400
                assert [p['id'] for p in store.snapshot()['projects']]==['banchan']
                assert not store.snapshot()['runs']
                assert (await api.get('/api/artifacts/not-a-file')).status==404
            finally:
                await api.dispose()


async def test_console_fifty_agent_replay_is_separate_from_real_runs(tmp_path):
    with console_server(tmp_path) as (base,store):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width':1536,'height':1000})
                await page.goto(base+'/#monitor')
                await expect(page.locator('.agent-tile')).to_have_count(50)
                await expect(page.get_by_text('데모 재생 · 실제 실행 아님',exact=True)).to_be_visible()
                await page.get_by_role('button',name='데모 재생 일시정지').click()
                timestamp=await page.locator('#replay-time').inner_text()
                await page.wait_for_timeout(1100)
                assert await page.locator('#replay-time').inner_text()==timestamp
                await page.locator('[data-agent="1"]').click()
                await expect(page.locator('.inspector-person strong')).to_have_text('정훈')
                await page.get_by_role('button',name='진행 중',exact=True).click()
                tiles=page.locator('.agent-tile')
                assert 0<await tiles.count()<50
                assert await page.locator('.agent-tile:not(.running)').count()==0
                await page.get_by_role('button',name='전체50',exact=True).click()
                await expect(page.locator('.agent-tile')).to_have_count(50)
                assert store.snapshot()['runs']==[] # replay did not start browsers or invent actual records
                await page.get_by_role('button',name='데모 처음부터').click()
                await expect(page.locator('.agent-tile')).to_have_count(50)
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            finally:
                await browser.close()
