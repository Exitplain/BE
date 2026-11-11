---
name: dropspot-verify
description: Validate this DropSpot repository's controlled browser agent, session isolation, and GUI smoke-test evidence. Use for agent or demo-site verification; excludes product deployment and real customer conversion claims.
---

# DropSpot 검증

저장소 루트의 `harness/SPEC.md`와 `harness/CHECKLIST.md`에서 이번 변경의 요구사항 ID를 확인한다. `agent/schemas.py`가 실제 실행 입력의 기준이다.

기본 검증은 프로젝트 Python 환경에서 `python scripts/verify_harness.py --profile browser`로 실행한다. `verdict.json`·`pytest.xml`·`execution.log`를 확인하고 **pass / fail / blocked**를 구분한다. 실행 불가·누락·skip을 성공으로 바꾸지 않는다. 소스 fingerprint가 바뀌면 현재 변경에 맞는 검증을 다시 수행한다.

실제 모델 호출이 요청·허용된 경우에만 `--profile model --model <모델 ID> --allow-model-call`을 사용한다. 키는 환경에서 공급하고 출력하지 않는다. 키가 없으면 blocked를 보고하되 모델 없는 브라우저 검증은 계속할 수 있다. 고정 정책·SDK mock을 실제 모델 GUI 성공으로 표현하지 않는다.

검증 모델에는 실제 사용자가 볼 화면·과업만 전달한다. 문제 정답·기대 이탈률을 프롬프트에 주입하지 않는다. 가상 이탈·기술 실패·인증 차단·예산 초과를 분리한다. 일반 UX 정확도·실사용자 전환율·배포 회귀 해결 여부는 그에 맞는 별도 실험 없이 주장하지 않는다.

인계에는 요구사항 ID, 실행 명령, verdict 파일 경로, 관측된 결과, 미실행·미검증 항목을 남긴다. 수동 훅을 자동 CI·Git 훅에 설치했다고 말하지 않는다.
