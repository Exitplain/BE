"""리포트 '화면 둘러보기'·'문제가 있었던 화면'·'원인 분포(감지된 마찰)' (Figma 시안 · 리포트 화면 둘러보기).

리포트의 실행 퍼널(journey_funnel) 화면 단계를 그대로 쓰고, 그 위에 실제로 찍힌 화면 캡처와 마찰을 얹는다.
- 마찰은 이탈한 세션만이 아니라 목표에 도달한 세션이 겪은 것도 센다. 에이전트가 화면마다 감지해 issue 로 남긴 것
  (원인·근거 스텝)을 근거 스텝의 화면에 붙인다. 이탈을 일으킨 주 원인(primary)은 따로 센다.
- 화면 캡처는 에이전트가 그 스텝에서 찍은 것이다. 마찰이 있었던 스텝의 캡처를 먼저 고른다.
- 모델을 부르지 않는다. 이미 저장된 세션·관측만 다시 묶는다.
- 기술 실패·한도 초과 세션은 세지 않는다 (리포트와 같은 기준). 모두 가상 페르소나의 모의 결과다.
- voices: 그 화면에서 이탈한 세션이 왜 그만뒀는지를 사람이 말하듯 쓴 문장 (agent/explain.describe).
  같은 문장은 묶어 몇 번 방문이 그랬는지·어느 세그먼트인지 센다. 말투만 사람처럼 바꾼 모델 판정 근거다.
"""
import re
from collections import defaultdict

from .aggregate import cause_names, classify, technical_statuses
from .journey import screen_id

STEP_NUMBER = re.compile(r"(\d+)$")
PROBLEM_LIMIT = 5
VOICE_LIMIT = 3


def _step(step_id):
    m = STEP_NUMBER.search(str(step_id or ""))
    return int(m.group(1)) if m else None


def _detail(issue):
    """불편한 점 한 줄. 입력 부담은 칸 수, 나머지는 페르소나가 말하듯 (예전 영어 summary 대신)."""
    from agent.explain import REASONS
    if issue.get("category") == "input_burden" and issue.get("observed_fields") is not None:
        return f"필수 입력 {issue['observed_fields']}칸 (허용 {issue.get('configured_limit')}칸)"
    return REASONS.get(issue.get("category")) or issue.get("summary") or ""


def _voice(session):
    from agent.explain import describe
    return describe(session.status, session.reason, session.issues or [], session.terminal_stage)


