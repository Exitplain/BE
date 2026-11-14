# 에이전트 수정 내역 

- 브랜치: `b/agent-update`

## 한 줄 요약

페르소나 수치 4개·시드·화면 크기·요청 간격을 에이전트 입력에 추가하고, 이탈 판정을 그 수치로 계산하게 바꿨습니다. 화면마다 퍼널 7단계와 이탈 원인 7종을 기록합니다. **기본값(수치 50, 시드 0)이면 예전과 결과가 같습니다.**

## 커밋 순서

1. 에이전트 입력에 페르소나 수치 4개, 시드, 화면 크기, 행동 간격 칸 추가
2. 퍼널 7단계 매핑 추가 (`agent/funnel.py`)
3. 이탈 판정에 페르소나 수치·시드 반영, 화면 크기·요청 간격 적용, 화면마다 퍼널 단계 기록
4. 이탈 원인 7종 감지 추가
5. 동시 실행 상한 5, 세션별 토큰 상한 추가
6. 시드마다 페르소나 수치를 개인차만큼 다시 뽑기

## 파일별 변경

| 파일 | 변경 | 기존 코드 영향 |
|---|---|---|
| `agent/schemas.py` | `Persona`에 `patience`, `digital_literacy`, `price_sensitivity`, `trust_need`(0~100, 기본 50). `RunInput`에 `seed`(기본 0), `viewport_width/height`(기본 1000×800), `action_interval_seconds`(기본 0), `stage_paths`, `max_tokens_per_session`(기본 없음). `concurrency` 상한 4 → 5. `SessionResult`에 `terminal_stage`, `stages_reached` | 모두 기본값이 있어 예전 입력 그대로 동작 |
| `agent/funnel.py` (새 파일) | 화면 경로 → 7단계(랜딩·탐색·상세·장바구니·가입·결제 정보·완료). 순서: `stage_paths` 표 → 성공 경로 → 경로 단어 → 첫 화면은 랜딩, 모르면 탐색 | 없음 (`frustration.STAGES`는 그대로 둠) |
| `agent/behavior.py` (새 파일) | `PersonaMeter`: 화면마다 불만 점수 계산, 원인 7종 감지, 주 원인 선택 | 없음 |
| `agent/runtime.py` | 예전 규칙(필수 입력 초과 시 +70)을 `PersonaMeter`로 교체. 화면 크기·요청 간격·토큰 상한 적용. 이벤트에 단계·점수 추가 | 기본값이면 같은 결과 (기존 테스트 전부 통과) |
| `agent/persona_report.py` | `CATEGORIES`에 새 원인 4종 이름 추가 | 기존 3종 이름은 이 화면 테스트가 써서 그대로 둠 |
| `tests/` | `test_schema_params.py`, `test_funnel.py`, `test_behavior.py`, `test_limits.py` 추가 | — |

## 이탈 판정 (`agent/behavior.py`)

```
점수 = max(0, 0.85 × 이전 점수 + 이번 화면의 원인별 기여 − 4 × 새 단계 도착)
점수 ≥ 임계치 → 가상 이탈
임계치 = frustration_threshold × f(인내심),  f(x) = 0.5 + x/100  (0 → 0.5배, 50 → 1배, 100 → 1.5배)
```

| 원인 키 | 이름 (FE `causeNames`와 같음) | 감지 조건 | 기본 가중치 | 연결 수치 |
|---|---|---|---|---|
| `input_burden` | 입력 부담 | 필수 입력칸 > `max_required_fields` | 70 | 신뢰 요구 |
| `navigation` | 탐색 실패 | 같은 단계에서 스크롤·뒤로가기 등 반복(10), 앞 단계로 되돌아감(8) | 10 / 8 | 숙련도가 낮을수록 |
| `cost` | 비용 충격 | 앞 단계에서 안 보이던 배송비·수수료가 장바구니 이후 등장 | 40 | 가격 민감도 |
| `value` | 가치 불명확 | 상세 화면에 가격(…원)이 없음 | 20 | 가격 민감도 |
| `trust` | 신뢰 부족 | 결제 전 단계에서 연락처·생년월일 등 개인정보 입력칸 | 30 | 신뢰 요구 |
| `technical` | 기술 오류·지연 | 행동 후 3초 초과 또는 오류 문구 | 15 | 인내심이 낮을수록 |
| `accessibility` | 접근성 장벽 | 이름 없는 입력칸, 모바일(≤480)에서 24px 미만 버튼이 2개 이상 | 20 | 숙련도가 낮을수록 |

