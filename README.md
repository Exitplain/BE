# Exitplain BE

[API 명세서](docs/API_SPEC.md) · [Figma UI 반영 내역](https://github.com/Exitplain/FE/blob/main/docs/FIGMA_IMPLEMENTATION.md)


기존 `backend/` Django 프로젝트를 보존하고 DropSpot Python 에이전트·테스트·하네스를 이관했습니다.
화면과 정적 자산은 [Exitplain/FE](https://github.com/Exitplain/FE)에 있습니다.
원본: [Jaehyeon-kr/dropspot_](https://github.com/Jaehyeon-kr/dropspot_/tree/c52730f5413cfeffb8b28b82b3eaa033a8a99b58), 커밋 `c52730f5413cfeffb8b28b82b3eaa033a8a99b58`의 스냅샷.

## 분리 저장소 로컬 실행

두 저장소를 같은 상위 폴더 아래 `BE`, `FE`로 클론합니다.

```bash
git clone https://github.com/Exitplain/BE.git BE
git clone https://github.com/Exitplain/FE.git FE
cd BE
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[model,test]'
python -m playwright install chromium
python -m agent.console --port 4174
```

SaaS 화면: http://localhost:4174. 데모 쇼핑몰은 별도 터미널에서 BE 가상환경으로 `python -m agent.shop --port 4173`을 실행합니다.
FE 폴더 위치가 다르면 실행 전에 `export DROPSPOT_FE_ROOT=/absolute/path/to/FE`를 설정합니다.
정적 파일은 FE에서 읽고, API는 BE의 개발 어댑터가 같은 origin으로 제공합니다.
Django와의 운영 API 통합은 원본과 마찬가지로 후속 작업입니다.

검증: `python scripts/verify_harness.py --profile browser`.
기존 `requirements.lock`은 원본 기록이며 현재 `pyproject.toml`과 버전 불일치가 있어 위 설치 명령을 사용합니다.

## 원본 구현 안내

아래 원본 안내에서 `saas-web/`, `demo-shop/` 경로는 이제 `../FE/` 아래에 있습니다.

# DropSpot

배포된 웹서비스를 여러 페르소나의 브라우저로 점검하고, 문제가 생긴 위치와 실행 근거를 리포트로 전달하는 서비스의 **에이전트 모듈 + 임시 SaaS 프론트**입니다.

Django 백엔드는 별도 구현을 전제로 합니다. 이 저장소의 Python HTTP 서버는 화면과 에이전트를 연결하는 **로컬 개발용 어댑터**입니다. 현재 검증 대상은 합성 데이터로 만든 ‘반찬로드’ 쇼핑몰입니다.

> 에이전트 실행·근거 수집·로컬 리포트의 기반은 구현했습니다. 하지만 운영 제품이 되려면 Django API 연결 외에도 임의 사이트 대응, 실제 모델 실행 검증, 작업 큐·인증·저장소, 사람 관측을 통한 보정이 필요합니다. API 키를 넣는 것만으로 현재 대시보드가 범용 GUI 에이전트로 전환되지는 않습니다.

## 상세 기능명세와 개발 설계

입력은 **URL만 제공 / 코드 접근도 제공** 두 가지로 구분합니다. React·Flutter 등 기술명과 실행 환경(web/native), 관측 방식(DOM/visual)은 별도 속성으로 관리합니다. 아래는 **구현 전 제품 설계**이며 현재 완료 기능과 구분합니다.

- [기능명세서](기능명세서.md): 두 입력 방식, 사용자 흐름, 기능별 FE/BE 책임·예외·수용 시나리오, P0/P1/P2.
- [개발 백로그](docs/개발_백로그.md): FE 24개·BE 28개 작업과 에이전트/PO 작업, 의존성과 인수 기준.
- [API·데이터 계약](docs/API_및_데이터_계약.md): 제안 API, 불변 계획/실행 snapshot, 비동기 작업·상태·오류·멱등성.
- [ERD·성능 설계](docs/ERD_및_성능설계.md): 관계/제약·인덱스, 캐시 키/무효화/격리, 메모리/스트림, 모델 문맥·토큰/비용 예산, 측정 기준.

기존 역할분담 문서는 초기 논의 기록입니다. 새 제품 개발 범위는 상세 명세를 우선하며 실제 실행 계약은 `agent/schemas.py`, 현재 브라우저 검증은 `harness/SPEC.md`를 기준으로 합니다. 제안 API/ERD를 현재 Django 구현이나 운영 성능으로 설명하지 않습니다.

## 현재 상태

| 구분 | 구현된 것 | 남아 있는 경계 |
|---|---|---|
| 브라우저 실행 | Playwright 관찰·행동·스크린샷, 독립 세션, 취소·시간/행동 한도, 종료 상태 분리 | 임의 사이트의 성공 판정·안전한 행동 범위는 사이트별 연결 필요 |
| 행동 도구 | click, fill, scroll, hover, select, check/uncheck, press, wait, back/forward, reload, stop | 새 탭·iframe 제어, 데스크톱 전체 조작 미지원 |
| 페르소나 | 3개 프로필의 화면 폭·입력 허용치·탐색/비용 민감도 | 고정 프로필과 규칙이며 실제 사람 행동으로 보정하지 않음 |
| 과업 | 첫 주문, 배송 지역 확인, 쿠폰으로 주문, 배송 일정 변경, 장바구니 담기, 쿠폰 받기 | 반찬로드 전용 정책. 새 사이트의 과업을 자동 생성·수행하는 범용 기능 아님 |
| 실행 준비 | 실제 로컬 화면 읽기 → 기능 확인 → 과업 선택/추가/제외 → 연령·문맥 확인 → 최종 실행 | 추가 자유 입력은 검토 메모. 연령은 문맥이며 행동 가중치를 바꾸지 않음 |
| GUI 관찰 | Skyvern 브라우저 API·공식 UI 연결, 실제 화면과 마우스 움직임, 종료 후 마지막 캡처 | 로컬 설치 필요. 고객별 스트림 인증·배포 프록시 미구현 |
| 좌절 누적 | 필수 입력·재방문·탐색 스크롤·늦은 배송비의 관측값과 설정 가중치 기록 | 초기 가설. 실제 이탈 확률이나 고객 감정으로 해석할 수 없음 |
| 세션 상세 | 카드 팝업, 행동 기록, 종료 이유 자동 표시, 추가 질문·근거 링크·답변 저장 | 질문 답변은 기록 기반 규칙. 대화 모델이나 비공개 사고 과정의 설명 아님 |
| 리포트 | 과업/단계/원인별 그룹, 기술 실패 분리, 당시 캡처·DOM bbox, Markdown·PDF 다운로드 | 이슈의 배포 간 동일성·해결 확인·자동 공유는 미구현 |
| 개선 목업 | 가입 화면용 AI 생성 예시 1장, 실제 근거와 별도 표시 | 문제별 자동 이미지 생성이나 개선 효과 검증 아님 |
| Human Alignment | 실제 참여자 관측 수동 입력·중복 방지·결과/단계/원인 일치도 계산 | 실제 사람 데이터 수집 및 검증 연구가 필요. 기본 실제 관측 0건 |
| 모델 어댑터 | CLI에서 OpenAI 구조화 행동 정책 호출 경로, 키 없음/잘못된 출력 오류 처리 | SDK mock만 검증. 실제 모델의 GUI 과업 성능·비용 미검증, 페르소나 대시보드에는 미연결 |
| 반복 점검 | 실행 기록 저장·이전 리포트 조회·샘플 조건 비교 | 주기 실행·배포 훅·신규/악화/해결 판정·운영 알림 미구현 |
| 하네스 | SPEC, SKILL, SUBAGENTS, WORKFLOW, HOOK, CHECKLIST, 검증 스크립트 | 문서만으로 자동 실행되지 않음. CI·배포 훅 등록 별도 |

### 임시 프론트에서 볼 수 있는 것

- 흰색 macOS 스타일의 SaaS 화면, 좁은 사이드바, 고객 화면과 운영 화면 구분.
- 실제 페르소나 카드·필터·실행 전 질문·확대 팝업·문제 근거 리포트.
- `#overview`의 **50개 타일은 UI 재생**입니다. 브라우저 50개가 실제로 실행되는 기능이 아닙니다.
- `#personas`는 실제 브라우저를 사용합니다. 기본 4과업 × 3프로필 = **12세션**, 최대 동시 **3세션**입니다. 6개 과업을 모두 선택하면 총 18세션을 순차 처리합니다.
- 사이트 등록·워크스페이스 설정은 `localStorage`, 실행 근거는 로컬 파일입니다. 운영용 계정·DB·권한 검사는 없습니다.
- `#admin`은 화면만 분리돼 있습니다. 관리자 접근 제어가 구현된 상태가 아닙니다.
- 테스트 쇼핑몰의 주문·쿠폰·구독은 합성 데이터입니다. 실제 결제·배송·계정 변경이 없습니다.

## 설치와 실행

Python 3.11 이상, Playwright Chromium이 필요합니다.

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[model,test]'
python -m playwright install chromium
# Linux에서 시스템 의존성이 부족하면: python -m playwright install --with-deps chromium
```

```bash
python -m agent.console --port 4174
# SaaS: http://localhost:4174
# 실제 페르소나: http://localhost:4174/#personas
# 리포트: http://localhost:4174/#report
```

쇼핑몰을 직접 둘러보려면 별도 터미널에서 실행합니다.

```bash
python -m agent.shop --port 4173
# http://localhost:4173/lab
```

대시보드의 라이브 페르소나 실행에는 **별도 Skyvern 설치**가 필요합니다. 저장소에 Skyvern 서비스·DB·인증키는 포함하지 않았습니다. 기존 설치 관리 및 구조는 [Skyvern 연동 안내](integrations/skyvern/README.md)를 참고하세요. 관리 스크립트는 새 머신용 설치 프로그램이 아닙니다.

Skyvern 없이도 핵심 브라우저 실행과 자동 테스트는 확인할 수 있습니다.

```bash
python -m agent.cli --shop fixed
python -m agent.cli --shop problem --case signup
python scripts/verify_harness.py --profile browser
```

### PDF 리포트

완료된 실행의 리포트에서 **PDF 내보내기**를 누릅니다. 선택한 실행의 요약·과업 결과·문제 화면/박스·개선 가설·세션별 근거·한계를 A4 PDF로 내려받습니다. 접힌 세션 상세도 PDF에는 포함합니다.

- `GET /api/personas/report.pdf?run_id=<완료된 실행 ID>`
- 서버의 Chromium이 기존 리포트를 인쇄합니다. 별도 PDF API 키는 필요 없습니다.
- 실행 중인 결과는 내보내지 않으며, 과거 완료 실행은 ID로 선택할 수 있습니다.
- 로컬 서버는 동시 PDF 생성 1건으로 제한합니다. 운영 환경에서는 백그라운드 생성·캐시·보존 기간을 연결해야 합니다.

## 에이전트와 Django 연결

### 1. 핵심 실행기를 워커에서 호출

[RunInput 계약](agent/schemas.py), [연동 예시와 이벤트 규약](agent/README.md)을 기준으로 연결합니다.

```python
from agent import RunInput, execute_run
from agent.policies import OpenAIPolicy

config = RunInput.model_validate(validated_payload)
result = await execute_run(
    config,
    policy_factory=lambda: OpenAIPolicy(model=model_id),
    output_dir=artifact_root,
    emit_event=save_event,
    should_cancel=is_cancelled,
)
```

이 경로는 핵심 실행기입니다. 대시보드 전용 `PersonaDashboard`의 좌절 그래프·과업별 리포트 스키마와 동일하지 않습니다. **두 경로를 운영용 Run/Session/Observation 스키마로 통합하는 작업**이 남아 있습니다. Django 요청 처리 안에서 장시간 실행하지 말고 워커 작업으로 분리합니다.

- Django: 소유권·허용 대상·과업·정책을 확정하고 작업 생성, 큐 전달, 상태/이벤트 저장, 파일 업로드·권한 URL, 취소를 담당.
- 에이전트: 브라우저 실행, 관측·행동·종료 사유·근거 반환. 이벤트 중복 제거 키는 `event_id`, 순서는 attempt 전체의 `sequence`.
- 프론트: 작업 ID를 받아 상태·세션·근거·리포트를 표시. 브라우저 공급자 키나 로컬 경로를 받지 않음.
- 종료 상태: 목표 도달, 가상 이탈, 기술 실패, 인증 차단, 취소, 한도 초과를 구분. 기술 실패를 이탈률에 넣지 않음.

### 2. 현재 로컬 HTTP API

아래 API는 **운영 Django API가 아닌** `agent.console`의 개발 어댑터입니다. 프론트 연동 형식을 확인하는 용도로 사용합니다.

| 메서드 | 경로 | 동작 |
|---|---|---|
| POST | `/api/personas/discover` | `{ "variant": "fixed" }`로 로컬 기능·제안 과업 탐색 |
| POST | `/api/personas/confirm` | 계획 ID·확인 기능·과업·타겟을 받아 실행 시작 |
| GET | `/api/personas` | 현재 상태·프로필·세션·행동·관측·근거 ID·일치도 |
| POST | `/api/personas/explain` | 실행 ID·세션 ID·질문·선택 행동 번호에 대한 기록 기반 답변 |
| POST | `/api/personas/human` | 실제 사람의 수동 관측 저장 |
| GET | `/api/personas/reports` | 완료 리포트 이력 |
| GET | `/api/personas/report?run_id=...` | 리포트 JSON |
| GET | `/api/personas/report.md?run_id=...` | Markdown |
| GET | `/api/personas/report.pdf?run_id=...` | PDF 다운로드 |
| GET | `/api/artifacts/<id>` | 로컬 PNG 근거 조회 |

확인 요청 예시(탐색 응답의 `plan_id` 사용):

```json
{
  "plan_id": "<탐색 응답 ID>",
  "feature_ids": ["shopping"],
  "task_ids": ["order", "cart"],
  "ages": ["20s", "30s"],
  "context": "모바일로 처음 방문하는 고객",
  "additional_request": "추가 점검 검토 메모"
}
```

`/api/personas/start`와 `/api/runs`는 기존 데모 호환 경로입니다. 새 고객 흐름은 탐색/확인 API를 사용합니다. 임의 URL 입력이나 고객별 데이터 접근은 이 API로 지원하지 않습니다. 현재 갱신은 polling이며 SSE/WebSocket 제품 API는 미구현입니다.

## 다음 담당자별 작업

### Django 백엔드

| 우선순위 | 작업 | 완료 기준 |
|---|---|---|
| P0 | 사용자·워크스페이스·프로젝트·권한 | 고객끼리 실행·근거·리포트를 조회할 수 없고 관리자 권한도 검증 |
| P0 | Run/Attempt/Session/Observation/Issue/Report DB 모델 | 재시작 후 상태·이력 복구, 두 실행 경로의 스키마 통합 |
| P0 | Celery 등 작업 큐, 취소·heartbeat·실패 복구 | HTTP와 실행 분리, 워커 종료 감지, 중복 요청/재시도 정책 |
| P0 | 객체 저장소·근거 접근·보존 정책 | PNG/PDF를 권한 있는 URL로 제공하고 수명/삭제 처리 |
| P0 | 고객 대상 연결과 세션 경계 | 소유 확인, 허용 URL·네트워크·변경 행동 제한, 계정/스트림 소유권 |
| P0 | API 계약·오류/상태·프론트 갱신 | OpenAPI 확정, 현재 로컬 어댑터 교체, 인증된 polling 또는 SSE |
| P1 | PDF 백그라운드 생성·캐시 | 실행별 동일 리포트 재사용, 실패 재시도와 다운로드 권한 |
| P1 | 주기 실행·배포 훅·알림 | 같은 과업 재실행, 실행 간 이슈 대응과 해결 근거, 알림 중복 방지 |
| P1 | 사용량·운영 관측 | 토큰/브라우저 시간/비용, 제한·과금, 감사 기록 |

### 프론트엔드

| 우선순위 | 작업 | 완료 기준 |
|---|---|---|
| P0 | 임시 HTML/JS를 제품 구조로 이관 | 라우팅·컴포넌트·상태 관리, 기존 실행 준비/팝업/리포트 유지 |
| P0 | Django 인증·프로젝트·실행 API 연결 | localStorage 임시 등록을 실제 저장으로 교체, 권한/만료/오류 대응 |
| P0 | 실행 상태·대기·실패·취소 UX | 빈 화면 없이 상태를 설명하고 근거를 잃지 않음 |
| P0 | 고객별 라이브 브라우저 연결 | 공급자 UI 직접 노출을 인증된 스트림 경로로 교체 |
| P0 | 반응형·접근성·큰 기록 성능 | 키보드/포커스/읽기 크기, 많은 카드·이벤트 가상화 |
| P1 | 이슈 상세·재검증 비교·리포트 공유 | 과거/현재 근거 연결, PDF 생성 상태와 다운로드 표시 |
| P1 | 실제 50개 모니터 연동 | 백엔드가 실행한 실제 세션으로 표시하고 UI 재생과 구분 |

### PO

| 우선순위 | 결정/작업 | 산출물 |
|---|---|---|
| P0 | 첫 고객군·지원 사이트·핵심 여정 | 스타트업 대표가 첫 실행에서 확인할 1~3개 과업과 가치 기준 |
| P0 | 기능 확인·과업 제안·타겟 질문 | 질문 최소 개수, 자동 제안 확인/수정 기준, 미지원 요청 처리 |
| P0 | 제품 지표와 표현 | 완료/가상 이탈/오류 정의, 실제 고객 이탈률과 구분하는 문구 |
| P0 | 사람 관측과 검증 설계 | 동일 과업의 실제 관측 수집, 결과/중단 위치/원인 비교, 가중치 보정 기준 |
| P0 | 테스트 계정·변경 허용 범위 | 초기 화면 조건, 읽기/쓰기 허용 동작, 결제·삭제·전송 중단 규칙 |
| P1 | 반복 관리 서비스의 우선순위 | 배포 후 신규·악화·해결 알림, 리포트 빈도, 수동 점검 대비 효용 |
| P1 | 요금·사용량·파일 보존 | 실행 수/동시성/모델 비용 한도, 리포트 제공·보존 기준 |

### 에이전트 담당의 잔여 작업

1. **인계:** Django와 실행/이벤트/근거 계약을 확정하고 두 실행 경로를 통합. 임시 프론트를 운영 API로 바꿀 때 회귀 검증.
2. **실제 모델 연결:** 사용할 모델·예산을 정한 뒤 CLI 어댑터의 실제 호출을 검증하고 대시보드 정책 인터페이스에 연결. 실패·안전·성공 판정도 평가.
3. **새 사이트 대응:** 반찬로드 고정 정책에서 벗어나 확인된 기능/과업을 실제 사이트의 안전한 행동과 성공 기준으로 변환.
4. **검증 품질:** 사람 관측을 받아 좌절 가중치·원인 설명을 보정. 연령만으로 행동을 추정하지 않음.
5. **반복 관리:** 배포 전후 동일 과업·이슈를 대응시키고 해결 여부를 근거로 재검증. 50개 규모의 비용·부하·세션 격리는 별도 검증.

에이전트 담당이 만든 **로컬 데모와 실행 기반의 1차 인계는 가능한 상태**입니다. 범용 서비스 에이전트와 운영 품질까지 완료된 상태는 아닙니다.

## 검증과 저장소 구성

```bash
python scripts/verify_harness.py --profile browser
```

최근 검증(2026-11-03): **45개 테스트 통과**, 실패·오류·skip 0건. PDF 생성/한글 내용/다운로드, 로컬 실행·세션 격리·근거·API 경계 검사를 포함합니다. 로컬 verdict는 `artifacts/harness/20261102T200242Z_13c1831b/verdict.json`입니다.

검증은 외부 고객 사이트 대신 로컬 모의 화면과 SDK mock을 사용합니다. 결과는 `artifacts/harness/<실행>/verdict.json`, `pytest.xml`, `execution.log`에 저장하며 `pass / fail / blocked`를 구분합니다. 실제 모델 호출·실사용자 전환율·50개 동시 운영 성능은 이 통과 결과에 포함되지 않습니다.

| 경로 | 내용 |
|---|---|
| `agent/` | 실행기·정책·스키마·페르소나·관측·리포트·로컬 HTTP 어댑터 |
| `saas-web/` | 임시 SaaS 프론트, 대시보드·실행 준비·팝업·리포트 |
| `demo-shop/` | 합성 데이터 기반 반찬로드 테스트 웹 |
| `tests/` | 실제 로컬 Chromium 테스트 및 계약/SDK mock |
| `harness/` | SPEC·역할·워크플로·훅·체크리스트 |
| `.agents/skills/dropspot-verify/` | 저장소 검증 스킬 |
| `scripts/` | 검증 게이트·로컬 서비스 관리 |
| `integrations/skyvern/` | 브라우저 공급자 연결 안내 |
| `examples/` | 핵심 실행기 입력 예시 |

인증키·로그인 세션·실행 근거·로컬 캐시·원본 기획 PDF는 커밋하지 않습니다. 폰트와 외부 도구의 라이선스는 해당 자산/연동 문서를 참고하세요. 운영 인증·비밀값·고객 데이터·브라우저 격리는 Django 및 배포 설계에서 완성해야 합니다.
