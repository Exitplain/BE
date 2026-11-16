# 원인 후보 검증 스크립트

규약은 [공통 README](../../verify_script/README.md)를 따른다 (종료 코드 0/1/2, 결정적, 읽기 전용, 기준값은 인자). 요구사항은 [HARNESS](../HARNESS.md)를 본다. **전부 미작성이다.**

| 스크립트 | 판정 | 연결 ID | FE·LLM | 상태 |
|---|---|---|---|---|
| `check_taxonomy.py` | 리포트·Human Alignment가 같은 원인 목록을 쓰는지 | CAUSE-00 | 없음 | 미작성 |
| `compare_causes.py` | 후보 vs 정답 라벨 재현율·정밀도, 정상 fixture 오탐 (기준값은 인자) | CAUSE-01, 03, 06, 10 | 라벨 파일만 | 미작성 |
| `check_evidence.py` | 모든 후보에 근거(단계·캡처·신호값)가 있고 없으면 unknown인지 | CAUSE-02, 04, 08 | 없음 | 미작성 |
| `check_cost_detection.py` | 고정 문구 없이 금액·수수료 패턴으로 감지되는지 (문구 변형 fixture) | CAUSE-05 | 없음 | 미작성 |
| `check_tech_failures.py` | 기술 실패가 UX 후보에 섞이지 않는지 | CAUSE-07 | 없음 | 미작성 |
| `check_issue_grouping.py` | 과업·단계·원인 묶음과 집계 규칙 | CAUSE-12, 14 | 없음 | 미작성 |

## 참고

- `check_taxonomy.py`는 현재 코드에 바로 실행할 수 있다. 리포트 `CATEGORIES`는 3종, Human Alignment `CAUSES`는 9종이라 **현재는 fail이 예상된다** (아직 실행하지 않았다). "7종"은 목록이 정의돼 있지 않아 확정 전까지 blocked(`taxonomy_undefined`) 항목이다.
- 후보의 문장·해석 품질은 LLM 판단이라 스크립트로 정답 판정하지 않는다. 반복 일치율과 스키마만 본다.
- 코드 근거 위치 검사(CAUSE-09)는 [front_framework/verify_script](../../skills/front_framework/verify_script/README.md)의 `check_evidence_locations.py`를 쓴다.
