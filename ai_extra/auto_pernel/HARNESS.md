# 퍼널 단계 자동화 하네스

[SPEC](SPEC.md)의 성공 기준을 실행 결과와 연결하는 검증 명세다. 형식·판정 규칙은 [harness/SPEC.md](../../harness/SPEC.md)를 따른다. **아래 항목은 전부 미구현·미실행이다.** 제안 테스트명은 아직 존재하지 않으며, 통과했다고 표시하지 않는다.

## 범위

| 구분 | 내용 |
|---|---|
| 자동화 대상 | 하드코딩된 단계(STAGES ×3 중복)·과업(TASKS)·기능(FEATURES)을 대상 탐색 결과로 대체 |
| local | 타겟 소스 코드를 읽어 후보 그래프를 만들고 GUI로 확인 |
| url | 화면만으로 깊이 제한 탐색 |
| 범위 밖 | 실제 고객 이탈률, 실사이트 자동 테스트, 페르소나 행동 변경 |

## 선행 조건 (blocked 판정 기준)

- 정답 퍼널과 소스를 가진 fixture 저장소: 쇼핑몰 프론트는 별도 FE 체크아웃(`DROPSPOT_FE_ROOT`, 기본 `../FE`) 소유이고 현재 이 작업 디렉터리 옆에는 없다. FE가 없으면 local 검증은 **blocked**다.
- 기준값(재현율·정밀도·일관성·`other` 비율)은 미정이다. 기준값 없이 pass를 만들지 않는다. 기준값이 없는 항목은 blocked(`threshold_undefined`)로 기록한다.
- 단계 레지스트리 통합(선행 리팩터링)이 끝나기 전에는 자동 결과를 꽂을 곳이 세 군데라서 FUNNEL-00을 먼저 본다.

## 요구사항

| ID | 요구사항 | 모드 | 판정 단위 |
|---|---|---|---|
| FUNNEL-00 | 단계 정의가 단일 레지스트리에서만 읽히고 리포트·Human Alignment·집계가 같은 목록을 쓴다 | 공통 | 정적 검사 + 단위 |
| FUNNEL-01 | 현재 고정 퍼널 출력을 기준선(golden)으로 저장하고, 자동 추출이 정답 fixture에서 기준선과 같은 단계 순서를 재현한다 | local | 단계 ID 순서 비교 |
| FUNNEL-02 | 코드에서 추출한 단계·간선마다 근거 코드 위치(파일:줄)가 연결된다 | local | 산출물 스키마 검사 |
| FUNNEL-03 | 코드만으로 확정할 수 없는 항목(동적 라우트·조건부 렌더링·모달)이 `needs_gui_check`로 남고, GUI 확인 전에는 확정으로 집계되지 않는다 | local | 상태 값 |
| FUNNEL-04 | 코드 추출 결과와 GUI 확인 결과가 어긋나면 불일치로 기록되고 조용히 한쪽으로 덮어쓰지 않는다 | local | 불일치 레코드 |
| FUNNEL-05 | 프레임워크 판정(React Router·Next.js 등)이 설정 파일로 이루어지고, 미지원 프레임워크는 blocked(`framework_unsupported`)다 | local | 판정 결과 |
| FUNNEL-06 | 단계 수가 고정값이 아니며 과업·fixture에 따라 달라진다 | 공통 | 서로 다른 fixture의 단계 수 |
| FUNNEL-07 | 같은 대상을 N회 탐색했을 때 단계 구성이 기준 이상으로 일치한다 | 공통 | 반복 실행 비교 |
| FUNNEL-08 | `other`(미분류) 비율이 기준 이하다 | 공통 | 방문 상태 중 미분류 비율 |
| FUNNEL-09 | url 모드 탐색 중 비가역 행동(제출·결제·가입·로그아웃·삭제)이 0건이고 탐색 로그로 증명된다 | url | 행동 로그 |
| FUNNEL-10 | url 모드가 허용 origin·깊이·페이지 수·요청 간격·LLM 호출 상한(ExplorePolicy)을 지키고, 초과 시 중단 사유를 남긴다 | url | 로그·중단 사유 |
| FUNNEL-11 | 로그인·결제 벽은 `blocked`, 깊이 한계는 `frontier`로 남고 완료로 세지 않는다 | url | 종료 상태 |
| FUNNEL-12 | url 결과는 `verification=unverified`이고 사용자 승인 전에는 revision으로 확정되지 않는다 | url | 상태 전이 |
| FUNNEL-13 | 단계 ID가 revision 간 유지되고 이름·순서 변경에도 바뀌지 않는다 (R05 매핑 정렬) | 공통 | ID 비교 |
| FUNNEL-14 | 탐색 결과·정답 퍼널이 페르소나 실행 입력(프롬프트·관찰)에 포함되지 않는다 | 공통 | 입력 payload 검사 |
| FUNNEL-15 | 결과마다 `mode`·`verification`·근거가 붙는다 | 공통 | 스키마 검사 |
| FUNNEL-16 | 주 과업의 퍼널 단계가 정해졌다 (local: 정답 비교 통과, url: 사용자 승인 revision 존재) | 공통 | revision 상태 |

