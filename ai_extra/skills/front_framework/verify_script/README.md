# 프레임워크별 로컬 추출 검증 스크립트

규약은 [공통 README](../../../verify_script/README.md)를 따른다 (종료 코드 0/1/2, 결정적, 읽기 전용, 기준값은 인자). 대상은 타겟 소스 코드를 읽는 local 스킬의 추출 결과다. **전부 미작성이다.**

## 흐름: 감지가 0단계

```
소스 경로 → [0] detect_framework → 프레임워크·라우터 종류·버전 판정
          → [1] 해당 규칙 문서(<fw>_local.md) 선택 → [2] 추출 → [3] 검증 스크립트
```

- 어떤 프레임워크인지는 사용자가 정하는 입력이 아니라 **감지 결과**다. 감지 결과가 이후 규칙·추출기를 고른다.
- 감지 출력: `framework`, `router`(예: react-router-v6, next-app, next-pages), `version`, `confidence`, 근거(파일:줄), 후보가 여럿이면 후보 목록.
- 모호하거나(모노레포·여러 프레임워크 혼재, 라우터 라이브러리 없음) 지원 목록에 없으면 추측하지 않고 blocked(`framework_unsupported` 또는 `framework_ambiguous`)로 남긴다. 이 경우 퍼널은 `needs_gui_check` 중심의 화면 탐색으로 넘어가는 폴백을 쓰되, local이 아니라 url 수준의 검증 상태(`unverified`)로 표시한다.
- 감지 단계에서 구분해야 할 것: Next.js `app/` vs `pages/` 혼재, React Router v5 vs v6 vs 데이터 라우터, Remix·Vite+React·CRA 같은 메타/번들러 조합, Nuxt vs Vue Router 단독, 라우터가 없는 SPA, 모노레포의 여러 앱.

## 구현 상태 (2026-11-15, 로컬 정적 추출 1차)

추출기·감지기·fixture 생성기는 `skills/front_framework/`에 있다 (`detect_framework.py`, `extract_routes.py`, `gen_fixture.py`, `fixtures/hand/`). 표준 라이브러리만 쓴다. 실행: `python3 -I ai_extra/skills/front_framework/verify_script/run_all.py [seeds]`, 결과는 `artifacts/ai_extra/<시각>/verdict.json`.

| 스크립트 | 상태 | 마지막 결과 (20 seeds) |
|---|---|---|
| `check_no_hardcoding.py` | 구현 (자체 테스트 포함) | pass |
| `check_detection.py` | 구현 (`detect_framework.py` 감지 검증 통합) | pass, 108 cases |
| `compare_extraction.py` | 구현 (`--min-pass-rate` 필수, 없으면 blocked) | pass, 605 cases (생성 600 + 손작성 5) |
| `check_metamorphic.py` | 구현 (이름 변경·페이지 추가·링크 절단·우회 경로·노이즈 페이지) | pass, 560 checks |
| `check_evidence_locations.py` | 구현 | pass, 2020 locations |
| `check_unresolved.py`, `check_readonly.py` | **미작성** | - |

검증 스위트의 감도는 추출기에 결함을 넣은 복사본으로 확인했다: 전역 간선을 페이지 간선으로 집계 → 실패 감지, 고정 단계 사전 삽입 → 하드코딩 검사가 감지, BFS를 DFS로 교체 → 실패 감지, 그룹 세그먼트 미제거 → 실패 감지. (BFS 이웃 순서 뒤집기는 결과가 같은 동치 변이라 통과한다.)

### 외부 오픈소스 앱 평가 (`check_external.py`)

정답은 각 앱 소스를 **추출기 출력을 보기 전에** 읽고 `fixtures/external_expected.json`에 적었다(앱 자체는 저장소에 복사하지 않고 URL·커밋만 기록). 정적 읽기만 하며 외부 코드를 설치·실행하지 않는다. 실행: `check_external.py --apps-root <source>=<checkout 경로> --min-route-recall .. --min-edge-precision .. --min-edge-recall ..` (기준값 인자 필수).

