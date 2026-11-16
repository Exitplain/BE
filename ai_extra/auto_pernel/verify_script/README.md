# 퍼널 검증 스크립트

규약은 [공통 README](../../verify_script/README.md)를 따른다 (종료 코드 0/1/2, 결정적, 읽기 전용, 기준값은 인자). 요구사항은 [HARNESS](../HARNESS.md)를 본다. **전부 미작성이다.**

| 스크립트 | 판정 | 연결 ID | FE·LLM | 상태 |
|---|---|---|---|---|
| `check_stage_registry.py` | STAGES가 한 곳에서만 정의되고 리포트·Human Alignment·집계가 같은 목록을 읽는지 | FUNNEL-00 | 없음 | 미작성 |
| `compare_funnel.py` | 추출 퍼널 vs 정답의 재현율·정밀도·순서 (기준값은 인자) | FUNNEL-01, 06, 07, 08 | 정답 파일만 | 미작성 |
| `check_stage_ids.py` | revision 간 단계 ID 유지 | FUNNEL-13 | 없음 | 미작성 |
| `check_explore_log.py` | 탐색 로그에서 비가역 행동 0건, ExplorePolicy 한도 준수 | FUNNEL-09, 10 | 없음 | 미작성 |
| `check_terminal_states.py` | 로그인 벽=blocked, 깊이 한계=frontier, 완료로 세지 않음 | FUNNEL-11 | 없음 | 미작성 |
| `check_approval_state.py` | url 결과가 승인 전 unverified이고 revision으로 확정되지 않음 | FUNNEL-12, 16 | 없음 | 미작성 |

## 참고

- `check_stage_registry.py`는 현재 코드에 바로 실행할 수 있다. STAGES가 `agent/frustration.py`, `agent/persona_report.py`, `agent/human_alignment.py` 세 곳에 있어 **현재는 fail이 예상된다**. 이 결과를 자동화 전 기준선으로 기록하고, 레지스트리 통합 뒤 pass로 바뀌는지로 선행 리팩터링 완료를 판정한다 (예상일 뿐 아직 실행하지 않았다).
- 코드 추출 vs GUI 확인의 일치(FUNNEL-04)는 forward agent 실행 결과가 필요하다. 스크립트는 결과 비교까지만 한다.
- 프레임워크별 추출 검증은 [front_framework/verify_script](../../skills/front_framework/verify_script/README.md)에 둔다.
