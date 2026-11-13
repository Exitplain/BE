"""테스트 이름 규칙 (Figma P01·C08): "4차·첫 주문 완료 외 1개".

순수 함수라 모델과 서비스가 모두 import 해도 순환이 없다.
이름은 Run 을 만들 때 한 번 계산해 저장한다 (확정 스냅샷이 불변이라 나중에 바뀔 일이 없다).
"""


def build_run_name(sequence_no, kind, snapshot_data):
    if kind == "cause_repro":
        return "원인 재현"
    tasks = (snapshot_data or {}).get("tasks") or []
    names = [str(t.get("name") or t.get("id"))[:60] for t in tasks if isinstance(t, dict) and (t.get("name") or t.get("id"))]
    if not names:
        return f"{sequence_no}차"
    if len(names) == 1:
        return f"{sequence_no}차·{names[0]}"
    return f"{sequence_no}차·{names[0]} 외 {len(names) - 1}개"
