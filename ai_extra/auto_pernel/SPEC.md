퍼널 단계 자동화 (키워드 하드코딩 → 구조 신호 기반 배정, 단계는 Figma의 7개 고정)

목적: **단계는 Figma P12의 7개로 고정**하되(landing·browse·detail·cart·signup·payment·complete), 임의 사이트의 페이지를 그 7단계에 배정하는 방식을 **경로 키워드 사전(develop `agent/funnel.py` KEYWORDS)이 아니라 대상 탐색 결과(코드 구조 신호·경로 순서)로 일반화**한다. 단계 수를 늘리지 않는다. 배정 결과는 develop의 `RunInput.stage_paths`로 전달한다(`ai_extra/integration/`).

(정정) 초안의 "단계 수는 탐색 결과로 결정(가변)"은 목적을 잘못 읽은 것이었다. 탐색 그래프는 7단계 배정의 근거를 만드는 데 쓰고, 단계 목록 자체는 계약(`stage_roles.json` = `FUNNEL_KEYS`)이다.

현재 하드코딩 (자동화 대상)
- agent/frustration.py:2 STAGES — URL 경로 → 단계명 (모의 쇼핑몰 경로 13개, 미등록 경로는 'other')
- agent/persona_report.py:8 STAGES — 단계명 → 한글 라벨 (11개)
- agent/human_alignment.py:5 STAGES — 허용 단계 집합 (11개)
- agent/shop_tasks.py TASKS·DEFAULT_TASK_IDS — 과업 6종과 success_path
- agent/preflight.py FEATURES — 기능 4종과 시작 화면 링크 매칭 (깊이 1, verification=visible_entry_only)
- 문서 계약: 개발_역할분담_및_진행방식.md funnel_steps 6개 / Figma P05c "과업별 7단계" 문구
- 위 3곳의 STAGES 중복은 단일 레지스트리로 먼저 통합한다 (선행 리팩터링).

원칙
- 탐색 결과는 집계·라벨링에만 쓰고 행동 모델(페르소나)에는 주입하지 않는다.
- 단계 ID는 revision 간 유지한다 (R05 단계 매핑과 실측 퍼널 정렬).
- 결과마다 mode(local|url), verification(verified|unverified|blocked), 근거를 붙인다.

Subagent

- forward agent 1종을 harness/SUBAGENTS.md의 역할 표에 "탐색" 행으로 추가한다.
  - 입력: 대상(로컬 코드 경로 또는 URL), 과업 목표(success_rule), ExplorePolicy(깊이·예산·허용 행동)
  - 수정 범위: 없음(읽기 전용, 탐색 산출물만 쓴다)
  - 인계 산출물: 상태 그래프(노드·간선), 과업 경로, 단계 초안, 미탐색 프런티어, 중단 사유
- 검증 에이전트는 별도로 두고, forward agent의 결과를 정답 퍼널과 비교만 한다.

SKILL

- 로컬: 프레임워크별 라우트 추출 스킬. 정적 분석으로 후보 그래프를 만들고, GUI 탐색으로 실제 도달 여부를 확인한다.
  - React Router: 라우트 선언·navigate/Link 호출
  - Next.js: app/·pages/ 파일 기반 라우팅
  - Vue Router 등은 필요할 때 추가
  - 공통 한계: 동적 라우트, 조건부 렌더링, 모달·다단계 폼은 코드만으로 확정 불가 → GUI 확인 대상으로 표시
- URL: 읽기 전용 깊이 제한 그래프 탐색 스킬.
  - 진입 URL에서 깊이 d까지 탐색, origin 제한, 비가역 행동(제출·결제·가입·로그아웃) 클릭 금지
  - 로그인 필요 시 등록된 테스트 계정 세션으로만 진입, 벽은 blocked 기록
  - 경계 노드에서 정지, 종착점은 사람이 선언한 success_rule

Workflow

1. 대상 판정: 타겟 소스 코드를 읽을 수 있으면 local, 아니면 url. (URL 모드의 실행 안전 정책은 origin 허용 목록으로 별도 적용, 모르면 fail closed.)
2. 지도 작성:
   - (local) 먼저 프레임워크·라우터 종류·버전을 감지하고(`skills/front_framework`), 지원 목록에 없거나 모호하면 추측하지 않고 blocked로 남긴 뒤 화면 탐색 폴백(unverified)으로 간다. 감지된 규칙으로 코드에서 라우트·이동 호출·폼 필수 입력·조건 분기를 추출해 전체 후보 그래프를 만든다. 코드로 확정 못 하는 노드·간선은 needs_gui_check로 표시하고 forward agent가 화면에서 확인한다. 코드에서 전체 구조가 보이므로 깊이 제한은 GUI 확인 구간에만 적용한다.
   - (url) 코드가 없으므로 깊이 d까지 화면 탐색으로 그래프를 만든다.
3. 줄기 후보 제시: features·tasks에 근거·깊이·미탐색 여부를 채워 P05 칩으로 전달. 사용자가 과업을 이미 줬으면 이 질문은 생략.
4. 줄기 탐색: 선택된 과업만 더 깊게 탐색, 경계에서 정지.
5. 퍼널 초안: 경로 투영 → task_funnels.stages[{id,name,order}].
6. 확정: local은 정답 비교 통과 시 확정, url은 사용자 승인 후 revision.

성공 기준 (각 항목은 pass / fail / blocked로 기록)

- [ ] (local 스킬 검증) 코드와 정답을 아는 우리 모의 사이트에서 코드 기반 추출 퍼널이 정답 대비 단계 재현율·정밀도 기준 이상 (기준값 정하기)
- [ ] (local) 단계·간선마다 근거 코드 위치(파일:줄)가 연결되고, 코드로 확정 못 한 항목은 needs_gui_check로 남아 GUI 확인 전에는 확정으로 세지 않는다
- [ ] (local) 코드 추출 결과와 GUI 확인 결과가 어긋나면 불일치로 기록한다
- [ ] 같은 대상을 N회 탐색했을 때 단계 구성 일치율이 기준 이상
- [ ] 배정 결과의 단계는 항상 7개 키 안에 있고(7개 초과 없음) 경로 순서에서 단조 비감소다 (`check_stage_paths_contract.py`, 합성 fixture에서 확인)
- [ ] 신호가 없는 페이지는 `basis=ordinal`로 표시되고, 키워드 사전 없이 배정된다 (`check_no_hardcoding.py`는 추출기·감지기만 대상, 어댑터는 계약 어휘를 설정 파일로 분리)
- [ ] 동적 라우트가 현재 `stage_paths`(경로 접두사 매칭)로 표현되지 않으면 조용히 틀린 단계를 내지 않고 `collisions`로 표시한다
- [ ] other(미분류) 비율이 기준 이하
- [ ] url 모드에서 비가역 행동이 0건 (탐색 로그로 증명)
- [ ] 정찰 결과가 페르소나 입력에 포함되지 않음 (계약·테스트로 확인)
- [ ] 로그인 벽·깊이 한계는 blocked/frontier로 남고 완료로 세지 않는다
- [ ] 주 과업의 퍼널 단계가 정해졌다 (원래 항목: 사용자 승인된 revision 존재)