| 실행 | 대상 | 추출기 | 기준(사전 등록) | 결과 |
|---|---|---|---|---|
| run1 (`results/external_reserve_run1_frozen_469b19bc.json`) | 읽지 않은 React Router 예제 7개 | 동결 469b19bc | 경로 재현율 ≥.95, 간선 정밀도 ≥.90, 간선 재현율 ≥.80 | **FAIL**: 경로 재현율 .917, 간선 정밀도 .457, 간선 재현율 .571, 정확 일치 3/7 |
| (수정) | run1 실패 원인 4가지를 고침: 하위 라우트가 있는 부모 페이지 누락, loader/action redirect 미귀속, lazy 다중 컴포넌트 파일의 가짜 간선, 상대 템플릿 링크 | | | 이 7개는 이후 dev로 재분류 |
| run2 (`results/external_reserve_run2_frozen_8c49defd.json`) | 읽지 않은 Next.js 예제 6개 | 동결 8c49defd | 같음 | **PASS**: 경로 재현율·정밀도 1.0, 간선 정밀도 1.0, 간선 재현율 .861, 정확 일치 4/6 |
| (수정) | run2 실패 원인 1가지를 고침: 파일 기반 라우트는 페이지 파일 전체가 범위 (보조 컴포넌트의 redirect 귀속) | | | 이 6개도 dev로 재분류 |
| 현재 | RR 15개 + Next 6개 = 21개 | 95dac450 | 같음 | 21개 모두 정확 일치, 회귀 스위트 전체 pass |

**주의: 마지막 "21/21 정확 일치"는 일반화의 증거가 아니다.** 21개 모두 실패 후 고치는 과정에서 읽은 앱이라 지금은 개발셋이다. 동결된 추출기로 처음 만난 앱에 대한 정직한 수치는 run1(FAIL)과 run2(PASS)뿐이고, 새 보류셋은 아직 없다.

**한계 (통과가 의미하지 않는 것)**
- 평가한 외부 앱은 React Router 예제 15개와 Next.js 예제 6개뿐이다. **Vue Router는 외부 앱으로 평가하지 않았다**(합성 fixture만). Vue playground는 스트레스 테스트 성격이라 정답을 만들지 않았다. React Router v5, Next `app`+`pages` 혼재, 라우트 없는 SPA도 외부 앱으로는 미평가다. `multi-app`은 정답이 모호해 제외했다.
- 정답은 한 사람이 소스를 읽고 적었다. 해석 차이(예: 하위 라우트를 가진 부모를 페이지로 볼지)로 기준이 흔들릴 수 있고, 일부는 평가 중 정의를 정했다.
- Next 간선 정답은 **페이지 파일 안에 적힌 이동만** 포함한다. 임포트한 컴포넌트를 거치는 링크는 추출기가 귀속하지 않으며 평가에서도 빠져 있다. 그래서 이 수치는 실제 페이지 간 이동의 재현율이 아니다.
- 실제 FE 체크아웃이 없어 이 저장소의 프론트로는 돌려보지 않았다.
- 추출기는 정규식·괄호 매칭 기반이고, 컬럼 0에서 시작하는 선언(prettier 스타일)을 가정한다. 다른 포맷의 코드에서는 범위 판정이 틀릴 수 있다. 괄호가 불균형한 문자열이 JSX 텍스트에 있으면 객체 라우트 파싱이 틀릴 수 있다.
- 동적 라우트·조건부 렌더링·런타임 계산 경로는 `unresolved`/`needs_gui_check`로 남기고 해석하지 않는다.
- GUI 확인(FUNNEL-03·04), 반복 일관성(FUNNEL-07), `other` 비율(FUNNEL-08)은 forward agent가 없어 미검증이다.
- 프로젝트 Python 환경이 이 머신에 없어 시스템 Python 3.13으로 실행했다 (verdict에 경로 기록).

