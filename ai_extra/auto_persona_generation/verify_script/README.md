# 페르소나 생성 검증 스크립트

규약은 [공통 README](../../verify_script/README.md)를 따른다 (종료 코드 0/1/2, 결정적, 읽기 전용, 기준값은 인자). 요구사항은 [HARNESS](../HARNESS.md)를 본다. **전부 미작성이다.**

| 스크립트 | 판정 | 연결 ID | FE·LLM | 상태 |
|---|---|---|---|---|
| `check_persona_input.py` | 변수 범위·합계·계정 수 상한·연령 규칙(any 단독)·빈 입력 | PGEN-02, 03, 09, 12 | 없음 | 미작성 |
| `check_repro.py` | 같은 입력·시드에서 같은 세그먼트, 가변 생성이 입력에 따라 달라지는지 | PGEN-01, 04 | 생성기만 | 미작성 |
| `check_context_only.py` | 연령·타겟 설명만 다른 입력 쌍에서 행동 변수가 바뀌지 않는지 | PGEN-05 | 생성기만 | 미작성 |
| `check_meter_mapping.py` | 변수→FrustrationMeter 변환 후 현재 3프로필의 임계치·민감도가 유지되는지 | PGEN-06 | 없음 | 미작성 |
| `check_revisions.py` | 확정 후 변경이 새 revision이고 과거 실행을 덮어쓰지 않는지 | PGEN-11 | 없음 | 미작성 |
| `check_assignment.py` | 동시 실행 한도·과업×세그먼트 할당·중복 실행 방지 | PGEN-15 | 없음 | 미작성 |

## 참고

- `check_meter_mapping.py`의 기준선은 현재 `PROFILES` 3종(quick 2/390, careful 4/1280, patient 6/1440)과 임계치 `max_required_fields × 16`이다. 변환 규칙을 한 곳으로 모으기 전에는 blocked다.
- 세션 격리(PGEN-07)는 브라우저 실행이 필요해서 기존 EXEC-01 방식의 pytest로 확인하며 이 폴더의 스크립트 범위가 아니다.
- 변수 척도·세그먼트 이름 생성 방식이 미확정(D04 P07)이라 척도 관련 검사는 확정 전까지 blocked(`scale_undefined`)다.
- 코드 기반 범위 제안(PGEN-13) 검증은 [front_framework/verify_script](../../skills/front_framework/verify_script/README.md)를 쓴다.
