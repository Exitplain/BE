# EXITPLAIN API 명세서

작성일: 2026-11-11 · 현재 구현 계약: local-v1 · 제품 계약: v1-draft

디자인: [4호선톤 Figma](https://www.figma.com/design/XYkGMN8Avldbl3SN65vEsr?node-id=0-1).
관련 화면: W01 대시보드, C02 프로젝트, P02~P05c 새 테스트, P09 실행, P12 리포트, P15 세션 상세, R01~R11 실측 분석, K01~K04 보정.

**2~7절은 현재 실행 가능한 Python 개발 서버 계약입니다. 8절의 `/api/v1`은 미구현 제품 계약입니다.**
기존 Django `backend/exitplain/urls.py`에는 `/admin/`만 있으며, 아래 개발 API를 Django가 제공하는 것은 아닙니다.

## 1. 실행과 저장소 경계

- BE: 에이전트, API 개발 어댑터, 테스트, Django 뼈대.
- FE: `saas-web/` 화면, `demo-shop/` 모의 쇼핑몰, 이미지·폰트.
- BE에서 `python -m agent.console --port 4174` 실행. 기본 주소 `http://127.0.0.1:4174`.
- BE와 FE를 같은 상위 폴더 아래 클론. 다른 배치에서는 `DROPSPOT_FE_ROOT`에 FE 절대 경로 지정.
- 브라우저는 `/api/...` 상대 경로 사용. 같은 서버가 FE 정적 파일을 제공하므로 별도 CORS 설정 불필요.
- 실행 근거는 BE의 `artifacts/`에 저장. 프로젝트 등록·워크스페이스 설정은 현재 FE의 localStorage.
- `GET /api/state`의 기본 진단과 `GET /api/personas`의 페르소나 실행은 서로 다른 실행 저장소·스키마입니다. ID를 교차 사용하지 않습니다.

## 2. 공통 규칙

| 항목 | 현재 규칙 |
|---|---|
| 요청 | JSON, `Content-Type: application/json` |
| 응답 | JSON UTF-8. 정적 파일·Markdown·PDF·PNG는 해당 MIME |
| 시간 | ISO 8601 UTC 문자열. FE에서 사용자 시간대로 표시 |
| 캐시 | `Cache-Control: no-store` |
| 오류 | `{ "error": "사용자에게 표시할 메시지" }` |
| 인증 | 로컬 개발 서버에는 계정 인증·리소스 소유권 검사 없음 |
| Origin | POST에 Origin이 있으면 동일 포트의 localhost 또는 127.0.0.1만 허용. 불일치 403 |
| polling | 상태 조회는 FE에서 주기적으로 호출. SSE/WebSocket 미구현 |
| 재전송 | 실행 생성에 idempotency key 없음. 응답 유실 시 상태를 먼저 조회 |
| ID | 응답에서 받은 ID 사용. 로컬 파일 경로를 ID로 보내지 않음 |

`discover`, `confirm`, `explain`, `human`, 기본 `runs` POST 본문은 1~4096바이트입니다.
호환용 `personas/start`는 1~128바이트입니다. 한국어 본문은 글자 수와 UTF-8 바이트 수가 다릅니다.

## 3. 엔드포인트 목록

| 메서드 | 경로 | 성공 | 용도 |
|---|---|---:|---|
| GET | `/api/state` | 200 | 기본 프로젝트·2세션 진단 목록 |
| POST | `/api/runs` | 202 | 기본 2세션 진단 생성 |
| GET | `/api/personas` | 200 | 현재 페르소나 실행·프로필·과업·관측 |
| POST | `/api/personas/discover` | 200 | 모의 사이트 메뉴 읽기·과업 후보 생성 |
| POST | `/api/personas/confirm` | 202 | 기능·과업·타겟 확인 후 실행 |
| POST | `/api/personas/start` | 202 | 기존 클라이언트용 직접 실행 |
| POST | `/api/personas/explain` | 200 | 세션 기록 기반 질문 답변 저장 |
| POST | `/api/personas/human` | 200 | 실제 사람 관측 수동 입력 |
| GET | `/api/personas/reports` | 200 | 완료한 실행 이력 |
| GET | `/api/personas/report?run_id={id}` | 200 | 완료 리포트 JSON |
| GET | `/api/personas/report.md?run_id={id}` | 200 | Markdown 리포트 |
| GET | `/api/personas/report.pdf?run_id={id}` | 200 | PDF 다운로드 |
| GET | `/api/artifacts/{artifact_id}` | 200 | 실행 화면 PNG |
| GET | `/api/connections/demo` | 200 | Skyvern 데모 연결 상태 |
| POST | `/api/connections/demo/start` | 200 | 데모 브라우저 연결 |
| POST | `/api/connections/demo/checkpoint` | 200 | 현재 시작 화면 저장 |
| POST | `/api/connections/demo/close` | 200 | 연결 종료 |

## 4. 새 테스트: 사이트 → 과업·타겟 → 퍼널·페르소나 → 시작 전 확인

### 4.1 사이트 확인

`POST /api/personas/discover`

```json
{"variant":"fixed"}
```

`variant`: 필수, `fixed | problem`. **다른 필드는 허용하지 않습니다.** 외부 URL 검사 API가 아닙니다.
실제 로컬 브라우저로 시작 화면의 GET/HEAD만 읽습니다. 실행 세션은 생성하지 않습니다.

응답 형태(배열은 일부 항목 예시):

```json
{
  "project": "반찬로드",
  "plan_id": "<탐색 결과 ID>",
  "variant": "fixed",
  "features": [{
    "id": "shopping",
    "name": "상품 탐색·장바구니·주문",
    "evidence": {"label":"이번 주 식탁 만나기","path":"/product"},
    "verification": "visible_entry_only"
  }],
  "tasks": [{
    "id":"order", "name":"첫 주문", "goal":"모의 주문 완료",
    "feature_id":"shopping", "suggested":true
  }],
  "age_options": {"any":"전체 연령","20s":"20대","30s":"30대"},
  "note":"공개 시작 화면의 링크를 읽었습니다. 기능의 정상 작동을 판정한 것은 아닙니다."
}
```

FE는 응답의 기능·과업·연령 선택지를 사용합니다. 메뉴 문구·과업 설명을 식별자로 사용하지 않습니다.
계획은 프로세스 메모리에 보관하며 **15분 후 만료**, 서버 재시작 시 소실됩니다.

### 4.2 최종 확인 및 실행

`POST /api/personas/confirm`

```json
{
  "plan_id":"<discover 응답 ID>",
  "feature_ids":["shopping","coupon"],
  "task_ids":["order","coupon"],
  "ages":["20s","30s"],
  "context":"퇴근 후 모바일로 첫 주문하는 고객",
  "additional_request":"쿠폰 적용 후 총액이 바로 보이는지"
}
```

| 필드 | 타입 | 검증 |
|---|---|---|
| plan_id | string | 현재 프로세스의 유효한 탐색 ID |
| feature_ids | string[] | 비어 있지 않음, 중복 불가, 탐색 결과 안의 기능 |
| task_ids | string[] | 비어 있지 않음, 중복 불가, 선택 기능에 속하는 과업 |
| ages | string[] | `any, teens, 20s, 30s, 40s, 50s, 60plus`; any와 다른 값 동시 선택 불가 |
| context | string | 필수 필드, 빈 문자열 허용, 최대 500자 |
| additional_request | string | 필수 필드, 빈 문자열 허용, 최대 500자 |

위 여섯 필드를 정확히 전달합니다. 누락·추가 필드는 400입니다.
자유 메모는 기록만 하고 자동 과업으로 실행하지 않습니다. 연령은 문맥이며 행동 모델을 보정하지 않습니다.
기능 ID: `shopping, delivery, coupon, subscription`.
과업 ID: `order, cart, delivery, coupon, benefit, subscription`.

202 응답은 `GET /api/personas`와 같은 실행 상태 객체입니다. 핵심 필드:

```json
{
  "id":"<실행 ID>", "status":"running", "variant":"fixed",
  "policy":"scripted_fixture", "model_calls":0,
  "profiles":[{"id":"quick","name":"빠른 구매","width":390,"max_required_fields":2}],
  "sessions":[{
    "id":"quick_order", "profile_id":"quick", "task_id":"order",
    "task_name":"첫 주문", "status":"queued", "steps":0,
    "reason":"브라우저 준비 대기", "events":[], "trace":[],
    "artifact_id":null, "viewer_url":null
  }],
  "audience":{"ages":["20s","30s"],"context":"퇴근 후 모바일로 첫 주문하는 고객"}
}
```

배열과 부가 필드는 축약한 예시입니다. 실제 응답에는 3개 프로필과 **선택 과업 수 × 3세션**, 타겟·관측·좌절 모형·alignment가 포함됩니다.
프로필은 `quick, careful, patient`, 동시 실행 한도는 3입니다. 현재 UI에서 임의 세그먼트 수·퍼널 순서·시드 횟수를 보내지 않습니다.

오류: 400 잘못된 입력·만료된 계획, 409 진행 중인 실행, 503 탐색/처리 예외.
FE는 400 만료 시 사이트 확인 단계부터 다시 진행하고, 409는 현재 실행을 조회합니다.

### 4.3 상태 조회와 기존 직접 실행

`GET /api/personas`는 현재 실행을 반환합니다. 실행 상태는 `idle | running | completed`입니다.
실행이 completed여도 개별 세션이 오류로 종료될 수 있으므로 sessions를 확인합니다.

세션 주요 상태: `queued, running, success, simulated_dropout, agent_failure, budget_exceeded`.
리포트는 `site_failure, cancelled, blocked_auth`도 기술 종료로 분류합니다.
`simulated_dropout`만 가상 이탈로 해석하고 기술 실패를 이탈률에 합산하지 않습니다.

`POST /api/personas/start`: `{"variant":"fixed","task_id":"order"}`.
`task_id` 생략 시 order, `all`은 기본 4과업. 신규 UI는 discover/confirm을 사용합니다.

## 5. 세션 질문·사람 관측

### 5.1 기록 기반 질문

`POST /api/personas/explain`

```json
{"run_id":"<현재 실행 ID>","session_id":"quick_order","question":"왜 멈췄나요?","step":3}
```

`step`은 선택, 나머지 필드는 필수. question은 비어 있지 않은 최대 500자 문자열.
현재 실행 ID와 실제 세션 ID를 사용합니다. 200은 저장된 답변 객체이며 `answer`, `sources` 등 근거 정보를 포함합니다.
기록 기반 규칙 설명입니다. 별도 대화 모델 호출이나 비공개 사고 과정 출력이 아닙니다.
잘못된 실행·세션·질문은 400, 처리 충돌은 409, 처리 예외는 503입니다.

### 5.2 사람 관측 저장

`POST /api/personas/human`

```json
{
  "run_id":"<현재 완료 실행 ID>","persona_id":"quick_order",
  "participant_id":"participant_01","outcome":"dropout",
  "stage":"signup","cause":"input_burden","source":"참여자 01 관측 기록"
}
```

- persona_id는 프로필 ID가 아니라 **비교할 세션 ID**입니다.
- participant_id: 영문·숫자·밑줄·하이픈 1~40자. source: 공백 제거 후 3~300자.
- outcome: `success | dropout`. success는 stage=complete, cause=none이어야 합니다.
- stage: `landing, product, cart, signup, checkout, complete, delivery, benefits, coupon, account, subscription`.
- cause: `input_burden, navigation, cost, value, trust, technical, accessibility, unknown, none`.
- 동일 참여자×과업×세션은 기존 관측을 갱신. 동일 참여자×과업을 다른 세션에 중복 대응하면 400.
- 현재 실행 완료 후에만 저장 가능. 200은 갱신된 페르소나 상태, 검증 오류는 400.

## 6. 리포트·근거

### 6.1 이력 및 상세

`GET /api/personas/reports`

```json
{"reports":[{"run_id":"<ID>","created_at":"2026-11-11T12:00:00+00:00","variant":"fixed","sessions":12}]}
```

완료 실행만 최신순으로 반환합니다. 현재 페이지네이션은 없습니다.

`GET /api/personas/report?run_id={id}`

| 응답 필드 | 내용 |
|---|---|
| schema_version, run_id, created_at, target, variant | 리포트 식별·조건 |
| audience, policy, model_calls | 확인한 타겟·실행 정책 |
| summary | sessions, success, simulated_dropout, technical, findings, missing_evidence |
| tasks[] | id, name, total, success, dropout, technical |
| findings[] | id, task_id, stage, category, title, affected_sessions, task_sessions, evidence[], interpretation, recommendation, mockup |
| evidence[] | session_id, artifact_id, evidence_status, path, annotation, observations, steps_before, score, threshold |
| technical_failures[] | session_id, persona_name, task_name, status, reason |
| alignment | participants, observations, metrics의 outcome/stage/cause, 검증 상태 |
| limitations[] | 화면에 유지할 결과 해석 한계 |

run_id 생략 시 현재 실행을 사용합니다. 현재 실행이 미완료이면 과거 결과로 자동 대체하지 않습니다.
404 저장된 실행 없음, 409 미완료 실행 또는 유효하지 않은 조회 조건.
정상 결과에 근거가 없으면 `missing_evidence`, `evidence_status`로 표시하고 이미지를 만들어 채우지 않습니다.

### 6.2 내보내기와 화면 근거

- `.md`: `text/markdown; charset=utf-8`, 리포트 Markdown 문자열.
- `.pdf`: `application/pdf`, attachment 파일명 `dropspot-report-{run_id}.pdf`.
- PDF는 Chromium으로 렌더링. 동시 1건, 생성 중 추가 요청 429, 생성 실패 503. FE는 버튼 복구·재시도 안내.
- `/api/artifacts/{artifact_id}`: PNG 바이트, 없는 ID는 404. ID는 API 응답에서 받은 불투명 토큰.
- PDF는 서버 로컬 리포트 화면과 FE 자산이 필요하므로 배포 시 양쪽 파일을 함께 제공해야 합니다.

## 7. 기본 진단·브라우저 연결

`GET /api/state`: `{projects:[...], runs:[...]}`. 기본 프로젝트 ID는 banchan.
run에는 id, project_id, project_name, variant, case, created_at, status, progress, sessions, events, error가 포함됩니다.

`POST /api/runs`:

```json
{"project_id":"banchan","variant":"problem","case":"signup"}
```

202 `{"run_id":"<ID>"}`. project_id 생략 시 banchan, variant=fixed|problem, case=all|signup|fee|cta.
2개 독립 세션 고정 실행. 잘못된 조건 400, 실행 중 중복 시작 409. `/api/state` polling으로 결과 확인.

Skyvern 연결은 `GET /api/connections/demo` 조회 후 `/start`, `/checkpoint`, `/close` POST를 사용합니다(본문 `{}`).
응답은 status, 연결 정보, checkpoint 등 공개 상태입니다. checkpoint에는 title, url, controls, artifact_id 등이 포함됩니다.
상태: disconnected, connected, captured. 현재 연결 상태 조회 자체에서 만료를 자동 판정하지는 않습니다. 설치·서비스 불가 503, 연결 상태 충돌 409, 예외 500.
로컬 데모의 수동 시작 화면 확인용이며 고객 사이트 로그인 상태의 제품 저장 API가 아닙니다.

## 8. Figma 변경에 필요한 제품 API — 미구현

아래 경로는 기존 [API·데이터 계약 초안](API_및_데이터_계약.md)을 바탕으로 구체화한 **후속 구현 제안**입니다.
현재 FE에서 호출하지 않으며 현재 개발 API와 바꿔 쓸 수 없습니다. prefix는 `/api/v1`.
공통: 인증된 세션·CSRF, 리소스 소유권 검사, JSON 오류 `{error:{code,message,fields},request_id}`,
목록 `{items,next_cursor}`, 생성형 비동기 작업 202 `{job_id,status_url}`, 실행 생성 `Idempotency-Key`, 계획 수정 `If-Match`.

| Figma 흐름 | 메서드 / 경로 | 요청 핵심 | 성공 응답 핵심 |
|---|---|---|---|
| W01 대시보드 | GET `/workspaces/{id}/overview` | 기간 | completed_runs, failed_runs, active_runs, project_loops, recent_issues |
| C02~C04 프로젝트 | GET/POST `/projects` | name, entry_url | 목록 / 201 project |
| P02 사이트 확인 | POST `/environments/{id}/checks` | environment_revision | 202 check_job_id |
| P03 기능 탐색 | POST `/projects/{id}/discoveries` | environment_id, checkpoint_id? | 202 job_id |
| 탐색 조회 | GET `/discoveries/{id}` | 없음 | features, tasks, provenance, blocked_reason |
| P05 계획 | POST `/projects/{id}/plans` | discovery_id | 201 id, revision, tasks, audience |
| P05b 퍼널·비율 | PATCH `/plans/{id}` | task_funnels, segments, frustration_enabled, limits | 수정된 revision |
| P05c 확인 | POST `/plans/{id}/confirmations` | revision | 201 immutable plan_revision_id |
| P07~P10 실행 | POST `/runs` | plan_revision_id, environment_revision_id | 202 run_id, status_url |
| P09 상태 | GET `/runs/{id}` | 없음 | queued/preparing/running/analyzing/completed/failed/cancelled, progress |
| P11/P15 세션 | GET `/runs/{id}/sessions` | cursor, status | 세션 목록·최근 관측 |
| P15 질문 | POST `/sessions/{id}/questions` | question, observation_id? | 202 question_id |
| P12 리포트 | GET `/runs/{id}/reports` | 없음 | final/partial, revision, findings, prediction/actual 구분 |
| P13 내보내기 | POST `/reports/{id}/exports` | format=pdf\|md, locale | 202 export_job_id |
| P17 재테스트 | POST `/runs` | base_run_id, 새 plan_revision_id | 새 run_id, 이전 실행 보존 |
| P18/P19 비교 | POST `/comparisons` | base_run_id, target_run_id | 비교 결과 또는 비교 불가 사유 |
| P20/P21 공유 | POST `/reports/{id}/shares` | expires_at, scope | 201 share_id, url, expires_at |
| 공유 해제 | DELETE `/shares/{id}` | 없음 | 204 |
| R01 실측 연결 | POST `/projects/{id}/analytics-connections` | provider, allowed_origins | 201 connection_id, public_ingest_key, snippet |
| R02 설치 확인 | POST `/analytics-connections/{id}/checks` | 없음 | 202 job_id |
| R05 단계 매핑 | PUT `/analytics-connections/{id}/stage-mapping` | revision, events→stage_id | 저장된 mapping_revision |
| R06/R07 퍼널 | GET `/projects/{id}/actual-funnels` | from, to, device, mapping_revision | stages, sample_size, quality_status |
| R08 예측 vs 실측 | POST `/comparisons` | run_id, actual_snapshot_id | 단계별 차이·분모·비교 가능 여부 |
| R09~R11 원인 재현 | POST `/projects/{id}/reproductions` | comparison_id, stage_ids, plan_revision_id | 202 run_id |
| K01 보정안 | GET `/projects/{id}/calibration-proposals` | status | 기준 snapshot·제안값·근거 |
| K02 적용 | POST `/calibration-proposals/{id}/applications` | expected_revision | 201 calibration_revision |
| K03 이력 | GET `/projects/{id}/calibrations` | cursor | 변경 전후·적용자·적용시각 |
| K04 사람 관측 | POST `/projects/{id}/human-observations` | run_id, participant_id, task_id, outcome, stage, cause, source | 201 관측 ID |

### 계획 snapshot 예시 — 제품용 제안

```json
{
  "revision":3,
  "task_ids":["order"],
  "audience":{"context":"퇴근 후 첫 주문 고객","ages":["30s"]},
  "task_funnels":[{"task_id":"order","stages":[
    {"id":"stage_landing","name":"랜딩","order":0},
    {"id":"stage_complete","name":"완료","order":1}
  ]}],
  "segments":[{"id":"segment_1","count":5},{"id":"segment_2","count":5}],
  "frustration_enabled":true,
  "calibration_revision_id":null,
  "limits":{"concurrency":3,"max_steps":40,"timeout_seconds":300,"seed_count":3}
}
```

실제 허용 인원·한도·시드 수는 BE가 반환한 capability 범위에서 검증합니다. 예시 수치 자체가 현재 구현값은 아닙니다.
단계 ID는 이름·순서 변경에도 유지하며, 세그먼트 합계는 확정된 인원과 일치해야 합니다.
확정 후 변경은 새 revision으로 남기고 과거 실행·리포트를 덮어쓰지 않습니다.

### 제품 상태·오류와 화면 처리

| 상황 | 제안 HTTP / code | FE 동작 |
|---|---|---|
| 로그인 만료 | 401 / AUTH_REQUIRED | 원래 목적지를 보존하고 로그인 |
| 권한 없음 | 403 / FORBIDDEN | 권한 없음 화면 |
| URL·단계명·합계 오류 | 422 / VALIDATION_ERROR | fields에 해당하는 입력 아래 오류 |
| 이전 revision | 409 / REVISION_CONFLICT | 최신 계획을 다시 읽고 변경 확인 |
| 로그인 연결 만료 | 409 / LEASE_EXPIRED | 연결 관리로 이동 |
| 비용·실행 한도 | 429 / LIMIT_EXCEEDED | 제한 사유·재시도 가능 시각 표시 |
| 실측 표본 부족 | 200 / quality_status=insufficient_sample | 비교 숫자 대신 표본 부족 표시 |
| 실측 수집 중단 | 200 / quality_status=stale | 마지막 수신 시각 표시 |
| 공유 링크 만료 | 410 / SHARE_EXPIRED | 만료 화면 |
| 내보내기 실패 | job.status=failed | 실패 이유 표시·새 요청으로 재시도 |

예측 분모와 실제 사용자 분모를 구분하고, 기술 실패는 가상 이탈에 포함하지 않습니다.
비교 가능 여부는 같은 과업·퍼널 revision·기간·디바이스·실행 조건 기준으로 BE가 판정합니다.
보정 적용은 이후 실행에만 영향을 주며 기존 리포트의 예측값을 소급 변경하지 않습니다.

## 9. 검증과 변경 관리

- 실행 구현: `agent/console.py`, `agent/preflight.py`, `agent/persona_dashboard.py`, `agent/persona_report.py`.
- 검증: `python scripts/verify_harness.py --profile browser`.
- UI 변경 사항과 아직 없는 기능: FE `docs/FIGMA_IMPLEMENTATION.md`.
- 제품 API를 구현할 때는 이 문서에서 해당 항목의 미구현 표시를 제거하고 요청·응답·오류 계약 테스트를 함께 갱신합니다.
