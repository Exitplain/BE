# 제품 API·데이터 계약 초안

상위 명세: [기능명세서](../기능명세서.md). 이 문서의 `/api/v1`은 **구현 전 제안**이다. 현재 `/api/personas/*` 로컬 어댑터의 동작과 혼동하지 않는다. ERD와 저장/성능 정책은 [ERD·성능 설계](ERD_및_성능설계.md)를 따른다.

## 1. 경계와 원칙

- 브라우저 프론트는 Django만 호출한다. 에이전트/코드 공급자/모델 키·CDP 주소를 직접 받지 않는다.
- 프로젝트 입력은 `url_only|source_connected` 두 값. runtime과 framework는 별도 속성이다.
- 비동기 요청은 job/run ID와 상태 URL을 반환한다. 완료 전 결과를 만들어 반환하지 않는다.
- 변경 요청은 인증·리소스 소유권·정책·필드/버전 검사 후 처리한다. 실행/빌드/내보내기 생성에는 idempotency key를 사용한다.
- 클라이언트에 보내는 ID는 불투명한 식별자다. 경로·저장소 키·비밀값을 외부 ID로 사용하지 않는다.

## 2. 제안 API 목록

| 영역 | 메서드/경로 | 입력·출력과 책임 |
|---|---|---|
| 인증/멤버 | `/auth/*`, `/workspaces/{id}/members/*` | 팀의 인증 방식 확정 후 상세화. 역할/만료/초대 수명 포함 |
| 프로젝트 | GET/POST `/projects`, GET/PATCH `/projects/{id}` | 이름·입력 방식·현재 revision·권한·보관 상태 |
| 환경 | POST/PATCH `/projects/{id}/environments` | URL·runtime·허용 origin·행동 정책·운영/스테이징 |
| 접근 확인 | POST `/environments/{id}/checks` | `202 check_job_id`, 도달/인증/미지원 상태와 근거 |
| 코드 연결 | POST `/projects/{id}/source-connections` | 읽기 연결 시작. provider callback의 state/권한 검사 별도 |
| 코드 ref | GET `/source-connections/{id}/refs` | 권한 있는 ref 목록·cursor. code token을 반환하지 않음 |
| 코드 스냅샷 | POST `/source-connections/{id}/snapshots` | ref·경로→commit 고정·분석 job. 갱신은 새 snapshot |
| 연결 해제 | DELETE `/source-connections/{id}` | 신규 fetch 차단·token 폐기·보존 정책 실행 |
| preview | POST `/source-snapshots/{id}/previews` | 승인된 recipe·secret 참조→build job, 준비 전 실행 불가 |
| 연결 작업 | GET `/jobs/{id}` | 탐색/빌드/내보내기 공통 상태·단계·오류·재시도 가능 여부 |
| 인증 브라우저 | POST `/environments/{id}/browser-leases` | 수명·사용자 소유 세션·권한 있는 스트림 참조 |
| 시작 화면 | POST `/browser-leases/{id}/checkpoints` | 사용자 확인 시작 화면·인증 준비 시각·관측 근거 |
| 기능 탐색 | POST `/projects/{id}/discoveries` | environment/checkpoint/source_snapshot 선택→202 job |
| 탐색 조회 | GET `/discoveries/{id}` | 기능 후보·화면/코드/사용자 출처·부분 발견·차단 이유 |
| 계획 초안 | POST `/projects/{id}/plans` | discovery 기반 초안 생성 |
| 계획 편집 | PATCH `/plans/{id}` | features/tasks/audience/panel/limits, `If-Match` 또는 revision |
| 과업 검토 | POST `/plans/{id}/task-validations` | 자유 과업의 가능성·성공 조건·추가 입력 검토 job |
| 계획 확정 | POST `/plans/{id}/confirmations` | 최신 revision·허용 정책 확인→불변 PlanRevision |
| 실행 생성 | POST `/runs` | plan_revision_id·environment_revision, `202 run_id` |
| 실행 조회 | GET `/runs`, GET `/runs/{id}` | cursor/필터·집계·상태·마지막 갱신·비교 가능 조건 |
| 취소 | POST `/runs/{id}/cancellations` | 이유·중복 허용. 취소 요청과 완료 구분 |
| 재시도 | POST `/runs/{id}/attempts` | 허용 범위 검증·새 attempt. 이전 실패 근거 유지 |
| 이벤트 | GET `/runs/{id}/events?after=...` | cursor·정렬·다음 cursor·보존 범위. SSE는 동일 계약 확장 |
| 세션 | GET `/runs/{id}/sessions`, GET `/sessions/{id}` | task/profile/상태·최근 화면·근거. 전체 trace 기본 포함 금지 |
| 관측 | GET `/sessions/{id}/observations?cursor=...` | 단계별 화면·동작·신호·bbox·페이지네이션 |
| 제어권 | POST/DELETE `/sessions/{id}/control-lease` | 단일 제어자·수명·자동화 일시정지/복귀 |
| 스트림 | POST `/sessions/{id}/stream-tickets` | 짧은 수명의 소유권 검증된 연결. 지속 중 재검증 정책 |
| 질문 | POST `/sessions/{id}/questions`, GET 목록 | 질문 ID·대기/답변/실패·출처·설명 정책 버전 |
| 근거 | POST `/artifacts/{id}/access` | 현재 권한·보존 여부 검사 후 짧은 수명 조회 URL |
| 리포트 | GET `/runs/{id}/reports` | partial/final·snapshot_revision·문제/분모·한계 |
| PDF 등 | POST `/reports/{id}/exports` | format/locale→export_job, 완료 시 artifact 참조 |
| 이슈/비교 | GET/PATCH `/issues/{id}`, POST `/comparisons` | 발생 이력·수동 검토, base/target와 비교 불가 사유 |
| 반복/알림 | `/projects/{id}/schedules`, `/notification-rules/*` | 시간대·한도·trigger·수신 채널·발송 이력 |
| 사람 관측 | `/projects/{id}/human-observations` | 익명 참여자×과업·조건·관측 출처·검토/평가 구분 |
| 운영/사용량 | `/usage`, `/admin/jobs`, `/admin/attempts/*` | 비용 예약/정산·지연/실패·관리자 재처리 감사 |