- `navigation` 외의 원인은 세션마다 처음 한 번만 점수에 들어갑니다.
- 주 원인(`primary: true`) = 이탈 시점까지 점수에 가장 많이 기여한 원인.

## 시드

- 0이면 흔들림 없음 (예전과 같음).
- 1 이상이면 `(seed, 페르소나 ID)`로 정해지는 난수로 ① 수치 4개를 원래 값 중심으로 다시 뽑고(표준편차 12), ② 임계치와 가중치를 ±15% 흔듭니다.
- 데모 쇼핑몰 20명 × 시드 3번 결과: 주문 버튼 문제 17/19/20명 완료, 배송비 문제 11/13/11명 완료. 회원가입 문제는 입력 부담이 커서 시드와 관계없이 모두 이탈.
- LLM 정책(`--policy openai`)에는 시드를 넘기지 않습니다. 판정 기준만 흔듭니다.

## 이벤트·결과에 추가된 칸

- `observation.created`: `stage`, `frustration_score`, `frustration_threshold`
- `friction.detected`: 원인 7종, `stage`, `contribution`
- `session.finished` / `result.json`: `terminal_stage`, `stages_reached`, `issues[].primary`
- 기존 칸의 이름과 모양은 바꾸지 않았습니다.

## 확인 방법 (BE 폴더에서)

```bash
python -m pytest tests/test_limits.py tests/test_behavior.py tests/test_funnel.py tests/test_schema_params.py tests/test_runtime.py -q
# 전체: FE 저장소가 BE 옆(../FE)에 있어야 쇼핑몰 테스트가 돌아감
python -m pytest -q
```

## 확인 부탁 

1. 가중치(입력 70, 비용 40, 탐색 10·8, 가치 20, 신뢰 30, 기술 15, 접근성 20), 감쇠 0.85, 새 단계 보상 4, 배율 공식 `0.5 + x/100`. 사람 데이터로 보정하지 않은 가설값입니다.
2. 새 원인 4종의 감지 규칙. 특히 "상세 화면에 가격이 없으면 가치 불명확"은 단순한 규칙입니다.
3. 퍼널 단어 목록(`funnel.KEYWORDS`). `/login`을 "가입" 단계로 넣었습니다.
4. 시드 흔들림 크기(±15%, 표준편차 12).
5. 비용 상한을 금액이 아닌 토큰 수로 넣었습니다. 금액으로 하려면 모델별 가격표가 필요합니다.
6. 숫자는 모두 파일 맨 위 상수(`WEIGHTS`, `JITTER`, `VARIABLE_SD`, `KEYWORDS` 등)라 바꾸기 쉽습니다.

## 하지 않은 것

- **테스트 계정 로그인 (요청 7번):** `browser.py`의 비밀번호 입력 차단, 프롬프트의 "never authenticate", `input.json`·이벤트에 비밀번호가 남지 않게 하는 처리가 필요해서 LLM 담당에게 넘깁니다. 11/18 전에 어려우면 이번 범위에서 뺍니다.
- LLM 정책에 시드·페르소나 수치를 프롬프트로 활용하는 것 (지금도 `persona.model_dump()`로 수치가 전달되기는 함).
- 금액 기준 비용 상한.

## 참고: 백엔드 쪽에서 같이 바꾼 것

- `backend/plans/run_input.py`: 위저드 실행 조건과 페르소나 수치를 새 칸에 채워 보냄.
- `backend/reports/`: 이벤트의 `stage`, `primary`를 읽어 퍼널 7단계, 원인 분포·Top 3, 시드 범위, 히트맵을 계산함 (`report-2`).
- `backend/personas/rules.py`: 규칙 기반 페르소나 생성기 (타겟 설명·연령으로 수치 조정). LLM 생성기가 생기면 `EXITPLAIN_PERSONA_GENERATOR` 설정으로 교체하면 됩니다.