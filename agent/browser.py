from urllib.parse import urlsplit

OBSERVE = """() => {
 const visible = e => { const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
   return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth && s.visibility!=='hidden' && s.display!=='none'; };
 document.querySelectorAll('[data-dropspot-id]').forEach(e=>e.removeAttribute('data-dropspot-id'));
 const rect=e=>{const r=e.getBoundingClientRect();const x=Math.max(0,r.left),y=Math.max(0,r.top);return {x,y,width:Math.max(0,Math.min(innerWidth,r.right)-x),height:Math.max(0,Math.min(innerHeight,r.bottom)-y)};};
 // 모달·오버레이에 가려진 요소는 보이기만 하고 누를 수 없다. 화면에 보이는 부분의 가운데에서 맨 위에 있는 요소가 자신(또는 자신의 안/밖)일 때만 넣는다
 const reachable = e => { const b=rect(e); const top=document.elementFromPoint(b.x+b.width/2, b.y+b.height/2); return !!top && (top===e || e.contains(top) || top.contains(e)); };
 const els=Array.from(document.querySelectorAll('a,button,input,select,textarea,[role=button],[contenteditable=true]')).filter(e=>visible(e) && reachable(e));
 const elements=els.slice(0,80).map((e,i)=>{const id='e'+i;e.setAttribute('data-dropspot-id',id);
   return {id,box:rect(e),label:(e.labels?.[0]?.innerText||e.getAttribute('aria-label')||'').trim().slice(0,200),tag:e.tagName.toLowerCase(),text:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('placeholder')||'').slice(0,200),
    href:e.tagName==='A'?e.href:null, options:e.tagName==='SELECT'?Array.from(e.options).slice(0,30).map(o=>({value:o.value,label:o.label})):null, checked:!!e.checked, disabled:!!e.disabled, editable:e.isContentEditable, type:e.type||'',required:!!e.required,value:e.type==='password'?'[redacted]':(e.value||'').slice(0,100)};});
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;const lines=[];
 while(n=walker.nextNode()){if(n.textContent.trim() && !['SCRIPT','STYLE'].includes(n.parentElement.tagName) && visible(n.parentElement)) lines.push(n.textContent.trim());}
 const warnings=Array.from(document.querySelectorAll('p,div,span')).filter(e=>visible(e)&&e.innerText.trim()==='배송비 3,500원이 추가됩니다.').map(e=>rect(e)).sort((a,b)=>a.width*a.height-b.width*b.height).slice(0,1);
 return {viewport:{width:innerWidth,height:innerHeight},cost_warning_boxes:warnings,url:location.href,title:document.title,text:lines.join(' ').slice(0,6000),elements,
 required_fields:els.filter(e=>e.required).length};
}"""


_USERNAME_FIELDS = ", ".join(f"{s}:visible" for s in (
    "input[type=email]", "input[autocomplete=username]", "input[autocomplete=email]", "input[type=text]", "input:not([type])"))
_SUBMIT = "form button[type=submit]:visible, form input[type=submit]:visible"


async def login(page, login_url, username, password, timeout=15000):
    """테스트 계정으로 로그인한다. 성공하면 True.

    모델(정책)이 아니라 이 코드가 직접 입력한다. 아이디·비밀번호는 화면 입력칸에만 넣고
    이벤트·스크린샷·로그·예외 메시지 어디에도 남기지 않는다. 화면에서 비밀번호 칸이 사라지거나
    로그인 화면을 벗어나면 성공으로 본다.
    """
    login_path = urlsplit(login_url).path.rstrip("/") or "/"
    await page.goto(login_url, wait_until="domcontentloaded", timeout=timeout)
    secret = page.locator("input[type=password]:visible").first
    await secret.wait_for(state="visible", timeout=timeout)
    await page.locator(_USERNAME_FIELDS).first.fill(username, timeout=timeout)
    await secret.fill(password, timeout=timeout)
    submit = page.locator(_SUBMIT).first
    if await submit.count():
        await submit.click(timeout=timeout)
    else:
        await secret.press("Enter")
    for _ in range(timeout // 250):
        left = (urlsplit(page.url).path.rstrip("/") or "/") != login_path
        if left or not await page.locator("input[type=password]:visible").count():
            return True
        await page.wait_for_timeout(250)
    return False


def origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


async def observe(page):
    result = await page.evaluate(OBSERVE)
    result["screenshot"] = await page.screenshot(scale="css")
    return result


async def act(page, action, observation):
    """Execute one bounded action. Target IDs belong to the current observation only."""
    if action.kind == "stop":
        raise ValueError("stop is a runtime decision, not a browser operation")
    if action.kind == "wait":
        try:
            milliseconds = int(action.value or "500")
        except ValueError:
            raise ValueError("wait value must be milliseconds") from None
        if not 100 <= milliseconds <= 2000:
            raise ValueError("wait must be between 100 and 2000 ms")
        await page.wait_for_timeout(milliseconds)
        return
    if action.kind in {"back", "forward", "reload"}:
        operation = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action.kind]
        await operation(wait_until="domcontentloaded", timeout=10000)
        return
    if action.kind == "scroll":
        direction = action.value or "down"
        if direction not in {"up", "down"}:
            raise ValueError("scroll value must be up or down")
        await page.mouse.wheel(0, -550 if direction == "up" else 550)
        await page.wait_for_timeout(200)
        return
    valid = {e["id"]: e for e in observation["elements"]}
    locator = None
    if action.target_id is not None:
        if action.target_id not in valid:
            raise ValueError("Action references a target outside the current observation")
        el = valid[action.target_id]
        if el["type"] in {"password", "file"}:
            raise ValueError("Password and file inputs are outside the initial test scope")
        if el.get("disabled"):
            raise ValueError("Target is disabled")
        locator = page.locator(f'[data-dropspot-id="{action.target_id}"]')
        if not await locator.is_visible():
            raise ValueError("Target is no longer visible")
    elif action.kind != "press":
        raise ValueError("This action requires a visible target ID")
    if action.kind == "press":
        allowed = {"Enter", "Tab", "Escape", "Space", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End", "PageDown", "PageUp"}
        if action.value not in allowed:
            raise ValueError("Unsupported key")
        if locator is not None:
            await locator.press(action.value, timeout=5000)
        else:
            await page.keyboard.press(action.value)
    elif action.kind == "fill":
        if el["tag"] not in {"input", "textarea"} and not el.get("editable"):
            raise ValueError("fill target must be editable")
        await locator.fill(action.value or "", timeout=5000)
    elif action.kind == "select":
        if el["tag"] != "select" or action.value not in {o["value"] for o in el.get("options", [])}:
            raise ValueError("select requires a value from the observed options")
        await locator.select_option(value=action.value, timeout=5000)
    elif action.kind in {"check", "uncheck"}:
        if el["type"] not in {"checkbox", "radio"} or (el["type"] == "radio" and action.kind == "uncheck"):
            raise ValueError("check/uncheck requires a checkbox, or check on a radio")
        await locator.set_checked(action.kind == "check", timeout=5000)
    elif action.kind == "hover":
        await locator.hover(timeout=5000)
    elif action.kind == "click":
        await locator.click(timeout=5000)
    else:
        raise ValueError("Unsupported browser action")
    await page.wait_for_timeout(150)
