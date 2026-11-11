from pathlib import Path
from playwright.async_api import async_playwright
from agent import RunInput, execute_run
from agent.schemas import Persona
from agent.policies import ShopScriptedPolicy
from agent.shop import shop_server


def config(base, variant, case="all"):
    return RunInput(run_id="shop_test", target_url=f"{base}/?variant={variant}&case={case}",
        allowed_origins=[base], goal="반찬 모의 주문", success_path="/order/success", max_steps=35,
        personas=[Persona(id="A"),Persona(id="B")])


async def test_storefront_fixed_flow_and_signup_regression(tmp_path):
    with shop_server() as base:
        fixed = await execute_run(config(base, "fixed"), ShopScriptedPolicy, tmp_path)
        problem = await execute_run(config(base, "problem", "signup"), ShopScriptedPolicy, tmp_path)
    assert all(s["status"] == "success" for s in fixed["sessions"])
    assert all(s["status"] == "simulated_dropout" for s in problem["sessions"])
    assert all(s["issues"][0]["category"] == "input_burden" for s in problem["sessions"])
    for s in fixed["sessions"]+problem["sessions"]:
        assert Path(s["final_artifact"]).is_file()


async def test_storefront_visible_fee_and_cta_conditions():
    with shop_server() as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width":1000,"height":800})
                await page.goto(base+'/product?variant=fixed&case=all')
                assert await page.get_by_text('회당 3,500원').is_visible()
                assert await page.get_by_role('button', name='이 식탁 담기').evaluate('(e)=>e.getBoundingClientRect().top < innerHeight')
                await page.goto(base+'/product?variant=problem&case=all')
                assert await page.get_by_text('회당 3,500원').count() == 0
                assert not await page.get_by_role('button', name='이 식탁 담기').evaluate('(e)=>e.getBoundingClientRect().top < innerHeight')
                await page.get_by_role('button', name='이 식탁 담기').click()
                await page.get_by_role('button', name='주문하기').click()
                assert '/signup' in page.url
                await page.goto(base+'/product?variant=problem&case=fee')
                await page.get_by_role('button', name='이 식탁 담기').click()
                assert await page.get_by_text('배송비', exact=True).count() == 0
                await page.get_by_role('button', name='주문하기').click()
                assert await page.get_by_text('배송비', exact=True).is_visible()
                assert await page.get_by_text('배송비 3,500원이 추가됩니다.').is_visible()
            finally:
                await browser.close()


async def test_storefront_mobile_layout_and_completion_guard():
    with shop_server() as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width":390,"height":844})
                await page.goto(base+'/?variant=fixed')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.goto(base+'/order/success?variant=fixed')
                assert '/order/success' not in page.url
                await page.goto(base+'/product?variant=fixed')
                await page.get_by_role('button',name='이 식탁 담기').click()
                await page.get_by_role('button',name='주문하기').click()
                await page.get_by_label('받는 분').fill('모바일 테스트')
                await page.get_by_label('연락처').fill('01000000000')
                await page.get_by_label('배송 주소').fill('서울 노원구 동일로 1')
                await page.get_by_label('배송 요일').select_option('friday')
                await page.get_by_label('주문 내용 및 배송 안내를 확인했습니다.').check()
                await page.get_by_role('button',name='모의 주문 완료하기').click()
                assert '/order/success' in page.url
                assert await page.get_by_text('모바일 테스트님, 모의 주문이 완료되었습니다.').is_visible()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            finally:
                await browser.close()
