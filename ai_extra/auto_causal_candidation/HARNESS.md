# 원인 후보 자동화 하네스

[SPEC](SPEC.md)의 성공 기준을 실행 결과와 연결하는 검증 명세다. 형식·판정 규칙은 [harness/SPEC.md](../../harness/SPEC.md)를 따른다. **아래 항목은 전부 미구현·미실행이다.** 제안 테스트명은 아직 존재하지 않으며, 통과했다고 표시하지 않는다.

## 범위

| 구분 | 내용 |
|---|---|
| 자동화 대상 | 고정 3종 분류(`CATEGORIES`)·고정 개선 문구(`recommendation()`)·고정 비용 문구 일치 판정을 근거 기반 후보 도출로 대체 |
| local | 이탈 단계·화면을 해당 화면의 소스 코드와 연결해 후보의 근거(파일:줄) 부여 |
| url | 코드 없이 화면·행동 trace 근거로 후보 도출, 결과는 unverified |
| 범위 밖 | 원인 확정, 실제 고객 심리 주장, 좌절 모델 보정(별도) |

## 선행 조건 (blocked 판정 기준)

- 원인 분류 체계가 미확정이다: 리포트 3종, Human Alignment 9종, README의 "7종"(목록 미정의). 목록 확정 전에는 후보 정확도 항목(CAUSE-03)을 pass로 만들 수 없고 blocked(`taxonomy_undefined`)로 기록한다.
- 원인을 아는 fixture(문제 변형)와 소스 코드: 쇼핑몰 프론트는 별도 FE 체크아웃 소유이고 현재 없으면 local 검증은 blocked다.
- 기준값(재현율·정밀도·일치율)이 미정이면 blocked(`threshold_undefined`).
- 세션 종료 기록(단계·trace·좌절 점수 구성·문제 시점 캡처)이 입력이다. 캡처가 없으면 해당 후보는 근거 부족으로 처리한다.

## 요구사항

| ID | 요구사항 | 모드 | 판정 단위 |
|---|---|---|---|
| CAUSE-00 | 원인 분류 체계가 한 곳에서 정의되고 리포트·Human Alignment가 같은 목록을 읽는다 | 공통 | 정적 검사 + 단위 |
| CAUSE-01 | 현재 고정 분류의 출력(입력 부담·탐색 부담·늦은 배송비)을 기준선으로 저장하고, 자동 도출이 같은 fixture에서 기준선 후보를 재현한다 | local | 후보 비교 |
| CAUSE-02 | 모든 후보에 근거(단계·캡처·신호값)가 연결되고, 근거가 없으면 후보를 만들지 않는다 | 공통 | 스키마 검사 |
| CAUSE-03 | 원인을 아는 fixture에서 심어둔 원인의 후보 재현율·정밀도가 기준 이상이다 | local | 정답 라벨 비교 |
| CAUSE-04 | 근거가 부족하거나 후보가 없으면 `unknown`으로 남고 임의 원인을 만들지 않는다 | 공통 | 종료 값 |
| CAUSE-05 | 비용 요소 감지가 고정 문구가 아니라 금액·수수료 패턴으로 동작하고, 문구가 다른 fixture에서도 감지된다 | 공통 | fixture 변형 |
| CAUSE-06 | 정상(문제 없음) fixture에서 후보가 만들어지지 않는다 (오탐률) | local | 오탐 건수 |
| CAUSE-07 | 기술 실패(agent_failure·site_failure·budget_exceeded·cancelled·blocked_auth)가 UX 원인 후보에 섞이지 않는다 | 공통 | 종료 상태 분리 |
| CAUSE-08 | 근거 캡처가 없으면 최종 화면으로 대체하지 않고 누락을 표시한다 | 공통 | 근거 상태 |
| CAUSE-09 | local 후보에는 근거 코드 위치(파일:줄)가 붙고, 코드 근거는 후보를 뒷받침하는 정보로만 표시되며 원인 확정으로 표현되지 않는다 | local | 스키마·문구 검사 |
| CAUSE-10 | 같은 세션을 N회 분석했을 때 후보 일치율이 기준 이상이다 | 공통 | 반복 실행 비교 |
| CAUSE-11 | 후보·정답 원인이 행동 모델(페르소나) 입력에 포함되지 않는다 | 공통 | 입력 payload 검사 |
| CAUSE-12 | 이슈 묶음(과업·단계·원인)이 정의에 맞게 생성되고 원인별 빈도의 합이 이탈 세션 수와 같지 않아도 된다는 규칙이 지켜진다 | 공통 | 집계 값 |
| CAUSE-13 | url 결과는 `verification=unverified`로 표시되고 사람 검수 전에는 확정되지 않는다 | url | 상태 값 |
| CAUSE-14 | Human Alignment 비교에서 원인 미상 분모 처리가 기존 규칙(데이터 없음=null·미검증)을 따른다 | 공통 | 지표 값 |
| CAUSE-15 | 개선 가설은 가설로 표시되고, 생성 목업·개선 문구가 실제 개선 효과의 증거로 쓰이지 않는다 | 공통 | 문구·표시 검사 |

