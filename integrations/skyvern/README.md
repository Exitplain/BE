# Skyvern 로컬 연결

DropSpot은 브라우저 공급자의 세션 API와 공식 라이브 UI를 재사용한다. 자체 영상 전송/마우스 엔진을 다시 만들지 않는다. 현재 고정 행동 정책이 실제 Playwright 브라우저를 조작하며 모델 호출은 없다.

## 기존 개발 환경 실행

```bash
python scripts/manage_skyvern.py start
python scripts/manage_skyvern.py status
python -m agent.shop --port 4173
python -m agent.console --port 4174
```

`http://localhost:4174/#personas`에서 실행한다. 각 과업에 새 세션을 만들고 해당 세션만 종료한다. 최대 동시 3개, 기본 12개 세션이다. 선택한 카드의 라이브 화면을 보고 종료 후에는 캡처를 본다.

`/#connect`는 `http://127.0.0.1:4173/`만 여는 별도 데모 연결이다. 공식 UI의 `Take control`로 직접 조작하고 현재 화면을 기록할 수 있다. 모델의 자율 탐색이나 외부 계정 인증 기능은 아니다. `/api/connections/demo`의 start/checkpoint/close API를 사용한다.

## 설치 구성과 재현 범위

현재 관리 스크립트는 아래처럼 **이미 준비된 설치**를 관리한다. 새 머신용 설치 프로그램은 없다.

- Python 환경: `~/.cache/dropspot-skyvern`, `skyvern[server]==1.0.56`, `skyvern-ui==1.0.56`.
- Skyvern 작업 폴더: `~/.cache/dropspot-skyvern-work`. 서버 인증키는 이 폴더의 `.env`에만 있으며 저장소나 웹 응답에 포함하지 않는다.
- API: loopback 8000. 공식 UI: loopback 8080. `run_server.py`와 외부 작업 폴더의 `ui/start.mjs`가 필요하다.
- PostgreSQL 16.2: `~/.cache/dropspot-postgres/pgserver/pginstall`, DB `~/.cache/dropspot-skyvern-db`, port 55432. 로컬 개발용 trust 설정이며 운영 구성은 별도다.
- PyPI UI CLI의 프론트 경로 문제로 공식 `localServer.js`를 사용했다. 런타임 URL은 로컬 API/WS를 가리킨다. 버전 출처는 `manifest.json` 참고.
- 서버 키는 API 요청에만 사용한다. CDP 주소를 연결 API의 웹 응답에 노출하지 않는다. 공식 공급자 UI 자체는 개발용 정보를 표시할 수 있다.

모델 키를 넣거나 Skyvern을 시작하는 것만으로 DropSpot 고정 정책이 모델 정책으로 바뀌지 않는다. 핵심 CLI의 모델 어댑터와 페르소나 대시보드 통합은 별도 작업이다.

## 라이브 검증

```bash
~/.cache/dropspot-skyvern/bin/python scripts/verify_skyvern_live.py
```

합성 로컬 화면에서 영상 프레임, 실제 웹 마우스/키보드 입력, 같은 세션 인계를 확인한다. 모델 호출은 0회다. 기본 브라우저 게이트는 공급자 SDK 대역으로 계약을 검사하며 실제 설치 검증과 구분한다.

운영 연동에는 고객별 세션 소유권·인증된 스트림 프록시·네트워크 격리·보존/삭제·비용 제한이 필요하다. 공급자 라이선스와 배포 조건은 도입 버전에서 별도 확인한다.
