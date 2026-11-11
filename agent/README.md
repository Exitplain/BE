# DropSpot 에이전트 — 첫 실행 버전

Django와 독립적인 Python 모듈이다. 실제 Chromium에서 관찰·입력·클릭을 실행하고 세션별 이벤트·화면·결과를 저장한다. 현재 범위는 신뢰할 수 있는 테스트 사이트의 호출·격리 검증과 필수 입력 필드 부담 규칙 1종이다.

## 설치

프로젝트 루트에서 실행한다. Python 3.11 이상이 필요하다.

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[model,test]'
python -m playwright install chromium
```

Linux에서 브라우저 시스템 라이브러리가 부족하면 환경 관리자에게 맞는 Playwright 시스템 의존성 설치가 필요하다. 이번 작업 환경에는 `/home/user/.cache/dropspot-venv`에 전용 환경을 구성했다.

## API 키 없이 실제 브라우저 검증

```bash
python -m agent.cli --demo normal --repeat 5
python -m agent.cli --demo problem
python -m agent.cli --demo fixed
python -m agent.metrics artifacts/demo_normal
python -m pytest -q
```

각 실행은 2개 독립 세션을 동시 실행한다. normal/fixed는 1개 필드와 제출 버튼, problem은 8개 필수 필드를 갖는 통제 페이지다. 고정 테스트 정책이 입력·클릭하고 별도의 URL 조건으로 성공을 판정한다. 이 모드는 실제 브라우저를 쓰지만 **모델이 판단하는 GUI 에이전트가 아니다**. 문제·수정 결과 역시 알려진 규칙의 검증이며 일반 UX 진단 정확도를 입증하지 않는다.

## 모델이 행동을 선택하는 실행

실행 환경에 `OPENAI_API_KEY`를 설정한 뒤 계정에서 사용할 수 있는 정확한 모델 ID를 지정한다. 키를 파일·명령 기록·채팅에 붙이지 않는다. `.env` 자동 로딩은 하지 않으므로 환경 변수로 주입한다.

```bash
python -m agent.cli --demo normal --policy openai --model YOUR_MODEL_ID
python -m agent.cli --input examples/run.json --policy openai --model YOUR_MODEL_ID
```

모델에는 현재 뷰포트의 스크린샷·보이는 요소·최근 행동·페르소나를 전달한다. 구조화 출력으로 받은 한 행동을 실행하고 새 관찰을 만든다. 모델 출력·거부·인증 오류는 성공이나 고객 이탈로 처리하지 않는다. 공급자 오류 상세는 키·내용 노출을 피하도록 결과에 예외 종류만 기록한다.

API 키가 없는 현재 환경에서는 이 호출 경로의 실제 응답·모델 성능은 검증되지 않았다. 자동으로 고정 정책으로 대체하지 않는다. 모델 실행은 호출 비용이 발생한다.

## Django 워커 연동

```python
from agent import RunInput, execute_run
from agent.policies import OpenAIPolicy

