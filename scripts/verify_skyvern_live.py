"""Live Skyvern proof with synthetic local data. No LLM or real login credentials."""
import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.demo import demo_server
from playwright.async_api import async_playwright,expect


def read_key(env_path):
    from dotenv import dotenv_values
    return dotenv_values(env_path).get('SKYVERN_API_KEY')


def api(key,path,body=None):
    req=Request('http://127.0.0.1:8000'+path,headers={'x-api-key':key,'Content-Type':'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    with urlopen(req,timeout=45) as response:return json.load(response)


async def verify(folder,key):
    report={'started_at':datetime.now(timezone.utc).isoformat(),'verdict':'fail','checks':{},'model_calls':0}
    sid=None
    with demo_server() as base:
        created=await asyncio.to_thread(api,key,'/v1/browser_sessions',{'url':base+'/normal','timeout':10,'needs_live_view':True})
        sid=created['browser_session_id']
        try:
            async with async_playwright() as p:
                viewer=await p.chromium.launch()
                page=await viewer.new_page(viewport={'width':1440,'height':1000})
                messages=[]
                def on_ws(ws):
                    ws.on('framereceived',lambda payload:messages.append(len(payload) if isinstance(payload,(str,bytes)) else 0))
                page.on('websocket',on_ws)
                await page.goto('http://127.0.0.1:8080/browser-session/'+sid)
                image=page.locator('img').first
                await expect(image).to_be_visible(timeout=60000)
                await page.wait_for_function('document.querySelector("img")?.naturalWidth>0',timeout=60000)
                metadata=await asyncio.to_thread(api,key,'/v1/browser_sessions/'+sid)
                address=metadata['browser_address']
                attached=await p.chromium.connect_over_cdp(address)
                remote=next(pg for context in attached.contexts for pg in context.pages if pg.url.startswith(base))
                before=await remote.locator('input[name=token]').input_value()
                assert before==''
                await page.get_by_role('button',name='Take control',exact=True).click()
                await page.wait_for_timeout(1000)
                box=await remote.locator('input[name=token]').bounding_box()
                image_box=await image.bounding_box()
                viewport=await remote.evaluate('({width:innerWidth,height:innerHeight})')
                x=image_box['x']+(box['x']+box['width']/2)*image_box['width']/viewport['width']
                y=image_box['y']+(box['y']+box['height']/2)*image_box['height']/viewport['height']
                await page.mouse.click(x,y)
                await page.keyboard.type('live-view-verified',delay=80)
                await expect(remote.locator('input[name=token]')).to_have_value('live-view-verified',timeout=10000)
                report['checks']['web_mouse_and_keyboard']='pass'
                report['checks']['live_frames']='pass' if len(messages)>2 else 'fail'
                await page.screenshot(path=str(folder/'manual-input.png'),full_page=True)
                # Release control before deterministic automation takes over the same CDP session.
                buttons=await page.get_by_role('button').all_text_contents()
                for label in ['Release control','Stop control','Exit control']:
                    if any(label in value for value in buttons):
                        await page.get_by_role('button',name=label).first.click();break
                await remote.get_by_role('button',name='모의 주문').click()
                await expect(remote.locator('#result')).to_have_text('live-view-verified')
                assert 'saved:live-view-verified;cookie:participant=live-view-verified' in await remote.locator('#storage').inner_text()
                report['checks']['same_session_handoff']='pass'
                await page.wait_for_timeout(1200)
                await page.screenshot(path=str(folder/'same-session-result.png'),full_page=True)
                report['session_id']=sid
                report['websocket_message_count']=len(messages)
                await viewer.close()
        finally:
            if sid:
                try:await asyncio.to_thread(api,key,'/v1/browser_sessions/'+sid+'/close',{})
                except Exception:pass
    report['verdict']='pass' if all(v=='pass' for v in report['checks'].values()) else 'fail'
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',type=Path,default=Path('/home/user/.cache/dropspot-skyvern-work/.env'))
    args=parser.parse_args()
    folder=ROOT/'artifacts'/'skyvern'/('verification_'+uuid4().hex[:8]);folder.mkdir(parents=True)
    try:
        key=read_key(args.env_file)
        if not key:raise ValueError('Local Skyvern credential missing')
        result=asyncio.run(verify(folder,key))
    except Exception as exc:
        result={'verdict':'fail','reason':type(exc).__name__,'model_calls':0}
    (folder/'verdict.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'verdict':result['verdict'],'report':str(folder/'verdict.json'),'checks':result.get('checks'), 'reason':result.get('reason')},ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['verdict']=='pass' else 1)


if __name__=='__main__':main()
