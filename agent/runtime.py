import asyncio
import inspect
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from .behavior import PersonaMeter
from .browser import observe, act, origin, login
from .funnel import funnel_stage
from .schemas import RunInput, SessionResult


ISSUE_SUMMARIES = {
    "input_burden": "Visible required fields exceed persona budget",
    "cost": "Extra cost appeared late in the flow",
    "navigation": "Persona wandered or went back without progress",
    "value": "Price was not visible on the detail screen",
    "trust": "Personal information was requested before payment",
    "technical": "The screen was slow or showed an error message",
    "accessibility": "Unlabeled fields or small touch targets",
}


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_CONSECUTIVE_FAILURES = 3     # 행동이 연달아 이만큼 실패하면 기술 실패로 끝낸다


async def execute_run(config: RunInput, policy_factory, output_dir="artifacts", emit_event=None, should_cancel=None,
                      credentials=None, judge=None):
    """Call from a Django worker's async boundary; callbacks may be sync or async.

    Only trusted test sites are supported. Exact-origin routing is not an SSRF firewall.
    Each invocation creates a unique attempt directory; no existing evidence is overwritten.
    credentials: {persona_id: {"username", "password"}} for config.login_url. Kept in memory only, never written.
    judge: success_criteria 판정기 (agent/judge.py). 정책이 done 을 고르면 화면을 따로 판정한다.
    """
    if config.success_criteria and judge is None:
        raise ValueError("success_criteria needs a judge")
    attempt = uuid4().hex
    root = Path(output_dir) / config.run_id / attempt
    root.mkdir(parents=True, exist_ok=False)
    sequence = 0

    async def emit(kind, session_id, payload):
        nonlocal sequence
        sequence += 1
        event = {"schema_version": "1.0", "event_id": uuid4().hex, "run_id": config.run_id,
                 "attempt_id": attempt, "session_id": session_id, "sequence": sequence,
                 "occurred_at": datetime.now(timezone.utc).isoformat(), "type": kind, "payload": payload}
        with (root / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if emit_event:
            value = emit_event(event)
            if inspect.isawaitable(value):
                await value

    async def cancelled():
        if should_cancel is None:
            return False
        value = should_cancel()
        return bool(await value if inspect.isawaitable(value) else value)

    (root / "input.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    semaphore = asyncio.Semaphore(config.concurrency)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            async def session(persona):
                async with semaphore:
                    sid = f"session_{persona.id}"
                    folder = root / sid
                    folder.mkdir()
                    started = time.monotonic()
                    state = {"status": "budget_exceeded", "reason": "Step limit reached", "steps": 0,
                             "calls": 0, "input": 0, "output": 0, "by_model": {}, "issues": [], "artifact": None,
                             "judgments": 0, "blocked_writes": 0, "blocked_resources": 0}
                    context = await browser.new_context(viewport={"width": config.viewport_width, "height": config.viewport_height},
                                                        service_workers="block")
                    policy = None
                    blocked = []
                    logging_in = [config.login_url is not None]   # 로그인 요청(POST)은 read_only 여도 보낸다
                    async def guard(route):
                        request = route.request
                        if origin(request.url) not in config.allowed_origins:
                            # 허용 출처 밖 요청은 모두 막는다. 세션을 끝내는 건 화면 자체가 밖으로 나가려 할 때(최상위 이동)뿐이다.
                            # 분석 스크립트·폰트·이미지 같은 외부 부속 요청은 막고 기록만 한다 (사이트가 흔히 쓰므로)
                            if request.is_navigation_request() and request.frame.parent_frame is None:
                                blocked.append(request.url)
                            else:
                                state["blocked_resources"] += 1
                            await route.abort()
                        elif config.read_only and not logging_in[0] and route.request.method not in SAFE_METHODS:
                            state["blocked_writes"] += 1      # 운영 자료가 바뀌지 않게 쓰기 요청만 막는다
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", guard)
                    # Disable websocket connections outside the HTTP route guard.
                    await context.route_web_socket("**/*", lambda ws: ws.close())
                    page = await context.new_page()
                    page.set_default_timeout(5000)
                    history = []
                    failures = [0]                    # 연속으로 실패한 행동 수
                    seen_signals = set()
                    meter = PersonaMeter(persona, config.seed)
                    last_action = None
                    stage = None
                    wait_seconds = 0.0

                    async def capture(name):
                        path = folder / name
                        await page.screenshot(path=str(path))
                        state["artifact"] = str(path)
                        return str(path)

                    judged_screens = set()

                    async def judge_screen(obs, step_id, trigger):
                        """지금 화면을 성공 기준으로 판정한다. 판정 수 한도를 넘으면 None (판정 안 함)."""
                        if state["judgments"] >= config.max_judgments_per_session:
                            return None
                        state["judgments"] += 1
                        verdict, used = await judge.judge(config.success_criteria, config.goal, obs)
                        for model_id, u in used.items():
                            total = state["by_model"].setdefault(model_id, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
                            for key in total:
                                total[key] += u.get(key, 0)
                            state["calls"] += u.get("calls", 0)
                            state["input"] += u.get("input_tokens", 0)
                            state["output"] += u.get("output_tokens", 0)
                        await emit("success.judged", sid, {"step_id": step_id, "achieved": verdict.achieved, "trigger": trigger,
                                                           "reason": verdict.reason, "model": getattr(judge, "model", None)})
                        return verdict

                    async def body():
                        nonlocal policy, last_action, stage, wait_seconds
                        await emit("session.started", sid, {"persona": persona.model_dump()})
                        if await cancelled():
                            state.update(status="cancelled", reason="Cancellation requested")
                            return
                        policy = policy_factory()
                        if config.login_url:
                            account = (credentials or {}).get(persona.id)
                            ok = False
                            if account:
                                try:
                                    ok = await login(page, config.login_url, account["username"], account["password"])
                                except Exception:
                                    ok = False            # 예외 문구에 입력값이 섞이지 않게 내용은 버린다
                            logging_in[0] = False
                            await emit("login.completed", sid, {"ok": ok, "has_account": bool(account)})
                            if not ok:
                                state.update(status="blocked_auth",
                                             reason="Test account login failed" if account else "No test account for persona")
                                return
                        response = await page.goto(config.target_url, wait_until="domcontentloaded", timeout=15000)
                        if response and response.status >= 400:
                            state.update(status="site_failure", reason=f"HTTP {response.status}")
                            await capture("http-error.png")
                            return
                        for step in range(config.max_steps):
                            if await cancelled():
                                state.update(status="cancelled", reason="Cancellation requested")
                                return
                            if origin(page.url) not in config.allowed_origins:
                                state.update(status="blocked_auth", reason="Navigation left allowed origins")
                                return
                            obs = await observe(page)
                            state["steps"] += 1
                            step_id = f"step_{step:03d}"
                            artifact = await capture(f"{step_id}.png")
                            (folder / f"{step_id}.json").write_text(json.dumps(
                                {k: v for k, v in obs.items() if k != "screenshot"}, ensure_ascii=False, indent=2), encoding="utf-8")
                            stage = funnel_stage(obs["url"], config.success_path, config.stage_paths)
                            point = meter.observe(stage, obs["required_fields"], obs["text"],
                                                  obs.get("cost_warning_boxes", []), last_action, obs["elements"],
                                                  config.viewport_width, wait_seconds)
                            await emit("observation.created", sid, {"step_id": step_id, "url": obs["url"],
                                "title": (obs.get("title") or "")[:200], "stage": stage,
                                "artifact": artifact, "required_fields": obs["required_fields"], "text": obs["text"],
                                "frustration_score": point["score"], "frustration_threshold": point["threshold"]})
                            # Independent environment predicate; model's completion claim is ignored.
                            if config.success_path and urlsplit(page.url).path == config.success_path:
                                state.update(status="success", reason="Configured success path observed")
                                return
                            # 새 화면에 들어서면 정책의 done 을 기다리지 않고 판정한다 (목표 화면에 와서도 계속 진행하는 경우).
                            # 시작 화면은 건너뛴다. 판정 수는 max_judgments_per_session 안에서만.
                            screen = urlsplit(page.url).path
                            if not step:
                                judged_screens.add(screen)          # 시작 화면
                            elif config.success_criteria and screen not in judged_screens:
                                judged_screens.add(screen)
                                verdict = await judge_screen(obs, step_id, "new_screen")
                                if verdict is not None and verdict.achieved:
                                    state.update(status="success", reason="Success criteria judged achieved on screen")
                                    return
                            for category in point["signals"]:
                                if category in seen_signals:
                                    continue          # 같은 원인은 처음 한 번만 issue로 남김
                                seen_signals.add(category)
                                issue = {"category": category, "summary": ISSUE_SUMMARIES[category], "stage": stage,
                                    "evidence_step_ids": [step_id], "artifact": artifact,
                                    "contribution": point["signals"][category]}
                                if category == "input_burden":
                                    issue.update(observed_fields=obs["required_fields"],
                                                 configured_limit=persona.max_required_fields)
                                state["issues"].append(issue)
                                await emit("friction.detected", sid, {**issue, "frustration_score": point["score"]})
                            if point["crossed"]:
                                cause = meter.primary_cause()
                                for issue in state["issues"]:
                                    issue["primary"] = issue["category"] == cause   # 이탈을 일으킨 주 원인 표시
                                state.update(status="simulated_dropout", reason="Configured frustration threshold exceeded")
                                return
                            state["calls"] += int(getattr(policy, "name", "") != "scripted_fixture")
                            action, usage = await policy.decide(obs, persona, config.goal, history)
                            if usage.get("by_model"):        # 한 스텝에 모델을 여러 번 부르는 정책 (Claude 혼합)
                                for model_id, used in usage["by_model"].items():
                                    total = state["by_model"].setdefault(model_id, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
                                    for key in total:
                                        total[key] += used.get(key, 0)
                                state["calls"] += sum(used.get("calls", 0) for used in usage["by_model"].values()) - 1
                            state["input"] += usage.get("input_tokens", 0)
                            state["output"] += usage.get("output_tokens", 0)
                            if config.max_tokens_per_session and state["input"] + state["output"] >= config.max_tokens_per_session:
                                state.update(status="budget_exceeded", reason="Token budget reached")   # 비용 상한
                                return
                            if action.kind == "stop":
                                state.update(status="agent_failure", reason="Policy stopped without verified completion")
                                return
                            if action.kind == "done":
                                # 정책의 '다 했다'는 주장은 성공이 아니다. 성공 기준이 있으면 별도 판정 모델이 지금 화면으로 확인한다.
                                if not config.success_criteria:
                                    note = "not verified: success is checked by the configured success path only"
                                else:
                                    verdict = await judge_screen(obs, step_id, "done")
                                    if verdict is None:
                                        note = "not verified: judgment limit reached for this session"
                                    elif verdict.achieved:
                                        state.update(status="success", reason="Success criteria judged achieved on screen")
                                        return
                                    else:
                                        note = f"not achieved yet: {verdict.reason}"
                                history.append({**action.model_dump(), "result": note})
                                last_action = "done"
                                continue
                            acted = time.monotonic()
                            try:
                                await act(page, action, obs)
                            except Exception as exc:
                                # 처음 보는 사이트에서는 누를 수 없는 요소를 고를 수 있다. 세션을 바로 끝내지 않고
                                # 실패를 행동 기록에 남겨 다음 스텝에서 정책이 다른 방법을 고르게 한다.
                                # (예외 문구는 화면·입력값이 섞일 수 있어 종류만 남긴다)
                                failures[0] += 1
                                await emit("action.failed", sid, {"step_id": step_id, "action": action.model_dump(),
                                                                  "error": type(exc).__name__})
                                history.append({**action.model_dump(), "result": f"failed ({type(exc).__name__}): "
                                                "this action could not be performed on the current screen"})
                                last_action = "failed"
                                if failures[0] >= MAX_CONSECUTIVE_FAILURES:
                                    state.update(status="agent_failure",
                                                 reason=f"{MAX_CONSECUTIVE_FAILURES} actions failed in a row")
                                    return
                                continue
                            failures[0] = 0
                            history.append(action.model_dump())
                            last_action = action.kind
                            await emit("action.completed", sid, {"step_id": step_id, "action": action.model_dump()})
                            if blocked:
                                state.update(status="blocked_auth", reason="An out-of-scope network request was blocked")
                                return
                            wait_seconds = time.monotonic() - acted     # 요청 간격(대기)은 빼고 잰다
                            if config.action_interval_seconds:
                                await asyncio.sleep(config.action_interval_seconds)   # 요청 간격
                        # Verify completion even when the final permitted action succeeded.
                        await capture("final.png")
                        if config.success_path and urlsplit(page.url).path == config.success_path:
                            state.update(status="success", reason="Success path observed after final action")

                    try:
                        async with asyncio.timeout(config.timeout_seconds):
                            await body()
                    except TimeoutError:
                        state.update(status="budget_exceeded", reason="Session time limit reached")
                    except asyncio.CancelledError:
                        state.update(status="cancelled", reason="Worker task cancelled")
                        raise
                    except Exception as exc:
                        # Never persist exception messages that might contain provider secrets or page data.
                        state.update(status="agent_failure", reason=f"Execution error: {type(exc).__name__}")
                    finally:
                        try:
                            if policy is not None and hasattr(policy, "close"):
                                await policy.close()
                        finally:
                            await context.close()
                    result = SessionResult(session_id=sid, persona_id=persona.id,
                        policy=getattr(policy, "name", "custom_or_uninitialized"), model_id=getattr(policy, "model", None),
                        status=state["status"], reason=state["reason"], steps=state["steps"],
                        duration_seconds=round(time.monotonic()-started, 3), model_calls=state["calls"],
                        input_tokens=state["input"], output_tokens=state["output"], usage_by_model=state["by_model"],
                        judgments=state["judgments"],
                        issues=state["issues"], final_artifact=state["artifact"],
                        terminal_stage=stage, stages_reached=list(meter.stages), blocked_writes=state["blocked_writes"],
                        blocked_resources=state["blocked_resources"])
                    (folder / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
                    await emit("session.finished", sid, result.model_dump())
                    return result

            results = await asyncio.gather(*(session(p) for p in config.personas))
        finally:
            await browser.close()
    summary = {"schema_version": "1.0", "run_id": config.run_id, "attempt_id": attempt,
        "policy": sorted({r.policy for r in results}), "sessions": [r.model_dump() for r in results],
        "metrics": {"started": len(results), "success": sum(r.status == "success" for r in results),
            "technical_failure": sum(r.status == "agent_failure" for r in results),
            "model_calls": sum(r.model_calls for r in results), "cost": None, "cost_completeness": "unavailable"}}
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_dir": str(root), **summary}