def _task_screens(task, sessions, causes, shot_url):
    funnel = task.get("journey_funnel") or {}
    stages = [s for s in funnel.get("stages", [])]
    known = {s["id"] for s in stages}
    screens = {s["id"]: {"screen_id": s["id"], "name": s["name"], "order": s["order"], "reached": s["reached"],
                         "dropouts_here": s["dropouts_here"], "is_goal": s.get("is_goal", False),
                         "screenshot_url": None, "_shot_rank": 9, "_frictions": defaultdict(dict),
                         "_voices": {}} for s in stages}
    cause_sessions = defaultdict(set)          # 원인 → 마찰을 겪은 세션
    cause_dropouts = defaultdict(set)          # 원인 → 이 원인이 주 원인으로 이탈한 세션
    cause_screens = defaultdict(lambda: defaultdict(set))

    for session, kind, observations in sessions:
        by_step = {o.step: o for o in observations}
        if kind == "dropout" and observations:                 # 떠난 화면 = 마지막으로 본 화면
            left = screen_id(observations[-1].url)
            if left in screens:
                text = _voice(session)
                voice = screens[left]["_voices"].setdefault(text, {"text": text, "sessions": 0, "segments": []})
                voice["sessions"] += 1
                segment = getattr(session.persona_snapshot, "segment_name", "")
                if segment and segment not in voice["segments"]:
                    voice["segments"].append(segment)
        for o in observations:                                 # 캡처 후보: 이탈 세션 > 그 밖
            key = screen_id(o.url)
            if key in screens and o.artifact:
                rank = 2 if kind == "dropout" else 3
                if rank < screens[key]["_shot_rank"]:
                    screens[key].update(screenshot_url=shot_url(o.id), _shot_rank=rank)
        for issue in session.issues or []:
            category = issue.get("category") or "unknown"
            steps = [_step(s) for s in issue.get("evidence_step_ids") or []]
            obs = next((by_step[s] for s in steps if s in by_step), None)
            key = screen_id(obs.url) if obs else None
            if key not in known:
                continue
            cause_sessions[category].add(session.pk)
            cause_screens[category][key].add(session.pk)
            primary = kind == "dropout" and issue.get("primary")
            if primary:
                cause_dropouts[category].add(session.pk)
            entry = screens[key]["_frictions"][category]
            entry.setdefault("sessions", set()).add(session.pk)
            entry.setdefault("dropouts", set())
            if primary:
                entry["dropouts"].add(session.pk)
            if obs.artifact and (not entry.get("screenshot_url") or primary):
                entry["screenshot_url"] = shot_url(obs.id)
            entry.setdefault("detail", _detail(issue))
            if obs.artifact and screens[key]["_shot_rank"] > 1:          # 마찰이 있었던 스텝의 캡처를 대표로
                screens[key].update(screenshot_url=shot_url(obs.id), _shot_rank=1)

    out = []
    for s in sorted(screens.values(), key=lambda s: s["order"]):
        raw = s.pop("_frictions")
        frictions = [{"category": c, "name": causes.get(c, "원인 미상"), "sessions": len(e["sessions"]),
                      "dropouts": len(e["dropouts"]), "detail": e.get("detail", ""), "screenshot_url": e.get("screenshot_url")}
                     for c, e in raw.items()]
        frictions.sort(key=lambda f: (-f["sessions"], -f["dropouts"], f["category"]))
        s.pop("_shot_rank")
        s["frictions"] = frictions
        voices = sorted(s.pop("_voices").values(), key=lambda v: (-v["sessions"], v["text"]))
        s["voices"] = voices[:VOICE_LIMIT]
        s["friction_sessions"] = len(set().union(*(e["sessions"] for e in raw.values())))   # 이 화면에서 마찰을 겪은 세션 수
        out.append(s)

    problems = [s for s in out if s["frictions"] or s["dropouts_here"]]
    problems.sort(key=lambda s: (-s["friction_sessions"], -s["dropouts_here"], s["order"]))
    by_id = {s["screen_id"]: s for s in out}
    cause_rows = []
    for category, members in cause_sessions.items():
        where = sorted(cause_screens[category].items(), key=lambda kv: (-len(kv[1]), by_id[kv[0]]["order"]))
        cause_rows.append({
            "category": category, "name": causes.get(category, "원인 미상"), "sessions": len(members),
            "dropouts": len(cause_dropouts[category]),
            "screens": [{"screen_id": k, "name": by_id[k]["name"], "sessions": len(v),
                         "screenshot_url": next((f["screenshot_url"] for f in by_id[k]["frictions"] if f["category"] == category),
                                                by_id[k]["screenshot_url"])} for k, v in where[:3]],
        })
    cause_rows.sort(key=lambda c: (-c["sessions"], -c["dropouts"], c["category"]))
    return {"task_id": task["task_id"], "name": task.get("name", task["task_id"]),
            "valid_sessions": funnel.get("valid_sessions", 0), "screens": out,
            "problems": [{k: p[k] for k in ("screen_id", "name", "screenshot_url", "reached", "dropouts_here",
                                            "friction_sessions")} | {"top": p["frictions"][:1]} for p in problems[:PROBLEM_LIMIT]],
            "causes": cause_rows}


def report_screens(report):
    """GET /reports/{id}/screens 응답. 실행 기록(DB)이 있는 리포트만 화면 캡처가 있다."""
    note = "가상 페르소나의 모의 결과입니다. 감지된 불편한 점은 목표에 도달한 방문이 겪은 것도 셉니다."
    if not report.run_id:
        return {"report_id": str(report.id), "available": False, "reason": "NO_SCREENSHOTS",
                "message": "화면 캡처가 없는 예전·샘플 리포트예요.", "tasks": [], "note": note}
    from runs.models import Session
    from runs.queries import observation_url
    technical = technical_statuses()
    causes = cause_names()
    rows = (Session.objects.filter(run_id=report.run_id).select_related("persona_snapshot")
            .prefetch_related("observations").order_by("task_id", "seed", "pk"))
    by_task = defaultdict(list)
    for session in rows:
        kind = classify(session.status, technical)
        if kind not in ("success", "dropout"):
            continue
        by_task[session.task_id].append((session, kind, sorted(session.observations.all(), key=lambda o: o.step)))
    tasks = [_task_screens(t, by_task.get(t["task_id"], []), causes, observation_url)
             for t in (report.data or {}).get("tasks", [])]
    return {"report_id": str(report.id), "available": True, "reason": None, "message": None, "tasks": tasks, "note": note}