# Django가 검증·확정한 입력. 워커의 동기 진입점에서는 asyncio.run으로 호출 가능.
config = RunInput.model_validate(payload)
result = await execute_run(
    config,
    policy_factory=lambda: OpenAIPolicy(model=model_id),
    output_dir=artifact_root,
    emit_event=save_event,        # sync 또는 async, event_id로 중복 제거
    should_cancel=is_cancelled,  # sync 또는 async, 스텝 경계에서 확인
)
```

공유 가능한 이벤트 payload를 반환하지만 현재 아티팩트는 로컬 파일 경로다. Django 저장 어댑터에서 객체 스토리지 업로드·권한 조회 URL을 연결해야 한다. 콜백 오류는 세션 실패로 이어질 수 있고, 브라우저 시작·저장 실패 등 실행 수준 예외는 호출자에게 전파될 수 있다. 외부 작업 취소는 정리 후 전파되므로 워커가 Run 상태를 갱신해야 한다. 프로세스 강제 종료 감지·재시도·heartbeat는 백엔드 책임이다.

- 함수 호출마다 고유 `attempt_id`와 디렉터리를 만든다.
- 각 페르소나는 고유 context·쿠키·저장소·정책 인스턴스를 사용한다.
- 세션 ID는 패널 내에서 고정되며 attempt와 조합해 식별한다.
- 이벤트 sequence는 **attempt 전체**에서 증가한다. 이전 문서의 세션별 순서 가정 대신 이 구현 규약을 사용한다.
- 취소는 스텝 경계에서 확인하므로 진행 중인 모델 호출이 끝날 때까지 지연될 수 있다. 전체 세션 제한 시간으로 상한을 둔다.
- summary의 success는 설정된 URL 경로 도달 기준이다. 임의 사이트의 주문 상태까지 보장하지 않으므로 실제 연동에서는 사이트별 외부 성공 판정을 확장해야 한다.

## 출력

```text
artifacts/<run_id>/<attempt_id>/
  input.json
  events.jsonl
  summary.json
  session_A/
    step_000.png
    step_000.json
    result.json
```

스텝 JSON에는 관찰 내용이 포함되고 화면에는 테스트 데이터가 보일 수 있다. 현재는 합성 데이터·전용 테스트 계정으로만 실행한다. 토큰 사용량은 모델 응답에서 수집하고, 공급자 비용은 아직 계산하지 않아 `null`로 둔다. 실패한 호출의 토큰 사용량은 공급자 확인이 필요하다.

## 검증 범위와 다음 작업

구현·검증 대상:

- 실제 Chromium에서 입력·클릭·성공 URL 확인.
- 두 세션의 로컬 저장소·쿠키·입력 결과 분리.
- 필드 예산 초과 시 근거와 가상 이탈 생성.
- 잘못된 target의 기술 실패 분리, 시간·스텝 예산, 취소.
- 반복 실행별 별도 결과와 기본 지표 집계.

미구현·미검증:

- 실제 모델 호출 성능과 프롬프트 튜닝.
- 비용 계산, 부하·동시성 확대, 모델 버전 비교.
- 배송비·시각적 CTA·복잡한 과업·iframe·새 창 제어.
- 자연어 패널 생성, 화면 기반 퍼널 추정, 7종 원인 분석.
- 이전 배포와 이슈 대응, 신규·악화·해결 판정, 알림 품질 평가.
- 저장 어댑터와 Django 연동.

허용 origin 검사와 HTTP 요청 차단은 초기 범위 제한이다. DNS rebinding·내부 주소 차단을 포함한 서비스용 SSRF 방어는 구현하지 않았다. 임의 고객 URL을 받는 공개 서버에 바로 배포하지 않는다. 실제 결제·실인증을 코드로 완벽히 차단하는 범용 정책도 아직 없으므로 모의 페이지에 한정한다.

## 확장 웹 테스트와 행동 도구

실제 쇼핑몰 형태의 로컬 프론트는 [demo-shop/README.md](../demo-shop/README.md)를 참고한다.

```bash
python -m agent.shop --port 4173
python -m agent.cli --shop fixed
python -m agent.cli --shop problem --case signup
python scripts/verify_harness.py --profile browser
```

실행 행동은 `click`, `fill`, `hover`, `select`, `check`, `uncheck`, `press`, `scroll`, `wait`, `back`, `forward`, `reload`이며, `stop`은 런타임의 중단 신호다. 입력은 현재 관찰의 target ID와 허용 값으로 제한한다. `select`는 관찰된 option 값, `press`는 허용 키, `scroll`은 up/down, `wait`는 100~2000ms만 지원한다. 스크린샷·요소 관찰은 매 스텝 자동으로 수집한다. iframe·팝업·새 탭 제어와 업로드는 미지원이다.

`ShopScriptedPolicy`는 UI 라벨 기반 고정 정책이고 OpenAI 정책과 혼동하지 않는다. 새 스토어프런트에서도 현재 자동 진단 규칙은 필수 입력 필드 부담 1종이다.
