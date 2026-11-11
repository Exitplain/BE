# DropSpot 검증 하네스

문서로 약속한 조건을 실행 결과와 연결한다. 브라우저 기반·모델 호출·UX 진단·배포 회귀는 서로 다른 검증 범위다. browser 프로필은 기본 실행 계약, 확장 행동, 반찬로드 프론트의 주문·가입 회귀·화면 조건·모바일 검증과 DropSpot SaaS의 실행·근거·비교·로컬 저장을 함께 실행한다. model 프로필은 단순 통제 페이지 호출이며 실제 반찬로드 모델 E2E는 별도 미검증이다.

| 구성 | 역할 |
|---|---|
| [SPEC](SPEC.md) | 입력·격리·종료·증거 계약과 요구사항 ID |
| [SKILL](../.agents/skills/dropspot-verify/SKILL.md) | 이 저장소 검증을 수행하는 에이전트용 지침 |
| [SUBAGENTS](SUBAGENTS.md) | 구현·검증·리뷰의 작업 경계와 인계 형식 |
| [WORKFLOW](WORKFLOW.md) | 변경 → 로컬 검증 → 모델 검증 → 평가 순서 |
| [HOOK](hooks.json) | 수동 실행 가능한 검증 명령 등록부 |
| [CHECKLIST](CHECKLIST.md) | 요구사항과 테스트·결과 파일의 대응 |

```bash
python scripts/verify_harness.py --profile browser
python scripts/verify_harness.py --profile model --model YOUR_MODEL_ID --allow-model-call
```

현재 작업 환경에서는 `/home/user/.cache/dropspot-venv/bin/python`을 사용할 수 있다. 다른 환경의 설치 방법은 [에이전트 README](../agent/README.md)를 따른다. `model`은 API 키·모델 ID·명시적 호출 옵션이 있어야 실행하며 비용이 발생할 수 있다.

결과는 `artifacts/harness/<실행 ID>/verdict.json`, `execution.log`, 브라우저 프로필의 `pytest.xml`에 저장된다. 종료 코드는 **0=pass, 1=fail, 2=blocked**다. 키 없음·필수 테스트 누락·skip을 성공으로 바꾸지 않는다. 검증 중 소스가 변경되면 blocked로 표시하고 안정된 소스로 다시 실행한다.

훅은 이 명령 등록부와 실행 스크립트다. Git·CI·Codex 자동 훅에 설치된 상태는 아니다. 배포 전 실제 호출 주체가 이 명령을 연결해야 한다. 통과 결과도 당시 소스 fingerprint와 검증 범위 안에서만 유효하다.
