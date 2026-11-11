import asyncio
import inspect
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from .browser import observe, act, origin
from .schemas import RunInput, SessionResult


async def execute_run(config: RunInput, policy_factory, output_dir="artifacts", emit_event=None, should_cancel=None):
    """Call from a Django worker's async boundary; callbacks may be sync or async.

    Only trusted test sites are supported. Exact-origin routing is not an SSRF firewall.
    Each invocation creates a unique attempt directory; no existing evidence is overwritten.
    """
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
                             "calls": 0, "input": 0, "output": 0, "issues": [], "artifact": None}
                    context = await browser.new_context(viewport={"width": 1000, "height": 800}, service_workers="block")
                    policy = None
                    blocked = []
                    async def guard(route):
                        if origin(route.request.url) not in config.allowed_origins:
                            blocked.append(route.request.url)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", guard)
                    # Disable websocket connections outside the HTTP route guard.
                    await context.route_web_socket("**/*", lambda ws: ws.close())
                    page = await context.new_page()
                    page.set_default_timeout(5000)
                    history = []
                    seen_signals = set()
                    frustration = 0.0

                    async def capture(name):
                        path = folder / name
                        await page.screenshot(path=str(path))
                        state["artifact"] = str(path)
                        return str(path)

                    async def body():
                        nonlocal policy, frustration
                        await emit("session.started", sid, {"persona": persona.model_dump()})
                        if await cancelled():
                            state.update(status="cancelled", reason="Cancellation requested")
                            return
                        policy = policy_factory()
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
                            await emit("observation.created", sid, {"step_id": step_id, "url": obs["url"],
                                "artifact": artifact, "required_fields": obs["required_fields"], "text": obs["text"]})
                            # Independent environment predicate; model's completion claim is ignored.
                            if urlsplit(page.url).path == config.success_path:
                                state.update(status="success", reason="Configured success path observed")
                                return
                            if obs["required_fields"] > persona.max_required_fields and "input_burden" not in seen_signals:
                                seen_signals.add("input_burden")
                                frustration += 70
                                issue = {"category": "input_burden", "summary": "Visible required fields exceed persona budget",
                                    "evidence_step_ids": [step_id], "artifact": artifact,
                                    "observed_fields": obs["required_fields"], "configured_limit": persona.max_required_fields}
                                state["issues"].append(issue)
                                await emit("friction.detected", sid, {**issue, "frustration_score": frustration})
                            if frustration >= persona.frustration_threshold:
                                state.update(status="simulated_dropout", reason="Configured frustration threshold exceeded")
                                return
                            state["calls"] += int(getattr(policy, "name", "") != "scripted_fixture")
                            action, usage = await policy.decide(obs, persona, config.goal, history)
                            state["input"] += usage.get("input_tokens", 0)
                            state["output"] += usage.get("output_tokens", 0)
                            if action.kind == "stop":
                                state.update(status="agent_failure", reason="Policy stopped without verified completion")
                                return
                            await act(page, action, obs)
                            history.append(action.model_dump())
                            await emit("action.completed", sid, {"step_id": step_id, "action": action.model_dump()})
                            if blocked:
                                state.update(status="blocked_auth", reason="An out-of-scope network request was blocked")
                                return
                        # Verify completion even when the final permitted action succeeded.
                        await capture("final.png")
                        if urlsplit(page.url).path == config.success_path:
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
                        input_tokens=state["input"], output_tokens=state["output"],
                        issues=state["issues"], final_artifact=state["artifact"])
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
