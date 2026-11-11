import pytest
from playwright.async_api import async_playwright
from agent.browser import observe, act
from agent.schemas import Action
from agent.demo import demo_server


async def test_extended_form_actions():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content('''<style>#tip{display:none}button:hover+#tip{display:block}</style>
                <label>Message<input id="message"></label>
                <label>Delivery<select id="delivery"><option value="tue">Tuesday</option><option value="fri">Friday</option></select></label>
                <label>Agree<input type="checkbox" id="terms"></label>
                <button>More</button><span id="tip">Details</span>''')
            async def execute(kind, tag=None, value=None):
                obs = await observe(page)
                target = next((e["id"] for e in obs["elements"] if (e["tag"] == tag if tag != "checkbox" else e["type"] == tag)), None) if tag else None
                await act(page, Action(kind=kind, target_id=target, value=value, explanation="test"), obs)
            await execute("fill", "input", "hello")
            assert await page.locator('#message').input_value() == "hello"
            await execute("select", "select", "fri")
            assert await page.locator('#delivery').input_value() == "fri"
            await execute("check", "checkbox")
            assert await page.locator('#terms').is_checked()
            await execute("uncheck", "checkbox")
            assert not await page.locator('#terms').is_checked()
            await execute("press", "input", "End")
            assert await page.locator('#message').evaluate('(e)=>e.selectionStart') == 5
            await execute("hover", "button")
            assert await page.locator('#tip').is_visible()
            await execute("wait", value="100")
            with pytest.raises(ValueError, match="between"):
                await execute("wait", value="100000")
            with pytest.raises(ValueError, match="Unsupported key"):
                await execute("press", value="Control+l")
            with pytest.raises(ValueError, match="observed options"):
                await execute("select", "select", "invalid")
        finally:
            await browser.close()


async def test_navigation_and_bidirectional_scroll():
    with demo_server() as base:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(base+"/normal")
                await page.goto(base+"/fixed")
                async def execute(kind, value=None):
                    await act(page, Action(kind=kind, target_id=None, value=value, explanation="test"), await observe(page))
                await execute("back")
                assert page.url.endswith('/normal')
                await execute("forward")
                assert page.url.endswith('/fixed')
                await execute("reload")
                assert await page.locator('h1').inner_text() == '테스트 주문'
                await page.locator('body').evaluate("e=>e.style.minHeight='3000px'")
                await execute("scroll", "down")
                assert await page.evaluate('scrollY') > 0
                await execute("scroll", "up")
                assert await page.evaluate('scrollY') == 0
            finally:
                await browser.close()
