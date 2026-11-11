"""Real isolated Skyvern sessions for the synthetic shop; no customer-site actions."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit
from uuid import uuid4
from .browser import observe, act
from .shop_tasks import TASKS, DEFAULT_TASK_IDS, ShopTaskPolicy, task_succeeded
from .schemas import Persona
from .shop import shop_server
from .skyvern_bridge import SkyvernBridge
from .frustration import FrustrationMeter, PARAMETERS, STAGES
from .visual_evidence import annotation
from .human_alignment import validate_observation, summarize_alignment

PROFILES = [
    {'id':'quick', 'name':'빠른 구매', 'context':'입력 단계를 최소화하고 바로 주문하려는 방문자', 'max_required_fields':2, 'width':390,'search_sensitivity':1.6,'cost_sensitivity':0.5},
    {'id':'careful', 'name':'꼼꼼한 첫 방문', 'context':'주문 정보와 배송 안내를 확인하는 방문자', 'max_required_fields':4, 'width':1280,'search_sensitivity':1.0,'cost_sensitivity':2.0},
    {'id':'patient', 'name':'여유 있는 탐색', 'context':'입력 과정에 비교적 여유가 있는 방문자', 'max_required_fields':6, 'width':1440,'search_sensitivity':0.6,'cost_sensitivity':0.5},
]

CURSOR = """() => {
 if(document.getElementById('dropspot-live-cursor'))return;
 const c=document.createElement('div');c.id='dropspot-live-cursor';
 c.style.cssText='position:fixed;left:40px;top:40px;width:20px;height:20px;border:3px solid #111;border-radius:50%;background:#ffffffbb;box-shadow:0 0 0 4px #ffffff88;pointer-events:none;z-index:2147483647;transform:translate(-50%,-50%)';
 document.documentElement.append(c);
 document.addEventListener('mousemove',e=>{c.style.left=e.clientX+'px';c.style.top=e.clientY+'px'});
 document.addEventListener('mousedown',()=>c.style.background='#111');
 document.addEventListener('mouseup',()=>c.style.background='#ffffffbb');
}"""



class PersonaDashboard:
    def __init__(self, artifacts):
        self.artifacts=Path(artifacts)
        self.provider=SkyvernBridge(artifacts)
        self.lock=Lock()
        self.state={'status':'idle','id':None,'sessions':[], 'profiles':deepcopy(PROFILES),
                    'policy':'scripted_fixture','model_calls':0,'target':'반찬로드 모의 쇼핑몰',
                    'frustration_model':deepcopy(PARAMETERS),'human_observations':[],'tasks':deepcopy(list(TASKS.values()))}
        summaries=sorted((self.artifacts/'persona-runs').glob('*/summary.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in summaries:
            try:
                saved=json.loads(path.read_text())
                if saved.get('status')=='completed' and saved.get('frustration_model',{}).get('version')==PARAMETERS['version']:
                    self.state=saved;break
            except (ValueError,OSError):continue


    def snapshot(self):
        with self.lock:
            result=deepcopy(self.state)
        result["alignment"]=summarize_alignment(result["sessions"],result["human_observations"])
        return result

    def persist(self,snapshot):
        folder=self.artifacts/'persona-runs'/snapshot['id']
        folder.mkdir(parents=True,exist_ok=True)
        temporary=folder/('summary.'+uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
        temporary.replace(folder/'summary.json')

    def report_history(self):
        saved={}
        for path in (self.artifacts/'persona-runs').glob('*/summary.json'):
            try:
                row=json.loads(path.read_text())
                if row.get('status')=='completed':saved[row['id']]=row
            except (OSError,ValueError,KeyError):continue
        current=self.snapshot()
        if current.get('status')=='completed':saved[current['id']]=current
        return sorted([{'run_id':r['id'],'created_at':r.get('created_at'),'variant':r.get('variant'),
                        'sessions':len(r.get('sessions',[]))} for r in saved.values()],
                      key=lambda r:r['created_at'] or '',reverse=True)

    def report_source(self,run_id=None):
        current=self.snapshot()
        if run_id is None or run_id==current.get('id'):return current
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id):raise ValueError('유효한 실행 ID를 선택해주세요.')
        path=self.artifacts/'persona-runs'/run_id/'summary.json'
        saved=json.loads(path.read_text())
        if saved.get('id')!=run_id:raise ValueError('실행 기록의 ID를 확인해주세요.')
        saved['alignment']=summarize_alignment(saved.get('sessions',[]),saved.get('human_observations',[]))
        return saved

    def update(self, pid, **fields):
        with self.lock:
            record=next(s for s in self.state['sessions'] if s['id']==pid)
            record.update(fields)

    def start(self, variant, task_id="order", task_ids=None, target=None):
        if variant not in {'fixed','problem'}:raise ValueError('지원하지 않는 테스트 버전입니다.')
        if task_id not in {*TASKS,'all'}:raise ValueError('지원하지 않는 과업입니다.')
        if task_ids is not None:
            if not isinstance(task_ids,list) or not task_ids or len(task_ids)>6 or any(not isinstance(t,str) or t not in TASKS for t in task_ids) or len(set(task_ids))!=len(task_ids):raise ValueError('실행할 과업을 확인해주세요.')
            chosen=[TASKS[t] for t in task_ids]
        else:chosen=[TASKS[t] for t in DEFAULT_TASK_IDS] if task_id=='all' else [TASKS[task_id]]
        with self.lock:
            if self.state['status']=='running':raise RuntimeError('이미 페르소나 점검이 실행 중입니다.')
            job=uuid4().hex
            self.state.update(status='running',id=job,variant=variant,tasks=deepcopy(list(TASKS.values())),audience=deepcopy(target or {}),task_selection=task_id,human_observations=[],created_at=datetime.now(timezone.utc).isoformat(),
                sessions=[{**deepcopy(p),'id':p['id'] if task_id=='order' and task_ids is None else p['id']+'_'+task['id'],
                           'context':p['context']+((' · 타겟 '+', '.join(target.get('age_labels',[]))+(' · '+target['context'] if target.get('context') else '')) if target else ''),
                           'profile_id':p['id'],'task_id':task['id'],'task_name':task['name'],'goal':task['goal'],
                           'status':'queued','steps':0,'events':[],'artifact':None,'viewer_url':None,
                           'reason':'브라우저 준비 대기','issue':None,'trace':[],'terminal_stage':None} for task in chosen for p in PROFILES])
        Thread(target=self.worker,args=(job,variant),daemon=True).start()
        return self.snapshot()

    def record_human(self,body):
        with self.lock:
            if self.state['status']!='completed' or not self.state['id']:
                raise ValueError('페르소나 실행을 마친 뒤 실제 사람의 관측을 등록해주세요.')
            row=validate_observation(body,self.state['id'],self.state['sessions'])
            existing=next((r for r in self.state['human_observations'] if r['participant_id']==row['participant_id'] and r.get('task_id','order')==row['task_id']),None)
            if existing and existing['persona_id']!=row['persona_id']:
                raise ValueError('같은 참여자는 과업당 한 페르소나에만 대응할 수 있습니다.')
            if existing:self.state['human_observations'].remove(existing)
            self.state['human_observations'].append(row)
            snapshot=deepcopy(self.state)
            self.persist(snapshot)
        return self.snapshot()

    def record_answer(self,run_id,session_id,answer):
        with self.lock:
            if self.state.get('id')!=run_id:raise ValueError('실행이 바뀌었습니다. 현재 세션에서 다시 질문해주세요.')
            session=next((s for s in self.state['sessions'] if s['id']==session_id),None)
            if session is None:raise ValueError('세션을 찾을 수 없습니다.')
            session.setdefault('answers',[]).append(deepcopy(answer))
            session['answers']=session['answers'][-20:]
            if self.state['status']=='completed':self.persist(self.state)
        return deepcopy(answer)

    def worker(self,job,variant):
        folder=self.artifacts/'persona-runs'/job;folder.mkdir(parents=True)
        try:
            with shop_server() as base:
                asyncio.run(self.run_all(base,variant,folder))
        except Exception as exc:
            for session in self.snapshot()['sessions']:
                if session['status'] in {'queued','running'}:
                    self.update(session['id'],status='agent_failure',reason=f'실행 환경 오류 ({type(exc).__name__})')
        finally:
            with self.lock:
                self.state['status']='completed'
                self.persist(self.state)

    async def run_all(self,base,variant,folder):
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            # Each provider session creates its own isolated browser context.
            semaphore=asyncio.Semaphore(3)
            async def limited(session):
                async with semaphore:await self.run_one(pw,base,variant,folder,session)
            await asyncio.gather(*(limited(s) for s in self.snapshot()['sessions']))

    async def run_one(self,pw,base,variant,folder,profile):
        pid=profile['id'];task=TASKS[profile['task_id']];sid=None;client=None;page=None;events=[];trace=[];meter=FrustrationMeter(profile)
        self.update(pid,status='running',reason='실제 브라우저를 열고 있습니다.')
        async def evidence(step):
            image=folder/f'{pid}_{step:03d}.png'
            await page.screenshot(path=str(image))
            self.update(pid,artifact=str(image),steps=step,url=page.url)
        try:
            async with asyncio.timeout(150):
                response=await asyncio.to_thread(self.provider._api,'/v1/browser_sessions',
                    {'url':base+f"/?variant={variant}&case={task['case']}",'timeout':5,'needs_live_view':True,'generate_browser_profile':False})
                sid=response['browser_session_id']
                if not sid.startswith('pbs_') or not sid[4:].isdigit():raise ValueError('Invalid session ID')
                self.update(pid,viewer_url='http://localhost:8080/browser-session/'+sid)
                address=None
                for _ in range(40):
                    meta=await asyncio.to_thread(self.provider._api,'/v1/browser_sessions/'+sid)
                    address=meta.get('browser_address')
                    if address:break
                    await asyncio.sleep(.5)
                parsed=urlsplit(address or '')
                if parsed.scheme not in {'ws','http'} or parsed.hostname not in {'127.0.0.1','localhost'}:
                    raise ValueError('Invalid local CDP')
                client=await pw.chromium.connect_over_cdp(address)
                page=next(pg for c in client.contexts for pg in c.pages if pg.url.startswith(base))
                async def restrict(route):
                    url=urlsplit(route.request.url)
                    if f'{url.scheme}://{url.netloc}'==base and route.request.method in {'GET','HEAD'}:await route.continue_()
                    else:await route.abort()
                await page.context.route('**/*',restrict)
                await page.set_viewport_size({'width':profile['width'],'height':900})
                await page.evaluate(CURSOR)
                policy=ShopTaskPolicy(task['id'])
                persona=Persona(id=pid,context=profile['context'],max_required_fields=profile['max_required_fields'])
                await evidence(0)
                for step in range(1,36):
                    observation=await observe(page)
                    stage=STAGES.get(urlsplit(page.url).path,'other')
                    point=meter.observe(stage,observation['required_fields'],
                        search_scroll=bool(events and events[-1]['action']=='scroll' and stage==meter.previous_stage),
                        unexpected_cost='배송비 3,500원이 추가됩니다.' in observation['text'])
                    trace_image=folder/f'trace_{pid}_{step:03d}.png'
                    trace_image.write_bytes(observation['screenshot'])
                    point.update(step=step,artifact=str(trace_image),url=page.url,
                        visual_evidence={kind:annotation(observation,kind) for kind in ['input_burden','navigation','cost']})
                    trace.append(point)
                    self.update(pid,trace=deepcopy(trace),terminal_stage=stage)
                    if task_succeeded(task,urlsplit(page.url).path,observation['text']):
                        self.update(pid,status='success',reason=task['name']+' 과업 완료 화면에 도달했습니다.');break
                    if point['crossed']:
                        contributions={'input_burden':point['terms']['input'],'navigation':point['terms']['search']+point['terms']['revisit'],'cost':point['terms']['cost']}
                        category=max(contributions,key=contributions.get)
                        self.update(pid,status='simulated_dropout',
                            reason=f"좌절 점수 {point['score']:g}이 초기 가설 임계치 {point['threshold']:g}을 초과했습니다.",
                            issue={'category':category,'observed_fields':point['required_fields'],
                                   'configured_limit':profile['max_required_fields'],'model_version':PARAMETERS['version']})
                        break
                    action,_=await policy.decide(observation,persona,task['goal'],events)
                    if action.target_id:
                        box=await page.locator(f'[data-dropspot-id="{action.target_id}"]').bounding_box()
                        if box:await page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2,steps=30)
                    self.update(pid,reason=action.explanation)
                    await asyncio.sleep(1)
                    await act(page,action,observation)
                    events.append({'step':step,'action':action.kind,'description':action.explanation})
                    self.update(pid,events=deepcopy(events))
                    await evidence(step)
                    await asyncio.sleep(.5)
                else:self.update(pid,status='budget_exceeded',reason='행동 횟수 한도에 도달했습니다.')
                await evidence(len(events))
        except TimeoutError:self.update(pid,status='budget_exceeded',reason='실행 시간 한도에 도달했습니다.')
        except Exception as exc:self.update(pid,status='agent_failure',reason=f'브라우저 실행 오류 ({type(exc).__name__})')
        finally:
            # Provider closes only the newly created shop session, never another existing session.
            if sid:
                try:await asyncio.to_thread(self.provider._api,'/v1/browser_sessions/'+sid+'/close',{})
                except Exception:self.update(pid,cleanup_warning='세션 종료를 확인하지 못했습니다.')
            self.update(pid,viewer_url=None)