## Fixture

| 이름 | 용도 | 상태 |
|---|---|---|
| 모의 쇼핑몰 문제 변형 (`signup`·`fee`·`cta`) | 원인을 아는 정답 라벨 | FE 체크아웃 필요, 현재 blocked |
| 정상(fixed) 변형 | 오탐 측정 | 같은 FE 필요 |
| 문구 변형 | 비용 문구·위치를 바꾼 fixture (CAUSE-05) | 미작성 |
| 세션 기록 샘플 | trace·캡처·점수 구성이 포함된 합성 세션 (실제 고객 대시보드 기록과 분리) | 미작성 |
| 복합 원인 | 한 세션에 원인이 둘 이상인 경우 | 미작성 |

fixture에는 실개인정보·실결제·운영 계정 자료를 넣지 않는다.

## 판정 규칙

- pass / fail / blocked 정의는 퍼널 하네스와 같다. 선행 조건 미충족은 blocked, 기준 위반(예: 정상 fixture에서 후보 생성)은 fail이다.
- 후보 정확도는 정답 라벨이 있는 fixture에서만 주장한다. 일반 사이트 진단 정확도로 확대하지 않는다.
- LLM을 쓰는 해석·문장화는 키·모델·호출 범위를 확인하고 명시적으로 실행한다. 고정 정책·SDK mock 결과와 구분한다.
- 후보 에이전트의 모델·프롬프트는 검증 에이전트에 공유하지 않는다.

## 증거

- 결과: `artifacts/harness/<실행 ID>/verdict.json` (제안: 프로필 `cause`, 아직 `scripts/verify_harness.py`에 없음), `execution.log`, 후보 산출물(후보·근거·신뢰도).
- 각 판정에 실행 시각, 명령 argv, Python 경로, 소스 SHA-256, 로그 경로를 남긴다.

## 증거 대응표 (제안 테스트명, 전부 미작성)

| ID | 제안 테스트 | 상태 |
|---|---|---|
| CAUSE-00 | `test_cause_taxonomy_single_source` | 미작성 |
| CAUSE-01 | `test_candidates_match_fixed_baseline` | 미작성 |
| CAUSE-02 | `test_candidate_requires_evidence` | 미작성 |
| CAUSE-03 | `test_recall_precision_on_labeled_fixtures` | 미작성 |
| CAUSE-04 | `test_unknown_when_evidence_insufficient` | 미작성 |
| CAUSE-05 | `test_cost_detection_without_fixed_phrase` | 미작성 |
| CAUSE-06 | `test_no_candidates_on_fixed_variant` | 미작성 |
| CAUSE-07 | `test_technical_failures_excluded` | 미작성 |
| CAUSE-08 | `test_missing_capture_is_flagged` | 미작성 |
| CAUSE-09 | `test_local_candidate_has_code_location` | 미작성 |
| CAUSE-10 | `test_candidate_consistency_across_runs` | 미작성 |
| CAUSE-11 | `test_candidates_not_in_persona_input` | 미작성 |
| CAUSE-12 | `test_issue_grouping_and_counts` | 미작성 |
| CAUSE-13 | `test_url_candidates_unverified` | 미작성 |
| CAUSE-14 | `test_alignment_denominators_unchanged` | 미작성 |
| CAUSE-15 | `test_improvement_hypothesis_labeled` | 미작성 |

## 아직 검증하지 않는 것

- 일반 웹사이트에서의 원인 후보 정확도, 시각적 CTA·숨은 비용의 자동 탐지 정확도, 사람 검수와의 일치도.
- 실제 고객이 같은 이유로 포기한다는 주장. 후보는 모델 가설이다.
- 좌절 모델 가중치·임계치의 보정 (별도 보정 작업).

## 인계 형식

요구사항 ID, 실행 명령, verdict 경로, 소스 fingerprint, pass·fail·blocked 건수, 후보 산출물 위치, 미실행·미검증 항목, 확정되지 않은 분류 체계 항목을 전달한다.
