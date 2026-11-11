# 개발·검증 흐름

1. **변경 범위 정리:** SPEC 요구사항 ID와 변경할 입력·관찰·행동·진단을 연결한다. 기존 스키마의 변경 여부를 적는다.
2. **구현:** 구현 담당이 런타임 또는 테스트 웹을 수정한다. 테스트 웹의 정답 라벨·기대 이탈률은 행동 모델에 주지 않는다.
3. **브라우저 gate:** ACTION-01·02와 SHOP-01~03·SAAS-01~03을 포함해 `python scripts/verify_harness.py --profile browser`를 실행한다. 실패하면 해당 로그를 보고 수정한다. 실행 불가·skip은 blocked다.
4. **실제 모델 gate:** 모델 연결 변경을 실제 호출로 확인할 때 키와 모델 ID를 설정하고 `--profile model --model <ID> --allow-model-call`을 실행한다. 이미 허용된 호출 범위는 반복 승인 없이 진행하되, 이 옵션이 없는 작업에서 묵시적으로 비용 호출을 시작하지 않는다.
5. **UX·회귀 평가:** 정상·문제·수정 버전을 같은 패널 조건에서 반복한다. 실제 노출 여부, 증거, 오탐, 기술 실패를 분리해 기록한다. 아직 평가되지 않은 항목은 미검증으로 둔다.
6. **리뷰·인계:** SPEC ID, 소스 fingerprint, verdict 경로, 관측 결과, 미검증 항목을 전달한다. 문서의 체크박스만으로 결과를 선언하지 않는다.

통제된 정상 과업의 성공은 사이트별 URL 판정 기준이다. 모델이 사람의 인내심·구매 의사를 재현한다는 결론으로 확대하지 않는다. 실제 GUI 호출이 막혀 있어도 프론트·브라우저 기반 검증은 계속 진행할 수 있다.

자동 CI 연결은 후속 작업이다. 연결 시 pass만 성공 처리하고 fail·blocked 모두 병합·배포 조건에 맞게 처리한다. blocked를 exit 0으로 무시하는 연결은 사용하지 않는다.

## 테스트 웹 실행

```bash
python -m agent.shop --port 4173
python -m agent.cli --shop fixed
python -m agent.cli --shop problem --case signup
python -m agent.cli --shop problem --case fee
python -m agent.cli --shop problem --case cta
```

`normal`·`fixed`·`problem`과 `all`·`signup`·`fee`·`cta` 조건을 조합할 수 있다. 강제 가입의 규칙 판정, 배송비·CTA UI 노출 조건, 모바일 실제 조작은 서로 다른 테스트로 검증한다. 고정 정책 실행이 완료돼도 모델이 실제 웹 과업을 수행했다고 보고하지 않는다. 실제 모델 E2E는 별도 호출·근거가 생길 때 추가한다.

## SaaS 로컬 워크스페이스

`python -m agent.console --port 4174`로 실행한다. 웹에서 새 진단을 시작하면 데모 쇼핑몰의 고정 정책 Chromium 세션을 실행한다. 외부 사이트 등록·프로필은 브라우저 로컬 저장이고, Django 인증·예약·배포 훅은 아직 없다.
