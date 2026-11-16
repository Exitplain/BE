# develop 통합: 추출 결과 → 7단계(`stage_paths`)

develop의 `agent/funnel.py`는 Figma P12의 7단계를 고정 키로 갖고, 경로를 단계로 바꾸는 규칙은 (1) 백엔드가 준 `stage_paths` 표(가장 긴 접두사 우선) → (2) success_path → (3) **경로 단어 사전(KEYWORDS)** → (4) 첫 화면=landing, 나머지=browse 순서다. 이 폴더는 (3)의 하드코딩을 대체할 (1)의 입력을 대상 코드에서 만든다. 단계는 늘리지 않는다.

- `stage_roles.json`: 7개 키와 역할. develop `FUNNEL_KEYS`와 같아야 하며, 다르면 검증이 blocked.
- `to_stage_paths.py`: 추출 결과 → `stage_paths`(현재 계약에 들어가는 것), `stage_patterns`(`:id` 표기까지 포함한 전체), `collisions`, `details`(페이지별 근거 `basis`).
- `fixtures/shopflow_next/`: 손으로 쓴 쇼핑 흐름(구조 신호만 있음). 정답은 `expected.json`.
- `verify_script/check_stage_paths_contract.py`: develop의 실제 `agent/funnel.py`를 불러서 검증. `--develop-root`로 다른 체크아웃 지정 가능.

## 배정 규칙 (구조 신호, 단어 사전 없음)
진입 경로=landing, 성공 경로=complete, 비밀번호 입력이 있는 페이지=signup, 입력칸 3개 이상 폼=payment, 동적(param) 라우트=detail, 동적 라우트로 링크하는 정적 페이지=browse. 신호가 없는 경로 위 페이지는 순서로 채운다(`basis=ordinal`). 그 밖은 미배정으로 남겨 develop의 기존 규칙에 맡긴다. **cart는 구조 신호가 없어 신호로는 배정되지 않는다.**

## 검증 결과 (2026-11-15, develop fe72fac 기준)
`check_stage_paths_contract.py --seeds 15`: 1460건 pass. 포함: 손으로 쓴 쇼핑 fixture의 단계·`stage_paths`·충돌 정답 일치, 무작위 이름 합성 앱(4개 프레임워크 × 15 시드)에서 7개 키 이내·경로 순서 단조·진입/성공 고정·결정성, develop `funnel_stage()` 왕복, 조용한 오답 방지.
감도 확인(변이): develop이 단계 키를 바꾸면 **blocked**, 어댑터가 동적 라우트를 접두사로 잘못 내보내면 **fail**.

## 계약 한계와 이 브랜치의 변경 (`ai/automation`)
**원래 `stage_paths`는 경로 접두사만 매칭해서, 목록과 상세가 같은 접두사를 쓰면 표현할 수 없었다.** 예: `/products`=browse, `/products/[id]`=detail 이면 `/products/5`가 browse로 나왔다. 어댑터는 그 경우를 `collisions`로 표시하고 `stage_paths`에서 뺀다(조용히 틀리지 않음).

이 브랜치에서 `agent/funnel.py`를 다음과 같이 바꿨다 (스키마 검증은 그대로 통과 — 키가 `/`로 시작하고 값이 7개 키이면 됨):
- `stage_paths` 키에 `:param` 구간 허용, 가장 구체적인 패턴(구간 수, 고정 구간 수) 우선. 기존 접두사 규칙(`/`는 정확히 `/`만, 대소문자·끝 슬래시 무시)은 그대로.
- 경로 키워드 사전을 `LEGACY_WORDS`로 강등하고 `USE_LEGACY_WORDS`(기본 True)로 끌 수 있게 했다. **기본값을 True로 둔 이유**: 지금 끄면 데모 쇼핑몰이 `stage_paths` 없이 얻던 cart·signup·payment 단계가 전부 browse로 무너지고 develop 테스트가 깨진다. 끄려면 데모에도 `stage_paths`를 공급해야 한다.
- `tests/test_funnel.py`에 새 동작 테스트 5개 추가.
- 어댑터의 `stage_patterns`(`:id` 표기)는 이 구현으로 왕복 검증된다 (`SUPPORTS_STAGE_PATTERNS` 플래그).

## 한계 (통과가 의미하지 않는 것)
- 신호 규칙(비밀번호→signup 등)의 **의미 정확도는 손으로 쓴 fixture 1개에서만** 확인했다. 실제 쇼핑몰 코드에서의 정확도는 미검증이다.
- 합성 앱에는 폼·신호가 없어 그쪽 통과는 구조(순서·키 범위·결정성)만 의미한다.
- 정적 코드만 본다. 입력칸이 컴포넌트·서버 응답으로 렌더링되면 신호가 빠진다.
- 모든 결과는 `unverified`이며 GUI 확인 전 확정이 아니다. develop의 Django/에이전트 테스트는 이 환경(Python 3.13, pytest 없음)에서 돌리지 못했다.
- `RunInput`에 실제로 넣어 실행해 본 것은 아니다. `agent/funnel.py`의 함수와 계약 조건을 재현해 검증했다.
