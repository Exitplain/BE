# ai_extra

현재 `agent/`에 하드코딩된 판단을 탐색·생성 기반으로 바꾸기 위한 명세 폴더다. 이 폴더의 문서는 **명세**이며 등록·활성화된 스킬·서브에이전트·자동 실행이 아니다.

| 폴더 | 자동화 대상 | 상태 |
|---|---|---|
| auto_pernel | 퍼널 단계 (고정 STAGES·과업) | [SPEC](auto_pernel/SPEC.md) · [HARNESS](auto_pernel/HARNESS.md) 작성, 구현·검증 없음 (FUNNEL-00~16) |
| auto_causal_candidation | 원인 후보 (고정 3종 분류) | [SPEC](auto_causal_candidation/SPEC.md) · [HARNESS](auto_causal_candidation/HARNESS.md) 작성, 구현·검증 없음 (CAUSE-00~15) |
| auto_persona_generation | 페르소나 생성 (고정 3프로필) | [SPEC](auto_persona_generation/SPEC.md) · [HARNESS](auto_persona_generation/HARNESS.md) 작성, 구현·검증 없음 (PGEN-01~16) |

HARNESS 문서의 테스트명은 제안이며 아직 작성되지 않았다. `scripts/verify_harness.py`에는 이 영역의 프로필이 없다.

- 로컬/URL 구분: **local = 타겟 소스 코드를 읽을 수 있음, url = 화면만 볼 수 있음.** 자동 테스트·검증 fixture는 별도로 우리 모의 사이트로 제한한다.
- 작성 지침과 SPEC 템플릿: [explain_skill_creator.md](explain_skill_creator.md)
- 스킬 파일은 `skills/`에 `_local.md` / `_url.md`로 나눠 둔다 (아직 없음).
- 폴더명 `auto_pernel`, `auto_causal_candidation`은 오타 가능성이 있어 확인 후 정리한다.

미결 항목은 각 SPEC의 성공 기준과 `Figma 문서 D04 확인 필요 목록`을 따른다.