이는 한 번에 전부 구현할 목록이 아니다. P0 공통 흐름을 먼저 연결하고 P1 코드/반복 관리 API를 순차 공개한다.

## 3. 실행 입력 snapshot 제안

```json
{
  "contract_version": "2-draft",
  "run_id": "run_x",
  "attempt_id": "attempt_x",
  "plan_revision_id": "plan_rev_x",
  "environment_revision_id": "env_rev_x",
  "input_mode": "source_connected",
  "runtime": {"kind": "web", "entry_url": "https://staging.example.test", "interaction": "hybrid"},
  "source": {"snapshot_id": "src_x", "commit_sha": "<fixed sha>", "deployment_match": "unverified"},
  "session_contexts": [{"task_id": "task_x", "persona_snapshot_id": "persona_x", "account_lease_ref": "lease_x"}],
  "safety_policy_revision": "safe_rev_x",
  "limits": {"max_concurrency": 3, "max_steps_per_session": 35, "model_token_budget": 20000},
  "success_rules": [{"task_id": "task_x", "kind": "observed_state", "description": "모의 주문 완료 안내가 실제로 표시됨"}]
}
```

수치는 계약 예시이며 운영 기본값/실측이 아니다. 현재 `RunInput`과 `PersonaDashboard` 입력을 대체하는 확정 구현도 아니다. source가 없는 A는 `source=null`이고 공통 실행 경로를 유지한다. source token·실제 비밀값은 snapshot에 넣지 않는다. browser/account lease 참조는 권한 있는 워커만 해석한다.

## 4. 상태와 일관성

| 객체 | 제안 상태 |
|---|---|
| 준비 작업 | queued → running → succeeded / partial / failed / cancelled |
| 과업 초안 | draft → validating → ready / needs_input / unsupported |
| 계획 | draft → confirmed → superseded / expired |
| Run | queued → provisioning → running → finalizing → completed / partially_completed / failed / cancelled |
| Attempt | queued → running → finalizing → completed / failed / cancelled / worker_lost |
| Session | queued → provisioning → running → success / simulated_dropout / agent_failure / site_failure / blocked_auth / budget_exceeded / cancelled |
| PDF | queued → rendering → ready / failed / expired |
| 비교 | queued → running → ready / incomparable / failed |

- 제품 Run/Attempt 상태는 기존 에이전트 종료 상태와 어댑터에서 매핑한다. 인프라 오류를 `simulated_dropout`으로 바꾸지 않는다.
- `CancelRequested`는 상태 전이를 요청한 기록이다. 자원 종료 확인 전 `cancelled`로 확정하지 않는다.
- 실시간 집계는 잠정, ReportSnapshot은 revision별 고정이다. 늦게 온 근거가 생기면 새 리포트 revision을 만든다.
- DB 저장과 큐 전달 간에는 outbox 등 일관성 장치를 두고 중복 전달을 허용하되 중복 실행을 막는다.
- event_id로 멱등 수집한다. producer의 순서와 서버 수집 순서를 구분하고 이벤트 발생 시각/수집 시각을 모두 저장한다. attempt 전체 순서가 필요하면 coordinator가 직렬 부여한다.

## 5. 오류 응답

```json
{
  "error": {
    "code": "START_SCREEN_EXPIRED",
    "message": "시작 화면의 인증이 만료됐습니다. 다시 연결해주세요.",
    "retryable": false,
    "next_action": "reconnect_browser",
    "request_id": "req_x",
    "field_errors": {}
  }
}
```

- `400/422`: 필드/지원 범위, `401`: 로그인 필요, `403/404`: 소유권 정책에 맞는 비노출 거부.
- `409`: 계획 revision 충돌·준비 미완료·제어권 충돌, `410`: 근거/스트림 만료, `429`: quota/동시성, `503`: 공급자/워커 준비 불가.
- 에러에 provider 원문·token·쿠키·내부 경로·고객 코드 원문을 싣지 않는다. 필요한 운영 상세는 접근 제한 로그와 request_id로 연결한다.

## 6. 공통 인수 기준

- API 예제/에러/페이지네이션/권한/멱등성까지 OpenAPI로 합의한다.
- FE는 API mock으로 정상·대기·부분 실패·만료·권한 거부·충돌을 먼저 구현할 수 있어야 한다.
- BE는 다른 workspace ID, 중복 요청/이벤트, 늦은 이벤트, 실패 복구를 테스트한다.
- 에이전트는 확정된 계약의 example input/event/result를 제공한다. 현재 두 실행 경로를 통합하는 compatibility adapter를 별도 작업으로 둔다.
