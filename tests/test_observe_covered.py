"""모달에 가려진 요소는 관찰 목록에 넣지 않는다 (누를 수 없는 요소를 모델이 고르면 클릭이 시간 초과로 끝난다)."""
from playwright.async_api import async_playwright

from agent.browser import observe

PAGE = """<!doctype html><body style="margin:0">
<nav><a href="/study-room" style="display:block;width:120px;height:40px">공부방</a></nav>
<input required placeholder="뒤쪽 입력칸" style="display:block;margin-top:20px">
<div style="position:fixed;inset:0;background:rgba(0,0,0,.4)">
  <div role="dialog" style="background:#fff;margin:200px auto;width:240px;padding:20px">
    <p>환영해요</p><button>나중에</button><button>다음</button></div></div></body>"""


async def test_elements_behind_a_modal_are_not_offered():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 390, "height": 844})
            await page.set_content(PAGE)
            observation = await observe(page)
        finally:
            await browser.close()
    texts = [e["text"].strip() for e in observation["elements"]]
    assert texts == ["나중에", "다음"]                 # 뒤의 메뉴·입력칸은 빠지고 모달 버튼만
    assert observation["required_fields"] == 0         # 가려진 필수 입력칸은 입력 부담으로 세지 않는다
    assert "공부방" in observation["text"]              # 화면 글자로는 여전히 보인다
