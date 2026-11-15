# 페르소나 생성 자동화 하네스

[SPEC](SPEC.md)의 성공 기준을 실행 결과와 연결하는 검증 명세다. 형식·판정 규칙은 [harness/SPEC.md](../../harness/SPEC.md)를 따른다. **아래 항목은 전부 미구현·미실행이다.** 제안 테스트명은 아직 존재하지 않으며, 통과했다고 표시하지 않는다.

## 범위

| 구분 | 내용 |
|---|---|
| 자동화 대상 | 고정 3프로필(`PROFILES`: quick·careful·patient)과 프로필에서 직접 파생되는 임계치를 입력 기반 가변 세그먼트·변수로 대체 |
| local | 타겟 소스 코드에서 필수 입력 수 분포·반응형 breakpoint·로그인 필요 여부를 읽어 변수 범위 제안 |
| url | 코드 없이 정찰 결과의 필수 입력 수 분포를 범위 제안에만 참고 |
| 범위 밖 | 실제 고객 비율 예측, 연령·인구통계 기반 행동 보정, 모델 보정 |

## 선행 조건 (blocked 판정 기준)

- 변수 척도와 세그먼트 이름 생성 방식이 미확정이다 (Figma D04 P07: 척도 0~100 예시). 미확정 상태에서는 범위·이름 관련 항목(PGEN-02, PGEN-09)을 blocked(`scale_undefined`)로 기록한다.
- 현재 `FrustrationMeter`는 `max_required_fields × 16`으로 임계치를 직접 만든다. 변수→입력 변환 규칙을 한 곳으로 모으기 전에는 PGEN-06을 판정하지 않는다.
- 실행 기록 비교용 기준선은 현재 3프로필 실행 결과다. 기준선이 없으면 회귀 판정은 blocked다.
- 코드 기반 범위 제안(local)에는 읽을 수 있는 소스 fixture가 필요하다. FE 체크아웃이 없으면 blocked다.
- LLM 호출이 필요한 항목은 키·모델·호출 범위를 확인한 뒤 실행한다. 키가 없으면 blocked를 보고하고 모델 없는 항목은 계속 진행한다.

## 요구사항

| ID | 요구사항 | 모드 | 판정 단위 |
|---|---|---|---|
| PGEN-01 | 프로필 수·이름·변수값이 입력(타겟 설명·인원·과업)에서 생성되고 입력에 따라 달라진다 (고정 3종 아님) | 공통 | 서로 다른 입력의 결과 비교 |
| PGEN-02 | 생성된 변수가 스키마가 허용한 범위 안에 있고, 범위 밖 값은 거부된다 | 공통 | 입력 검증 |
| PGEN-03 | 세그먼트 합계가 확정 인원과 일치하고 불일치는 오류로 표시된다 | 공통 | 합계 비교 |
| PGEN-04 | 같은 입력·시드에서 같은 세그먼트가 재현된다 | 공통 | 반복 실행 비교 |
| PGEN-05 | 연령·타겟 설명이 행동 변수를 직접 바꾸지 않는다 (문맥 전용) | 공통 | 입력만 다른 쌍의 변수 비교 |
| PGEN-06 | 변수→FrustrationMeter 입력 변환이 한 곳에 있고, 현재 3프로필을 그 변환으로 표현했을 때 기존 임계치·민감도가 유지된다 | 공통 | 값 비교 |
| PGEN-07 | 세션 간 브라우저·기억·출력 격리가 가변 세그먼트에서도 유지된다 | 공통 | 세션별 외부 관찰 |
| PGEN-08 | 정찰 결과·문제 정답·원인 후보가 페르소나 입력에 포함되지 않는다 | 공통 | 입력 payload 검사 |
| PGEN-09 | 로그인 필요 테스트에서 세그먼트 인원이 등록된 테스트 계정 수를 넘지 않는다 | 공통 | 상한 검증 |
| PGEN-10 | 보정 전 비율은 실제 고객 비율이 아니라 가정으로 표시된다 | 공통 | 표시·응답 값 |
| PGEN-11 | 생성 결과는 revision으로 저장되고 확정 후 변경은 새 revision이며 과거 실행·리포트를 덮어쓰지 않는다 | 공통 | 저장 상태 |
| PGEN-12 | 총 인원·타겟 설명 길이 상한·연령 값 규칙(any는 단독)·빈 입력을 검증한다 | 공통 | 입력 검증 |
| PGEN-13 | local 범위 제안이 코드에서 읽은 필수 입력 수·breakpoint·인증 필요 여부를 근거(파일:줄)와 함께 제안하고, 제안은 페르소나 행동에 직접 주입되지 않는다 | local | 제안 산출물 |
| PGEN-14 | url 모드는 코드 근거 없이 범위 제안을 unverified로 표시한다 | url | 상태 값 |
| PGEN-15 | 동시 실행 한도·세션 할당(과업×세그먼트)이 가변 세그먼트에서도 지켜지고 중복 실행이 막힌다 | 공통 | 세션 할당·동시 수 |
| PGEN-16 | 기술 실패·인증 차단이 세그먼트별 가상 이탈 통계에 섞이지 않는다 | 공통 | 종료 상태 분리 |

