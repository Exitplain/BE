"""Thin adapter over Skyvern's API and official interactive stream UI.

No browser engine, password handling, or video transport is implemented here.
"""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from uuid import uuid4

SKYVERN_HOME=Path.home()/'.cache/dropspot-skyvern-work'


class SkyvernUnavailable(RuntimeError):
    pass


class SkyvernBridge:
    def __init__(self,artifacts,home=None):
        self.artifacts=Path(artifacts).resolve()
        self.home=Path(home or SKYVERN_HOME)
        self.lock=RLock()
        self.connection=None
        self.state_file=self.artifacts/'skyvern'/'demo-connection.json'
        if self.state_file.is_file():
            try:self.connection=json.loads(self.state_file.read_text())
            except (OSError,ValueError):pass

    def _key(self):
        path=self.home/'.env'
        if not path.is_file():raise SkyvernUnavailable('Skyvern이 준비되지 않았습니다. 로컬 Skyvern 서비스를 시작해주세요.')
        # Read only the local organization credential; never return it to the web app.
        for line in path.read_text().splitlines():
            if line.startswith('SKYVERN_API_KEY='):
                return line.split('=',1)[1].strip().strip('"\'')
        raise SkyvernUnavailable('Skyvern 로컬 인증 설정을 확인해주세요.')

    def _api(self,path,body=None):
        try:
            key=self._key()
            request=Request('http://127.0.0.1:8000'+path,headers={'x-api-key':key,'Content-Type':'application/json'},
                data=json.dumps(body).encode() if body is not None else None)
            with urlopen(request,timeout=30) as response:return json.load(response)
        except SkyvernUnavailable:raise
        except (HTTPError,URLError,TimeoutError,OSError) as exc:
            raise SkyvernUnavailable(f'Skyvern 연결을 확인해주세요 ({type(exc).__name__}).') from None

    def _save(self):
        self.state_file.parent.mkdir(parents=True,exist_ok=True)
        self.state_file.write_text(json.dumps(self.connection,ensure_ascii=False,indent=2))
        self.state_file.chmod(0o600)

    def start(self):
        with self.lock:
            if self.connection and self.connection.get('status') in {'connected','captured'}:
                try:
                    metadata=self._api('/v1/browser_sessions/'+self.connection['session_id'])
                    if metadata.get('completed_at') is None:return self.public()
                except SkyvernUnavailable:pass
            result=self._api('/v1/browser_sessions',{'url':'http://127.0.0.1:4173/','timeout':30,'needs_live_view':True,'generate_browser_profile':False})
            sid=result['browser_session_id']
            if not sid.startswith('pbs_') or not sid[4:].isdigit():raise SkyvernUnavailable('Skyvern 세션 응답을 확인해주세요.')
            self.connection={'status':'connected','session_id':sid,'created_at':datetime.now(timezone.utc).isoformat(),
                'checkpoint':None,'iframe_url':'http://localhost:8080/browser-session/'+sid}
            self._save()
            return self.public()

    def public(self):
        with self.lock:
            if not self.connection:return {'status':'disconnected','provider':'skyvern','checkpoint':None}
            return json.loads(json.dumps({**self.connection,'provider':'skyvern'}))

    def close(self):
        with self.lock:
            if self.connection:
                self._api('/v1/browser_sessions/'+self.connection['session_id']+'/close',{})
                self.connection['status']='disconnected'
                self._save()
            return self.public()

    def checkpoint(self):
        with self.lock:
            if not self.connection or self.connection.get('status') not in {'connected','captured'}:
                raise ValueError('로그인용 브라우저를 먼저 연결해주세요.')
            metadata=self._api('/v1/browser_sessions/'+self.connection['session_id'])
            address=metadata.get('browser_address')
            # A CDP address is consumed server-side, never exposed by this adapter.
            parsed=urlsplit(address or '')
            if parsed.scheme not in {'ws','http'} or parsed.hostname not in {'127.0.0.1','localhost'}:
                raise ValueError('라이브 화면이 연결된 뒤 다시 시도해주세요.')
            result=asyncio.run(self._capture(address))
            self.connection.update(status='captured',checkpoint=result)
            self._save()
            return self.public()

    async def _capture(self,address):
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser=await pw.chromium.connect_over_cdp(address)
            pages=[p for context in browser.contexts for p in context.pages if urlsplit(p.url).hostname in {'127.0.0.1','localhost'} and urlsplit(p.url).port==4173]
            if not pages:raise ValueError('로컬 반찬로드 화면으로 돌아온 뒤 시작 화면을 지정해주세요.')
            page=pages[-1]
            if urlsplit(page.url).path.rstrip('/') in {'/login','/register'} or await page.locator('input[type=password]:visible').count():
                raise ValueError('로그인을 마치고 신규 사용자에게 보일 첫 화면을 열어주세요.')
            checkpoint_id=uuid4().hex
            folder=self.artifacts/'skyvern'/'checkpoints'/checkpoint_id
            folder.mkdir(parents=True,exist_ok=False)
            image=folder/'start.png'
            await page.screenshot(path=str(image))
            controls=[]
            for role in ['button','link']:
                for control in (await page.get_by_role(role).all())[:100]:
                    if await control.is_visible():
                        label=(await control.inner_text()).strip()
                        if label:controls.append({'role':role,'label':label[:120]})
            result={'id':checkpoint_id,'artifact':str(image),'title':await page.title(),
                'url':page.url.split('?')[0].split('#')[0],'controls':controls,
                'created_at':datetime.now(timezone.utc).isoformat(),
                'verification':'user_selected_start_screen','scope':'현재 화면과 보이는 기능 확인',
                'note':'계정 상태는 사용자가 지정했습니다. 기능 클릭·자료 생성·삭제·AI 호출은 수행하지 않았습니다.'}
            (folder/'checkpoint.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            # Exiting the CDP client disconnects our observer; it does not close the provider session.
            return result
