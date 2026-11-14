"""Evidence-led reports from recorded sessions, with model hypotheses kept explicit."""
from collections import Counter
from pathlib import Path
from .frontend import FRONTEND_ROOT
from hashlib import sha256
from urllib.parse import urlsplit

STAGES={'landing':'랜딩','product':'상품 상세','cart':'장바구니','signup':'회원가입','checkout':'배송 정보',
        'delivery':'배송 지역 조회','benefits':'쿠폰 혜택','coupon':'쿠폰 발급','account':'내 구독',
        'subscription':'배송 일정 변경','complete':'과업 완료'}
CATEGORIES={'input_burden':'필수 입력 부담','navigation':'탐색 부담','cost':'늦은 배송비 노출'}
# runtime(agent/behavior.py)이 새로 내는 원인. 기존 3종의 이름은 이 리포트 화면 테스트가 쓰고 있어 그대로 둔다.
CATEGORIES.update({'value':'가치 불명확','trust':'신뢰 부족','technical':'기술 오류·지연','accessibility':'접근성 장벽'})
TECHNICAL={'agent_failure','site_failure','budget_exceeded','cancelled','blocked_auth'}


def recommendation(category,stage):
    if category=='cost':return '상품 상세와 장바구니에서 배송비를 포함한 총액을 먼저 보여주고 같은 과업으로 재검증하세요.'
    if category=='navigation':return '혜택의 핵심 동작을 첫 화면 가까이에 배치하고, 소개를 끝까지 읽지 않아도 실행할 수 있게 해보세요.'
    if stage=='delivery':return '배송 가능 여부 조회에는 지역만 받고 연락처 등은 주문 단계로 미뤄보세요.'
    if stage=='subscription':return '배송 건너뛰기는 바로 처리하고 변경 사유는 선택 입력으로 분리해보세요.'
    if stage=='signup':return '비회원 첫 주문을 허용하거나 가입 시 필요한 항목을 줄여보세요.'
    return '현재 과업에 꼭 필요한 입력만 남기고 부가 정보는 이후 단계로 미뤄보세요.'


def build_report(state):
    if state.get('status')!='completed':raise ValueError('실행을 완료한 뒤 리포트를 생성할 수 있습니다.')
    sessions=state.get('sessions',[]);groups={}
    for session in sessions:
        issue=session.get('issue')
        if session.get('status')!='simulated_dropout' or not issue:continue
        stage=session.get('terminal_stage','unknown');category=issue.get('category','unknown')
        key=(session.get('task_id','order'),stage,category)
        group=groups.setdefault(key,[]);group.append(session)
    findings=[]
    for (task_id,stage,category),affected in groups.items():
        evidence=[]
        for session in affected:
            points=[p for p in session.get('trace',[]) if p.get('stage')==stage]
            point=next((p for p in points if p.get('crossed')),points[-1] if points else {})
            signals=point.get('signals',{});observations=[]
            if 'required_fields' in point:observations.append(f"현재 화면의 필수 입력 {point['required_fields']}개")
            scrolls=sum(p.get('signals',{}).get('search_scroll',0) for p in session.get('trace',[]) if p.get('stage')==stage)
            if scrolls:observations.append(f'해당 단계에서 탐색 스크롤 {scrolls}회')
            if signals.get('unexpected_cost'):observations.append('배송 단계에서 배송비 3,500원 추가 경고 문구 노출')
            artifact=point.get('artifact_id')
            evidence.append({'session_id':session['id'],'persona_name':session.get('name',session['id']),
                'width':session.get('width'),'step':point.get('step'),'artifact_id':artifact,
                'evidence_status':'available' if artifact else 'missing','path':urlsplit(point.get('url',session.get('url',''))).path,
                'score':point.get('score'),'threshold':point.get('threshold'),'observations':observations,
                'annotation':point.get('visual_evidence',{}).get(category,{'viewport':None,'regions':[],'note':'이전 실행에는 요소 좌표가 없습니다. 새 실행부터 표시합니다.'}),
                'steps_before':[(e.get('description') or e.get('action','')) for e in session.get('events',[])
                                if point.get('step') is not None and e.get('step',0)<point['step']][-4:]})
        count=sum(s.get('task_id','order')==task_id for s in sessions)
        findings.append({'id':sha256(f"{state['id']}:{task_id}:{stage}:{category}".encode()).hexdigest()[:16],
            'task_id':task_id,'task_name':affected[0].get('task_name','첫 주문'),'stage':stage,
            'stage_name':STAGES.get(stage,stage),'category':category,'category_name':CATEGORIES.get(category,category),
            'title':f"{STAGES.get(stage,stage)}에서 {CATEGORIES.get(category,category)}",
            'affected_sessions':len(affected),'task_sessions':count,'evidence':evidence,
            'interpretation':'설정한 가중치로 누적한 좌절 점수가 임계치를 넘어 가상 이탈했습니다. 실제 사람의 중단 이유를 확정한 결과는 아닙니다.',
            'recommendation':recommendation(category,stage),'recommendation_status':'hypothesis',
            'mockup':{'image_url':'/assets/mockups/signup-guest-v1.png','label':'AI 생성 개선 목업 · 실제 적용 전',
                      'note':'강제 가입 대신 비회원 주문에 필요한 정보만 받는 디자인 가설입니다. 개선 효과는 미검증입니다.'}
                     if category=='input_burden' and stage=='signup' and (FRONTEND_ROOT/'saas-web/assets/mockups/signup-guest-v1.png').is_file() else None})
    findings.sort(key=lambda f:(-f['affected_sessions'],f['task_id'],f['stage']))
    statuses=Counter(s.get('status') for s in sessions)
    tasks=[]
    for task_id in dict.fromkeys(s.get('task_id','order') for s in sessions):
        group=[s for s in sessions if s.get('task_id','order')==task_id]
        tasks.append({'id':task_id,'name':group[0].get('task_name','첫 주문'),'total':len(group),
            'success':sum(s.get('status')=='success' for s in group),
            'dropout':sum(s.get('status')=='simulated_dropout' for s in group),
            'technical':sum(s.get('status') in TECHNICAL for s in group)})
    return {'schema_version':'1.0','run_id':state['id'],'created_at':state.get('created_at'),
        'target':state.get('target','반찬로드 모의 쇼핑몰'),'variant':state.get('variant'),
        'audience':state.get('audience',{}),'policy':state.get('policy','scripted_fixture'),'model_calls':state.get('model_calls',0),
        'summary':{'sessions':len(sessions),'success':statuses['success'],'simulated_dropout':statuses['simulated_dropout'],
            'technical':sum(statuses[k] for k in TECHNICAL),'findings':len(findings),
            'missing_evidence':sum(e['evidence_status']=='missing' for f in findings for e in f['evidence'])},
        'tasks':tasks,'findings':findings,'technical_failures':[{'session_id':s['id'],'persona_name':s.get('name'),
            'task_name':s.get('task_name'),'status':s['status'],'reason':s.get('reason','')} for s in sessions if s.get('status') in TECHNICAL],
        'alignment':state.get('alignment',{}),'frustration_model':state.get('frustration_model',{}),
        'limitations':['고정 행동 규칙과 보정 전 좌절 가중치의 모의 실행 결과입니다.',
            '실제 고객의 이탈률·감정·구매 의사를 예측한 수치가 아닙니다.',
            '영향받은 수는 과업별 가상 세션 수이며 서로 독립된 실제 사람 수가 아닙니다.',
            '문제가 보고되지 않아도 검사 범위 밖의 UX 문제가 없다는 뜻은 아닙니다.']}


