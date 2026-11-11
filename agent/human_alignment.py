"""Matched, manually reported human observations; never synthetic human labels."""
import re
from datetime import datetime,timezone

STAGES={'landing','product','cart','signup','checkout','complete','delivery','benefits','coupon','account','subscription'}
CAUSES={'input_burden','navigation','cost','value','trust','technical','accessibility','unknown','none'}


def validate_observation(body,job,sessions):
    if not isinstance(body,dict) or set(body)!={'run_id','persona_id','participant_id','outcome','stage','cause','source'}:
        raise ValueError('입력 항목을 확인해주세요.')
    if body['run_id']!=job:raise ValueError('실행이 변경되었습니다. 현재 실행에서 다시 입력해주세요.')
    if body['persona_id'] not in {s['id'] for s in sessions}:raise ValueError('비교할 페르소나를 선택해주세요.')
    if not isinstance(body['participant_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',body['participant_id']):
        raise ValueError('참여자 익명 ID는 영문·숫자·밑줄·하이픈 1~40자로 입력해주세요.')
    if body['outcome'] not in {'success','dropout'} or body['stage'] not in STAGES or body['cause'] not in CAUSES:
        raise ValueError('관측 결과와 단계를 확인해주세요.')
    if body['outcome']=='success' and (body['stage']!='complete' or body['cause']!='none'):
        raise ValueError('완료한 과업은 과업 완료 단계·중단 원인 없음으로 입력해주세요.')
    if body['outcome']=='dropout' and (body['stage']=='complete' or body['cause']=='none'):
        raise ValueError('중단한 과업의 단계와 원인을 선택해주세요. 원인을 모르면 미상으로 입력하세요.')
    if not isinstance(body['source'],str) or not 3<=len(body['source'].strip())<=300:
        raise ValueError('실제 관측 기록의 출처를 3~300자로 입력해주세요.')
    session=next(s for s in sessions if s['id']==body['persona_id'])
    return {**body,'task_id':session.get('task_id','order'),'source':body['source'].strip(),'recorded_at':datetime.now(timezone.utc).isoformat(),
            'provenance':'manual_human_observation','independently_verified':False}


def summarize_alignment(sessions,observations):
    metrics={key:{'matched':0,'total':0,'rate':None} for key in ['outcome','stage','cause']}
    eligible={s['id']:s for s in sessions if s['status'] in {'success','simulated_dropout'}}
    excluded=0
    for row in observations:
        agent=eligible.get(row['persona_id'])
        if agent is None:
            excluded+=1;continue
        outcome='success' if agent['status']=='success' else 'dropout'
        stage='complete' if outcome=='success' else agent.get('terminal_stage')
        cause=(agent.get('issue') or {}).get('category','none')
        metrics['outcome']['total']+=1;metrics['outcome']['matched']+=int(outcome==row['outcome'])
        if row['outcome']=='dropout':
            metrics['stage']['total']+=1;metrics['stage']['matched']+=int(stage==row['stage'])
            if row['cause']!='unknown':
                metrics['cause']['total']+=1;metrics['cause']['matched']+=int(cause==row['cause'])
    for item in metrics.values():
        if item['total']:item['rate']=round(item['matched']/item['total'],4)
    return {'status':'unverified' if not observations else 'manually_reported',
            'participants':len({r['participant_id'] for r in observations}),
            'observations':len(observations),'excluded':excluded,'metrics':metrics,
            'note':'같은 실행 조건에 대응시킨 수동 관측입니다. 기록의 진위는 독립 검증하지 않았으며 전체 고객으로 일반화할 수 없습니다.'}
