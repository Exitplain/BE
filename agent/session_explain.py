"""Grounded explanations of recorded observations; no invented inner monologue."""
from datetime import datetime,timezone
from uuid import uuid4
from .persona_report import recommendation,STAGES


def explain_session(state,session_id,question,step=None):
    if not isinstance(question,str) or not 1<=len(question.strip())<=500:raise ValueError('질문은 1~500자로 입력해주세요.')
    session=next((s for s in state.get('sessions',[]) if s['id']==session_id),None)
    if session is None:raise ValueError('현재 실행의 세션을 선택해주세요.')
    if step is not None and (type(step) is not int or step<1):raise ValueError('행동 번호를 확인해주세요.')
    question=question.strip();trace=session.get('trace',[]);events=session.get('events',[])
    point=next((p for p in trace if p.get('crossed')),trace[-1] if trace else {})
    sources=[];known=True;kind='observations'
    if any(k in question for k in ['실제 고객','구매 의사','감정','속마음','전환율']):
        text='이 실행 기록만으로 실제 고객의 감정·구매 의사·전환율을 알 수 없습니다. 실제 사람의 과업 관측과 별도로 비교해야 합니다.';known=False;kind='not_observed'
    elif any(k in question for k in ['고치','개선','줄이','수정']):
        issue=session.get('issue')
        if issue:
            text=recommendation(issue.get('category'),session.get('terminal_stage'))+' 이것은 기록에 근거한 개선 가설이며 효과는 아직 검증하지 않았습니다.';kind='improvement_hypothesis'
        else:text='이 세션에는 중단 원인이 기록되지 않아 구체적인 개선 효과를 단정할 수 없습니다.';known=False
    elif step is not None or (any(k in question for k in ['버튼','클릭','입력','스크롤','행동','선택']) and not any(k in question for k in ['멈','중단','이탈','포기'])):
        action=next((e for e in events if e.get('step')==step),None) if step else (events[-1] if events else None)
        if action:
            step=action['step'];point=next((p for p in trace if p.get('step')==step),{})
            text=f"행동 {step}의 실행 설명은 ‘{action.get('description',action.get('action',''))}’입니다. 세션 과업 ‘{session.get('goal',session.get('task_name','과업'))}’을 진행하는 고정 행동 규칙에 따라 실행했습니다. 기록된 설명 이상의 심리적 이유는 확인하지 않았습니다.";kind='recorded_action'
        else:text='질문한 행동의 실행 기록을 찾지 못했습니다.';known=False
    elif any(k in question for k in ['멈','중단','이탈','포기','왜']):
        if point.get('crossed'):
            sig=point.get('signals',{});facts=[]
            if sig.get('new_required_fields'):facts.append(f"새 필수 입력 {sig['new_required_fields']}개")
            if sig.get('search_scroll'):facts.append('다음 동작을 찾는 탐색 스크롤')
            if sig.get('unexpected_cost'):facts.append('뒤늦은 배송비 3,500원 노출')
            if sig.get('revisit'):facts.append('이전 단계 재방문')
            text=f"{STAGES.get(point.get('stage'),point.get('stage','해당 화면'))}에서 {', '.join(facts) or '누적된 마찰 신호'}가 기록됐습니다. 가중치를 적용한 점수 {point['score']:g}이 설정한 임계치 {point['threshold']:g}을 넘어 가상 이탈했습니다. 실제 사람이 같은 이유로 포기한다는 뜻은 아닙니다.";kind='model_stop'
        elif session['status']=='success':text='이 세션은 중단하지 않고 과업 완료 화면까지 진행했습니다.';kind='recorded_outcome'
        elif session['status'] in {'queued','running'}:text='아직 실행 중이거나 대기 중입니다. 중단 원인은 아직 기록되지 않았습니다.';known=False
        else:text=session.get('reason','종료 원인이 기록되지 않았습니다.')+' 이를 사용자 이탈의 증거로 해석할 수 없습니다.';kind='execution_status'
    else:
        text='현재 기록으로 그 질문의 이유를 확인할 수 없습니다. 중단 이유, 특정 행동, 개선 가설에 대해 물어보세요.';known=False;kind='not_observed'
    if point and known:
        sources=[{'step':point.get('step'),'stage':point.get('stage'),'stage_name':STAGES.get(point.get('stage'),point.get('stage')),
                  'artifact_id':point.get('artifact_id')}]
    return {'id':uuid4().hex,'question':question,'answer':text,'kind':kind,'supported_by_record':known,'sources':sources,
            'recorded_at':datetime.now(timezone.utc).isoformat(),'method':'record_based_rules','model_calls':0}