def markdown_report(report,base_url):
    # Escape Markdown metacharacters in labels; evidence URLs are generated internally.
    def safe(value):return str(value or '').replace('\\','\\\\').replace('[','\\[').replace(']','\\]').replace('<','&lt;').replace('>','&gt;').replace('\n',' ')
    s=report['summary']
    lines=['# 반찬로드 진단 리포트','',f"실행: `{report['run_id']}`",'',
        f"총 {s['sessions']}세션 · 목표 도달 {s['success']} · 가상 이탈 {s['simulated_dropout']} · 기술 실패/한도 {s['technical']}",
        f"발견한 문제 위치 {s['findings']}곳 · 문제 시점 화면 누락 {s['missing_evidence']}건",'',
        '## 발견한 문제','']
    audience=report.get('audience',{})
    if audience.get('age_labels'):lines[4:4]=['타겟 연령: '+safe(', '.join(audience['age_labels'])),'']
    if audience.get('additional_request'):lines+=['추가 요청 메모(이번 실행에 자동 포함되지 않음): '+safe(audience['additional_request']),'']
    if not report['findings']:lines+=['현재 검사 규칙에 해당하는 문제를 발견하지 못했습니다. 전체 UX의 정상 판정은 아닙니다.','']
    for i,f in enumerate(report['findings'],1):
        lines += [f"### {i}. {safe(f['title'])}",'',f"과업: {safe(f['task_name'])} · 영향 {f['affected_sessions']}/{f['task_sessions']}세션",'']
        for e in f['evidence']:
            lines += [f"- {safe(e['persona_name'])} · {safe(e['path'])} · 관측 {e['step']} · 점수 {e['score']} / 임계치 {e['threshold']}"]
            lines += ['  - 관측: '+safe(' / '.join(e['observations']) or '관측 상세 없음')]
            if e['steps_before']:lines+=['  - 직전 경로: '+safe(' → '.join(e['steps_before']))]
            lines += [f"  - [문제 시점 화면]({base_url}/api/artifacts/{e['artifact_id']})" if e['artifact_id'] else '  - 문제 시점 화면 없음']
        lines += ['',f"모형 해석: {f['interpretation']}",'',f"개선 가설: {f['recommendation']}",'']
    lines += ['## 기술 실패·한도 초과','']
    lines += [f"- {safe(e['task_name'])} / {safe(e['persona_name'])}: {safe(e['status'])} · {safe(e['reason'])}" for e in report['technical_failures']] or ['해당 없음']
    a=report['alignment'];lines+=['','## Human Alignment','',f"사람 관측 {a.get('participants',0)}명 · {a.get('observations',0)}건 · 상태 {a.get('status','unverified')}",'']
    for key,title in [('outcome','완료·중단'),('stage','중단 단계'),('cause','중단 원인')]:
        m=a.get('metrics',{}).get(key,{})
        lines.append(f"- {title}: {m.get('matched',0)}/{m.get('total',0)}건 일치" if m.get('total') else f'- {title}: 미검증 / 비교 가능한 관측 없음')
    lines+=['','## 해석 범위','']+['- '+v for v in report['limitations']]
    return '\n'.join(lines)+'\n'