## Fixture

| 이름 | 용도 | 상태 |
|---|---|---|
| 현재 3프로필 실행 결과 | 회귀 기준선 (PGEN-06) | 실행 기록 보존 필요, 미확보 |
| 입력 변형 쌍 | 타겟 설명·연령만 다른 입력 (PGEN-05) | 미작성 |
| 범위 밖 입력 | 변수 범위·합계·계정 수 초과·빈 입력 | 미작성 |
| 모의 쇼핑몰 코드 | 필수 입력 수·breakpoint 추출 대상 (PGEN-13) | FE 체크아웃 필요, 현재 blocked |
| 합성 타겟 설명 | 실개인정보가 없는 가상 문장 | 미작성 |

fixture에는 실개인정보·실결제·운영 계정 자료를 넣지 않는다. 타겟 설명에 실제 고객 데이터를 쓰지 않는다.

## 판정 규칙

- pass / fail / blocked 정의는 퍼널·원인 하네스와 같다.
- 생성된 행동이 "그럴듯한지"는 정답 기준으로 평가하지 않는다. 검증은 스키마·합계·범위·재현성·격리·주입 차단만 판정한다.
- 세그먼트가 실제 고객 집단을 대표한다는 주장은 하지 않는다 (보정·실측 비교는 별도 검증).
- 이름·설명 생성의 LLM 호출은 명시적으로 실행하고, 고정 정책·SDK mock 결과와 구분해 기록한다.

## 증거

- 결과: `artifacts/harness/<실행 ID>/verdict.json` (제안: 프로필 `persona_gen`, 아직 `scripts/verify_harness.py`에 없음), `execution.log`, 생성 세그먼트 산출물·revision.
- 각 판정에 실행 시각, 명령 argv, Python 경로, 소스 SHA-256, 로그 경로를 남긴다.
- 격리(PGEN-07)는 기존 EXEC-01 방식의 외부 관찰(입력·쿠키·localStorage 분리)로 확인한다.

## 증거 대응표 (제안 테스트명, 전부 미작성)

| ID | 제안 테스트 | 상태 |
|---|---|---|
| PGEN-01 | `test_profiles_generated_from_input` | 미작성 |
| PGEN-02 | `test_variable_range_validation` | 미작성 |
| PGEN-03 | `test_segment_sum_matches_total` | 미작성 |
| PGEN-04 | `test_generation_reproducible_with_seed` | 미작성 |
| PGEN-05 | `test_age_and_description_do_not_change_variables` | 미작성 |
| PGEN-06 | `test_variable_to_meter_mapping_preserves_baseline` | 미작성 |
| PGEN-07 | `test_isolation_with_variable_segments` | 미작성 |
| PGEN-08 | `test_discovery_outputs_not_in_persona_input` | 미작성 |
| PGEN-09 | `test_segments_capped_by_test_accounts` | 미작성 |
| PGEN-10 | `test_ratio_labeled_as_assumption_before_calibration` | 미작성 |
| PGEN-11 | `test_generation_revisions_immutable` | 미작성 |
| PGEN-12 | `test_input_validation_rules` | 미작성 |
| PGEN-13 | `test_local_range_suggestion_has_code_evidence` | 미작성 |
| PGEN-14 | `test_url_range_suggestion_unverified` | 미작성 |
| PGEN-15 | `test_concurrency_and_session_assignment` | 미작성 |
| PGEN-16 | `test_technical_failures_excluded_from_segment_stats` | 미작성 |

## 아직 검증하지 않는 것

- 생성된 페르소나가 실제 고객 행동·비율과 일치하는지, 연령·세그먼트별 전환율 예측.
- 모델 버전 간 생성 결과 비교, 비용·부하·동시성 확대.
- 사람 관측(Human Alignment)을 보정에 반영하는 방식 (D04 K04 확인 전까지 미반영).

## 인계 형식

요구사항 ID, 실행 명령, verdict 경로, 소스 fingerprint, pass·fail·blocked 건수, 세그먼트 revision 위치, 미실행·미검증 항목, 미정 척도(D04 P07)를 전달한다.