## 구성

프레임워크마다 3가지를 세트로 둔다.

| 구성 | 내용 |
|---|---|
| 규칙 문서 `<fw>_local.md` | 라우트·이동·폼·분기를 어디서 어떻게 읽는지 |
| fixture 미니앱 + `expected.json` | 함정 케이스를 넣은 합성 앱과 정답 라우트·간선·필드 (FE 체크아웃 불필요) |
| 검증 스크립트 | 추출기 출력과 `expected.json` 비교 |

## 공통 스크립트

| 스크립트 | 판정 | 연결 ID | 상태 |
|---|---|---|---|
| `detect_framework.py` (검증 대상은 감지기, 아래 `check_detection.py`와 구분) | `package.json`·설정 파일·디렉터리 구조로 프레임워크·라우터·버전 판정, 미지원·모호는 blocked | FUNNEL-05 | 미작성 |
| `check_detection.py` | 프레임워크가 알려진 fixture 세트(Next app/pages 혼재, RR v5/v6, 라우터 없는 SPA, 모노레포 등)에서 감지 결과가 정답과 일치하고, 모호한 경우 추측하지 않고 blocked로 남는지 | FUNNEL-05 | 미작성 |
| `check_evidence_locations.py` | 추출 항목의 `파일:줄`이 실제로 존재하고 해당 줄에 주장한 구문이 있는지 | FUNNEL-02, CAUSE-09, PGEN-13 | 미작성 |
| `check_unresolved.py` | 동적 라우트·조건부 렌더링·모달이 `needs_gui_check`로 남고 조용히 확정되지 않는지 | FUNNEL-03 | 미작성 |
| `check_readonly.py` | 추출 전후 타겟 소스 해시 동일, 산출물에 `.env` 값·비밀값 없음 | 공통 원칙 | 미작성 |
| `compare_extraction.py` | 추출 라우트·간선·폼 필드·breakpoint vs `expected.json` (누락·초과 보고) | FUNNEL-01, PGEN-13 | 미작성 |

## 프레임워크별 fixture 함정

| 프레임워크 | 케이스 | 상태 |
|---|---|---|
| React Router | JSX `<Route>` vs `createBrowserRouter`, 중첩 라우트·`Outlet`, `lazy`, 인덱스 라우트, `navigate()` 이동, 인증 가드 리다이렉트 | 미작성 |
| Next.js | `app/` vs `pages/`, 라우트 그룹 `(group)`은 URL에 안 나옴, `[id]`·`[...slug]`, 병렬·가로채기 라우트, `middleware` 리다이렉트 | 미작성 |
| Vue Router | 라우트 배열, 네비게이션 가드, 이름 라우트 | 미작성 |
| 라우트 없는 SPA | URL 하나에서 state로 단계가 바뀌는 다단계 폼 (단계 수를 코드만으로 확정 불가 → `needs_gui_check`) | 미작성 |
| 공통(TypeScript) | 필수 입력: `required`·zod/yup·react-hook-form 규칙 / 반응형: CSS media query·Tailwind breakpoint / 조건부 노출 분기 | 미작성 |

## 미결정

- **파서 스택**: TS/TSX 정확도는 JS 쪽 파서(TypeScript 컴파일러 API·ts-morph·babel)가 높다. 저장소가 Python이라 추출기는 Node, 비교·판정은 Python으로 나누는 안과 Python tree-sitter 단독안이 있다. 결정 전에는 추출기를 만들지 않는다.
- **지원 목록의 범위**: 감지는 모든 프레임워크에 대해 하되, 추출 규칙·fixture는 어떤 프레임워크를 먼저 지원할지 정해야 한다. 이 선택은 사용자 입력이 아니라 "지원 목록"이라는 제품 범위 결정이다. 실제 FE의 프레임워크를 확인하지 못했다 (FE 체크아웃이 작업 위치에 없음). 감지기가 만들어지면 FE 체크아웃에 돌려 결과로 정할 수 있다.
