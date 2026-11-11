"""Read-only local-site discovery and explicit task/audience confirmation."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
from threading import Lock
import time
from urllib.parse import urlsplit
from uuid import uuid4
from .shop import shop_server
from .shop_tasks import TASKS,DEFAULT_TASK_IDS

FEATURES={
 'shopping':{'name':'상품 탐색·장바구니·주문','path':'/product','hint':'이번 주 식탁'},
 'delivery':{'name':'배송 가능 지역 조회','path':'/delivery','hint':'배송 가능 지역 확인'},
 'coupon':{'name':'첫 주문 쿠폰','path':'/benefits','hint':'첫 주문 혜택'},
 'subscription':{'name':'구독 배송 일정 관리','path':'/account','hint':'내 구독 관리'},
}
TASK_FEATURE={'order':'shopping','cart':'shopping','delivery':'delivery','coupon':'coupon','benefit':'coupon','subscription':'subscription'}
AGE_LABELS={'any':'전체 연령','teens':'10대','20s':'20대','30s':'30대','40s':'40대','50s':'50대','60plus':'60대 이상'}


async def discover_site(variant):
    from playwright.async_api import async_playwright
    with shop_server() as base:
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            try:
                page=await browser.new_page()
                async def readonly(route):
                    url=urlsplit(route.request.url)
                    if f'{url.scheme}://{url.netloc}'==base and route.request.method in {'GET','HEAD'}:await route.continue_()
                    else:await route.abort()
                await page.route('**/*',readonly)
                await page.goto(base+f'/?variant={variant}&case=all',wait_until='networkidle')
                links=await page.get_by_role('link').evaluate_all('(els)=>els.map(e=>({label:e.innerText.trim(),path:new URL(e.href).pathname}))')
                features=[]
                for fid,meta in FEATURES.items():
                    match=next((l for l in links if l['path']==meta['path'] and meta['hint'] in l['label']),None)
                    if match:features.append({'id':fid,'name':meta['name'],'evidence':match,'verification':'visible_entry_only'})
                ids={f['id'] for f in features}
                tasks=[{**deepcopy(t),'feature_id':TASK_FEATURE[t['id']],'suggested':t['id'] in DEFAULT_TASK_IDS}
                       for t in TASKS.values() if TASK_FEATURE[t['id']] in ids]
                return {'project':'반찬로드','features':features,'tasks':tasks,'age_options':AGE_LABELS,
                        'note':'공개 시작 화면의 링크를 읽었습니다. 기능의 정상 작동을 판정한 것은 아닙니다.'}
            finally:await browser.close()


class PreflightPlanner:
    def __init__(self):self.lock=Lock();self.plans={}

    def discover(self,variant):
        if variant not in {'fixed','problem'}:raise ValueError('지원하는 반찬 데모 버전을 선택해주세요.')
        result=asyncio.run(discover_site(variant));pid=uuid4().hex
        with self.lock:
            now=time.monotonic();self.plans={k:v for k,v in self.plans.items() if now-v['at']<900}
            self.plans[pid]={'at':now,'variant':variant,'result':deepcopy(result)}
        return {**result,'plan_id':pid,'variant':variant}

    def confirm(self,body):
        fields={'plan_id','feature_ids','task_ids','ages','context','additional_request'}
        if not isinstance(body,dict) or set(body)!=fields:raise ValueError('확인한 기능·과업·타겟을 전달해주세요.')
        with self.lock:plan=deepcopy(self.plans.get(body['plan_id'])) if isinstance(body['plan_id'],str) else None
        if not plan or time.monotonic()-plan['at']>=900:raise ValueError('탐색 결과가 만료됐습니다. 다시 확인해주세요.')
        features=body['feature_ids'];tasks=body['task_ids'];ages=body['ages']
        for values,allowed in [(features,{f['id'] for f in plan['result']['features']}),(tasks,{t['id'] for t in plan['result']['tasks']}),(ages,set(AGE_LABELS))]:
            if not isinstance(values,list) or not values or len(values)>len(allowed) or any(not isinstance(v,str) or v not in allowed for v in values) or len(set(values))!=len(values):raise ValueError('기능·과업·연령을 한 개 이상 선택해주세요.')
        if any(TASK_FEATURE[t] not in features for t in tasks):raise ValueError('확인한 기능에 해당하는 과업만 실행할 수 있습니다.')
        if 'any' in ages and len(ages)>1:raise ValueError('전체 연령과 특정 연령을 함께 선택할 수 없습니다.')
        if any(not isinstance(body[k],str) or len(body[k])>500 for k in ['context','additional_request']):raise ValueError('추가 설명은 500자 이하로 입력해주세요.')
        audience={'ages':ages,'age_labels':[AGE_LABELS[a] for a in ages],'context':body['context'],
                  'additional_request':body['additional_request'],'confirmed_features':features,'plan_id':body['plan_id'],
                  'confirmed_at':datetime.now(timezone.utc).isoformat(),'age_behavior_calibration':'unverified'}
        return plan['variant'],tasks,audience
