# 공통 검증 스크립트

영역 공통으로 쓰는 결정적 검증 스크립트를 둔다. 영역별 스크립트는 각 영역 폴더의 `verify_script/`에 둔다.

| 위치 | 대상 |
|---|---|
| `ai_extra/verify_script/` | 영역 공통 (이 폴더) |
| `ai_extra/auto_pernel/verify_script/` | 퍼널 (FUNNEL-xx) |
| `ai_extra/auto_causal_candidation/verify_script/` | 원인 후보 (CAUSE-xx) |
| `ai_extra/auto_persona_generation/verify_script/` | 페르소나 생성 (PGEN-xx) |
| `ai_extra/skills/front_framework/verify_script/` | 프레임워크별 로컬 추출 (FUNNEL-02·03·05, CAUSE-09, PGEN-13) |

## 규약 (모든 verify_script 공통)

- 종료 코드: **0=pass, 1=fail, 2=blocked**. `scripts/verify_harness.py`와 같다. 키 없음·선행 조건 미충족·입력 파일 없음·기준값 미정은 blocked이고 성공으로 바꾸지 않는다.
- 출력: 판정 JSON(`verdict.json` 형식)에 요구사항 ID, 판정, 사유, 근거 위치, 실행 시각, 명령 argv, Python 경로, 소스 SHA-256을 남긴다.
- 결정적이어야 한다: LLM·네트워크·실사이트 접근을 하지 않는다. LLM 판단이 필요한 항목은 스크립트가 판정하지 않고 일관성·스키마만 본다.
- 기준값은 코드에 박지 않고 인자로 받는다. 인자가 없으면 blocked(`threshold_undefined`).
- 읽기 전용: 검사 대상 소스·산출물을 수정하지 않는다.
- 입력은 fixture·합성 데이터·정제한 스냅샷으로 제한하고, 실개인정보·실결제·운영 계정 자료를 넣지 않는다.
- 실행은 프로젝트 Python으로 하고 결과 경로를 기록한다.

## 계획 (전부 미작성)

| 스크립트 | 판정 | 연결 ID | 상태 |
|---|---|---|---|
| `check_schema.py` | 산출물에 `mode`·`verification`·근거 필드가 있는지 | FUNNEL-15, CAUSE-02 | 미작성 |
| `check_no_leak.py` | 페르소나 입력 payload에 퍼널·후보·정답이 없는지 | FUNNEL-14, CAUSE-11, PGEN-08 | 미작성 |