## Fixture

| 이름 | 용도 | 상태 |
|---|---|---|
| 모의 쇼핑몰(FE 체크아웃) | 정답 퍼널을 아는 기준 fixture (order·delivery·coupon·subscription) | FE 체크아웃 필요, 현재 blocked |
| 라우팅 변형 | React Router·Next.js 파일 기반 각각의 최소 예제 | 미작성 |
| 동적 요소 | 동적 라우트·조건부 렌더링·모달 다단계 폼 | 미작성 |
| 루프·분기 | 뒤로 가기 루프, 같은 화면 다른 경로 | 미작성 |
| url 오프라인 스냅샷 | 공개 페이지를 정제해 저장한 replay (실개인정보 제거 확인 후) | 미작성 |

fixture에는 실개인정보·실결제·운영 계정 자료를 넣지 않는다. 자동 테스트는 모의 사이트와 정제한 스냅샷으로 제한하고 실사이트에서 실행하지 않는다.

## 판정 규칙

- **pass**: 해당 ID의 증거가 현재 소스 fingerprint로 실행돼 기준을 충족.
- **fail**: 실행됐고 기준 미달 또는 위반(예: 비가역 행동 1건 이상).
- **blocked**: 선행 조건 미충족(FE 체크아웃 없음, 기준값 미정, 프레임워크 미지원, 키 없음), 필수 테스트 누락·skip, 실행 중 소스 변경.
- 실행하지 않은 항목은 "미실행"이며 완료로 세지 않는다. 체크박스만으로 성공을 선언하지 않는다.
- LLM 호출이 들어가는 항목(단계 이름 붙이기)은 키·모델·호출 범위를 확인하고 명시적으로 실행한다. 고정 정책·SDK mock 결과는 실제 호출 결과와 구분해 기록한다.

## 증거

- 결과: `artifacts/harness/<실행 ID>/verdict.json` (제안: 프로필 `funnel`, 아직 `scripts/verify_harness.py`에 없음), `execution.log`, 탐색 산출물(그래프·단계 초안·근거 위치).
- 각 판정에 실행 시각, 명령 argv, Python 경로, 소스 SHA-256, 로그 경로를 남긴다.
- local pass는 url 검증을 충족하지 않고, url 결과는 정답 정확도의 증거가 아니다.

## 증거 대응표 (제안 테스트명, 전부 미작성)

| ID | 제안 테스트 | 상태 |
|---|---|---|
| FUNNEL-00 | `test_stage_registry_is_single_source` | 미작성 |
| FUNNEL-01 | `test_extracted_funnel_matches_baseline` | 미작성 |
| FUNNEL-02 | `test_every_stage_has_code_evidence` | 미작성 |
| FUNNEL-03 | `test_unresolved_nodes_need_gui_check` | 미작성 |
| FUNNEL-04 | `test_code_gui_mismatch_is_recorded` | 미작성 |
| FUNNEL-05 | `test_framework_detection_and_unsupported_blocked` | 미작성 |
| FUNNEL-06 | `test_stage_count_is_not_fixed` | 미작성 |
| FUNNEL-07 | `test_repeated_exploration_consistency` | 미작성 |
| FUNNEL-08 | `test_other_ratio_under_threshold` | 미작성 |
| FUNNEL-09 | `test_url_mode_has_no_irreversible_actions` | 미작성 |
| FUNNEL-10 | `test_explore_policy_limits_enforced` | 미작성 |
| FUNNEL-11 | `test_auth_wall_blocked_and_depth_frontier` | 미작성 |
| FUNNEL-12 | `test_url_result_unverified_until_approved` | 미작성 |
| FUNNEL-13 | `test_stage_ids_stable_across_revisions` | 미작성 |
| FUNNEL-14 | `test_exploration_not_in_persona_input` | 미작성 |
| FUNNEL-15 | `test_result_has_mode_verification_evidence` | 미작성 |
| FUNNEL-16 | `test_primary_task_funnel_approved_revision` | 미작성 |

## 아직 검증하지 않는 것

- 임의 실사이트에서의 퍼널 정확도, 실제 고객 이동 경로와의 일치, GA4 이벤트 매핑 정확도.
- 로그인 후 영역 탐색(테스트 계정 세션): 계정 격리·세션 만료 처리는 별도 요구사항을 추가한 뒤 검증한다.
- 공개 URL 환경의 SSRF 방어·운영 자원 격리 (기존 범위 밖 항목과 동일).

## 인계 형식

요구사항 ID, 실행 명령, verdict 경로, 소스 fingerprint, pass·fail·blocked 건수, 미실행·미검증 항목, 불일치 레코드 위치를 전달한다.
