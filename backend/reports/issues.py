"""발견한 문제 (Figma W03 발견한 문제 · C09 프로젝트 발견한 문제).

리포트에 이미 있는 세션 결과를 다시 묶어서 '어느 과업의 어느 단계에서 어떤 원인으로 몇 명이 이탈했는지'를 보여 준다.
새로 계산하는 값은 없고, 리포트(ReportSnapshot.data["sessions"])의 가상 이탈 세션만 센다.

- 문제 1개 = 과업 × 이탈한 화면 × 주 원인
  이탈한 화면은 실행 퍼널(journey_funnel)과 같은 단계 ID(화면 경로)이고, 이름도 실행 퍼널의 단계 이름을 쓴다.
  화면 정보가 없는 예전 리포트는 7단계 퍼널의 마지막 도달 단계를 쓴다.
- 영향(impact): 시드마다 이 문제로 이탈한 세션 수의 [최솟값, 최댓값]
- 판정(verdict): 같은 과업을 실행한 리포트들을 비교한다
    · 계속 발생: 가장 최근 실행에도 있고, 그 전 실행에도 있었음
    · 새로 발견: 가장 최근 실행에서 처음 나옴 (그 전에 같은 과업을 실행한 적은 있음)
    · 재테스트 전: 같은 과업을 한 번만 실행해서 아직 비교할 실행이 없음
  가장 최근 실행에 없는 문제(그 뒤 실행에서 사라진 문제)는 목록에 넣지 않는다.
- 기술 실패·한도 초과는 이탈이 아니므로 세지 않는다 (리포트와 같은 기준).
- 모두 가상 페르소나의 모의 결과다. 실제 사용자 이탈이 아니다.
"""
from collections import Counter, defaultdict

from django.utils import timezone

from .aggregate import cause_names, funnel_stages
from .services import project_reports

VERDICT_ORDER = {"계속 발생": 0, "새로 발견": 1, "재테스트 전": 2}


def _date_label(dt):
    local = timezone.localtime(dt)
    return f"{local.month}월 {local.day}일"


def _report_issues(report):
    """리포트 1개 → {(task_id, 이탈 화면, 원인): {"seeds": Counter, "valid": n, ...}}, 리포트의 과업 id 집합"""
    data = report.data or {}
    seeds_of_task = defaultdict(set)
    found = defaultdict(Counter)
    for row in data.get("sessions", []):
        seeds_of_task[row["task_id"]].add(row["seed"])
        if row.get("kind") != "dropout":
            continue
        where = row.get("terminal_screen") or f"stage:{row.get('terminal_stage') or 'unknown'}"
        key = (row["task_id"], where, row.get("primary_cause") or "unknown")
        found[key][row["seed"]] += 1
    valid = {t["task_id"]: t.get("valid_sessions", 0) for t in data.get("tasks", [])}
    names = {t["task_id"]: t.get("name", t["task_id"]) for t in data.get("tasks", [])}
    screen_names = {t["task_id"]: {s["path"]: s["name"] for s in (t.get("journey_funnel") or {}).get("stages", [])}
                    for t in data.get("tasks", [])}
    return {key: {"seeds": counts, "all_seeds": seeds_of_task[key[0]], "valid": valid.get(key[0], 0),
                  "task_name": names.get(key[0], key[0]), "screen_names": screen_names.get(key[0], {})}
            for key, counts in found.items()}, set(names)


def _stage_name(where, screen_names, stage_names):
    if where.startswith("stage:"):                       # 화면 정보가 없는 예전 리포트
        return stage_names.get(where[len("stage:"):], "알 수 없는 단계")
    return screen_names.get(where) or f"기타 화면 ({where})"


def project_issues(project):
    """프로젝트의 발견한 문제 목록. 리포트는 실행마다 가장 최근 revision 하나만 쓴다."""
    reports = list(reversed(project_reports(project)))          # 오래된 실행 → 최신 실행
    stage_names = dict(funnel_stages())
    causes = cause_names()
    history = []                                                 # [(report, issues, tasks)]
    for report in reports:
        issues, tasks = _report_issues(report)
        history.append((report, issues, tasks))

    latest_for_task = {}                                         # 과업마다 그 과업을 실행한 가장 최근 리포트
    runs_of_task = defaultdict(int)
    for report, issues, tasks in history:
        for task_id in tasks:
            latest_for_task[task_id] = (report, issues)
            runs_of_task[task_id] += 1

    items = []
    for task_id, (report, issues) in latest_for_task.items():
        for key, info in issues.items():
            if key[0] != task_id:
                continue
            _, stage_key, cause_key = key
            seen = [r for r, past, _ in history if key in past]
            earlier_with_task = runs_of_task[task_id] > 1
            if len(seen) > 1:
                verdict = "계속 발생"
            elif earlier_with_task:
                verdict = "새로 발견"
            else:
                verdict = "재테스트 전"
            per_seed = [info["seeds"].get(seed, 0) for seed in sorted(info["all_seeds"])] or [0]
            dropouts = sum(info["seeds"].values())
            stage = _stage_name(stage_key, info["screen_names"], stage_names)
            cause = causes.get(cause_key, "원인 미상")
            evidence = (f"'{info['task_name']}' 과업에서 가상 세션 {dropouts}개가 '{stage}' 화면에서 이탈했고, "
                        f"주된 원인은 '{cause}'로 기록됐어요 (유효 세션 {info['valid']}개 중).")
            if verdict == "계속 발생":
                evidence += f" 이전 실행 {len(seen) - 1}번에서도 같은 문제가 있었어요."
            first = seen[0]
            items.append({
                "id": f"{task_id}:{stage_key}:{cause_key}",
                "cause_key": cause_key, "cause": cause,
                "stage_key": stage_key, "stage": stage,
                "project_id": project.pk, "project_name": getattr(project, "name", ""),
                "task_id": task_id, "task": info["task_name"],
                "impact": [min(per_seed), max(per_seed)], "dropouts": dropouts, "valid_sessions": info["valid"],
                "verdict": verdict,
                "first_seen_at": first.created_at.isoformat(), "first_seen": _date_label(first.created_at),
                "last_seen_at": report.created_at.isoformat(),
                "run_id": report.run_ref, "report_id": str(report.id),
                "evidence": evidence,
            })
    items.sort(key=lambda i: (VERDICT_ORDER[i["verdict"]], -i["dropouts"], i["task_id"], i["id"]))
    return items


def issues_dict(projects):
    items = [item for project in projects for item in project_issues(project)]
    items.sort(key=lambda i: (VERDICT_ORDER[i["verdict"]], -i["dropouts"], str(i["project_id"]), i["id"]))
    return {"count": len(items), "items": items,
            "note": "가상 페르소나의 모의 결과입니다. 실제 사용자 이탈률이 아닙니다."}