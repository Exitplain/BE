"""K04 사람 관측 (CAL-16~18): 입력 검사·recorder.js 가져오기·예측과 일치 비교.

예측은 가장 최근 기준 리포트(원인 재현 제외)의 과업별 값이다.
- 결과: 그 과업 예측 세션의 다수 결과 (완료 ≥ 이탈 이면 완료)
- 단계: 예측에서 이탈이 가장 많았던 화면
- 원인: 예측 이탈의 1위 원인
분모는 harness/SPEC.md Human Alignment 와 같다: 결과=관측 전체, 단계=사람 이탈, 원인=원인을 아는 사람 이탈.
"""
import re

from agent.behavior import CAUSES
from common.exceptions import ApiError
from measurements import services as measure
from reports.journey import screen_id

from .models import HumanObservation

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
CAUSE_KEYS = (*CAUSES, "unknown")


def _fail(field, message):
    raise ApiError(422, "VALIDATION_ERROR", message, {field: [message]})


def predictions(project):
    """{task_id: {name, outcome, stage_id, stage_name, cause, stages:[{id,name}]}} — 비교 기준 리포트에서"""
    report = measure.latest_report(project)
    out = {}
    for task in (report.data.get("tasks") or []) if report else []:
        funnel = task.get("journey_funnel") or {}
        stages = funnel.get("stages") or []
        worst = max(stages, key=lambda s: (s.get("dropouts_here", 0), -s.get("order", 0)), default=None)
        top = next((c.get("key") for c in task.get("causes") or [] if c.get("dropouts") and c.get("key") in CAUSES), None)
        out[task["task_id"]] = {
            "name": task.get("name") or task["task_id"],
            "outcome": "success" if task.get("success", 0) >= task.get("dropout", 0) else "dropout",
            "stage_id": worst["path"] if worst and worst.get("dropouts_here") else None,
            "stage_name": worst["name"] if worst and worst.get("dropouts_here") else None,
            "cause": top,
            "stages": [{"id": s["path"], "name": s["name"]} for s in stages],
        }
    return out, report


def clean(project, body, tasks):
    if not isinstance(body, dict):
        _fail("body", "관측 형식이 올바르지 않습니다.")
    unknown = sorted(set(body) - {"participant_code", "task_id", "outcome", "stage_id", "cause", "purpose"})
    if unknown:
        raise ApiError(422, "VALIDATION_ERROR", "허용하지 않는 필드입니다.", {k: ["허용하지 않는 필드입니다."] for k in unknown})
    code = body.get("participant_code")
    if not isinstance(code, str) or not CODE_RE.match(code):
        _fail("participant_code", "영문·숫자·밑줄·하이픈 1~40자로 입력해주세요")
    task_id = body.get("task_id")
    if task_id not in tasks:
        _fail("task_id", "비교할 사전 테스트의 과업을 골라 주세요.")
    outcome = body.get("outcome")
    if outcome not in HumanObservation.Outcome.values:
        _fail("outcome", "완료 또는 이탈을 골라 주세요.")
    purpose = body.get("purpose") or HumanObservation.Purpose.CALIBRATION
    if purpose not in HumanObservation.Purpose.values:
        _fail("purpose", "보정용 또는 평가용을 골라 주세요.")
    stage_id, cause = body.get("stage_id") or "", body.get("cause") or ""
    if outcome == "success":
        if stage_id or cause:
            _fail("outcome", "완료한 과업은 멈춘 단계·원인 없이 입력해요.")
    else:
        if stage_id not in {s["id"] for s in tasks[task_id]["stages"]}:
            _fail("stage_id", "멈춘 단계를 골라 주세요.")
        if cause not in CAUSE_KEYS:
            _fail("cause", "원인을 골라 주세요. 모르면 '원인 미상'을 골라요.")
    return {"participant_code": code, "task_id": task_id, "outcome": outcome, "stage_id": stage_id,
            "cause": cause, "purpose": purpose}


def from_recorder(record, task_id, purpose, tasks):
    """demo-shop/assets/recorder.js 가 내려받게 한 JSON → 관측 입력. 원인은 기록에 없어서 '원인 미상'."""
    if not isinstance(record, dict) or record.get("schema") != "human-record-1":
        _fail("record", "recorder.js 로 저장한 결과 파일(human-record-1)이 아니에요.")
    if record.get("outcome") not in ("success", "give_up"):
        _fail("record", "과업이 끝나지 않은 기록이에요.")
    outcome = "success" if record["outcome"] == "success" else "dropout"
    stage_id = ""
    if outcome == "dropout":
        views = [e for e in record.get("events") or [] if isinstance(e, dict) and e.get("type") == "view"]
        path = screen_id(views[-1].get("path") or "/") if views else "/"
        known = {s["id"] for s in tasks.get(task_id, {}).get("stages", [])}
        stage_id = path if path in known else ""
        if not stage_id:
            _fail("record", f"멈춘 화면({path})이 이 과업의 예측 단계에 없어요. 직접 입력해 주세요.")
    return {"participant_code": str(record.get("participant_id") or ""), "task_id": task_id, "outcome": outcome,
            "stage_id": stage_id, "cause": "unknown" if outcome == "dropout" else "", "purpose": purpose}


def match(row, predicted):
    """한 관측의 결과·단계·원인 일치 (해당 없음은 None)."""
    if predicted is None:
        return {"outcome": None, "stage": None, "cause": None}
    dropout = row.outcome == "dropout"
    return {
        "outcome": row.outcome == predicted["outcome"],
        "stage": (row.stage_id == predicted["stage_id"]) if dropout else None,
        "cause": (row.cause == predicted["cause"]) if dropout and row.cause != "unknown" else None,
    }


def summary(rows):
    out = {}
    for key in ("outcome", "stage", "cause"):
        values = [r["match"][key] for r in rows if r["match"][key] is not None]
        out[key] = {"matched": sum(values), "total": len(values)}
    return out
