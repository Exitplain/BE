#!/usr/bin/env python3
"""Run local evidence gates. Exit codes: 0 pass, 1 fail, 2 blocked."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    "EXEC-01": "test_real_browser_success_and_isolation",
    "DIAG-01": "test_problem_and_fixed",
    "EXEC-02": "test_invalid_target_is_failure_not_dropout",
    "EXEC-03": "test_cancellation_and_time_budget",
    "EXEC-04": "test_last_action_can_succeed",
    "CONTRACT-01": "test_rejects_path_traversal_and_duplicate_ids",
    "MODEL-MOCK-01": "test_model_adapter_passes_screenshot_and_validates_action",
    "MODEL-AUTH-01": "test_missing_auth_is_not_silently_replaced",
    "ACTION-01": "test_extended_form_actions",
    "ACTION-02": "test_navigation_and_bidirectional_scroll",
    "SHOP-01": "test_storefront_fixed_flow_and_signup_regression",
    "SHOP-02": "test_storefront_visible_fee_and_cta_conditions",
    "SHOP-03": "test_storefront_mobile_layout_and_completion_guard",
    "SAAS-01": "test_console_real_run_evidence_and_comparison",
    "SAAS-02": "test_console_project_settings_and_mobile",
    "SAAS-03": "test_console_rejects_unapproved_targets_and_foreign_origin",
    "SAAS-04": "test_console_fifty_agent_replay_is_separate_from_real_runs",
    "LIVE-01": "test_skyvern_connection_reuses_session_and_hides_credentials",
    "LIVE-02": "test_skyvern_missing_installation_is_explicit",
    "LIVE-03": "test_skyvern_checkpoint_refuses_nonlocal_cdp",
    "LIVE-04": "test_checkpoint_response_does_not_mutate_stored_evidence",
    "PERSONA-01": "test_persona_budgets_change_outcomes_without_using_variant",
    "PERSONA-02": "test_persona_start_rejects_overlap_and_snapshot_is_isolated",
    "PERSONA-03": "test_persona_api_rejects_customer_target_and_hides_local_paths",
    "FRUST-01": "test_frustration_terms_no_double_count_and_revisit",
    "ALIGN-01": "test_alignment_denominators_and_technical_exclusion",
    "ALIGN-02": "test_human_observations_stale_duplicate_and_persistence",
    "ALIGN-03": "test_frustration_alignment_ui_evidence_and_manual_entry",
    "TASK-01": "test_multi_task_normal_controls_and_distinct_problem_stops",
    "TASK-02": "test_suite_allocates_distinct_sessions_and_rejects_unknown_task",
    "TASK-03": "test_search_cost_is_persona_specific_and_fee_not_double_counted",
    "REPORT-01": "test_report_groups_locations_and_keeps_technical_failure_separate",
    "REPORT-02": "test_report_endpoints_historical_download_and_incomplete_guard",
    "REPORT-03": "test_report_ui_problem_evidence_and_markdown_download",
    "PDF-01": "test_pdf_completed_report_and_busy_incomplete_guards",
    "PDF-02": "test_report_pdf_button_downloads_selected_report",
    "VISUAL-01": "test_observed_bboxes_match_css_screenshot_and_skip_hidden_targets",
    "VISUAL-02": "test_report_bbox_viewer_and_generated_mockup_are_distinct",
    "DESIGN-01": "test_workspace_review_filter_and_accessible_selection",
    "SETUP-01": "test_preflight_discovers_real_local_entries_without_running_personas",
    "SETUP-02": "test_preflight_confirmation_scope_and_age_not_stereotyped",
    "SETUP-03": "test_guided_setup_confirms_features_tasks_age_before_start",
    "EXPLAIN-01": "test_session_answers_grounded_and_persisted",
    "EXPLAIN-02": "test_card_popup_questions_and_escape_restore_selection",
}


def source_fingerprint():
    digest = hashlib.sha256()
    files = []
    for folder in ("agent", "tests", "integrations", "scripts", "harness", ".agents/skills/dropspot-verify"):
        if (ROOT / folder).exists():
            files.extend(p for p in (ROOT / folder).rglob("*")
                         if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    files += [p for p in (ROOT / "pyproject.toml", Path(__file__)) if p.is_file()]
    for path in sorted(set(files)):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    frontend = Path(os.environ.get("DROPSPOT_FE_ROOT", str(ROOT.parent / "FE"))).expanduser().resolve()
    for folder in ("demo-shop", "saas-web"):
        for path in sorted((frontend / folder).rglob("*")):
            if path.is_file():
                digest.update(("FE/" + str(path.relative_to(frontend))).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("browser", "model"), default="browser")
    parser.add_argument("--model", help="Exact model ID; required for model profile")
    parser.add_argument("--allow-model-call", action="store_true", help="Explicitly enable billable model calls")
    parser.add_argument("--timeout", type=int, default=180, help="Gate process timeout in seconds")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "harness")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 900:
        parser.error("timeout must be 1..900 seconds")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    folder = args.output.resolve() / stamp
    folder.mkdir(parents=True, exist_ok=False)
    fingerprint = source_fingerprint()
    report = {
        "schema_version": "1.0", "profile": args.profile, "verdict": "blocked", "reason": "not executed",
        "started_at": datetime.now(timezone.utc).isoformat(), "python": sys.executable,
        "source_sha256": fingerprint, "artifact_dir": str(folder), "command": None,
        "checks": [], "actual_model_call": False, "limitations": [],
    }
    started = time.monotonic()

    def finish(verdict, reason):
        if not (folder / "execution.log").exists():
            (folder / "execution.log").write_text("No completed process log. " + reason + "\n", encoding="utf-8")
        report.update(verdict=verdict, reason=reason,
                      duration_seconds=round(time.monotonic() - started, 3),
                      finished_at=datetime.now(timezone.utc).isoformat())
        if source_fingerprint() != fingerprint:
            report.update(verdict="blocked", reason="Source changed during verification; rerun on a stable tree")
        (folder / "verdict.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"verdict": report["verdict"], "reason": report["reason"],
                          "report": str(folder / "verdict.json")}, ensure_ascii=False, indent=2))
        return {"pass": 0, "fail": 1, "blocked": 2}[report["verdict"]]

    modules = ["pytest", "playwright", "pydantic", "openai"] if args.profile == "browser" else ["playwright", "pydantic", "openai"]
    missing = [module for module in modules if importlib.util.find_spec(module) is None]
    if missing:
        return finish("blocked", "Missing dependencies: " + ", ".join(missing))

    if args.profile == "model":
        report["limitations"] = ["One controlled normal-flow smoke test; does not establish general GUI or UX accuracy."]
        if not os.environ.get("OPENAI_API_KEY"):
            return finish("blocked", "OPENAI_API_KEY is not configured; no model call executed")
        if not args.model:
            return finish("blocked", "An exact --model ID is required; no model call executed")
        if not args.allow_model_call:
            return finish("blocked", "--allow-model-call is required for billable execution; no model call executed")
        command = [sys.executable, "-m", "agent.cli", "--demo", "normal", "--policy", "openai",
                   "--model", args.model, "--output", str(folder / "model_runs")]
    else:
        report["limitations"] = ["Browser fixtures and SDK mocks do not validate live model calls or real customer dropout rates."]
        command = [sys.executable, "-m", "pytest", "-q", "--junitxml=" + str(folder / "pytest.xml")]
    report["command"] = command
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                   timeout=args.timeout, check=False)
    except subprocess.TimeoutExpired:
        # Do not serialize arbitrary provider output or secret-bearing exception messages.
        return finish("fail", "Verification process exceeded the configured timeout")
    except OSError as exc:
        return finish("blocked", "Cannot start verification process: " + type(exc).__name__)
    output = completed.stdout + "\n" + completed.stderr
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        output = output.replace(key, "[REDACTED]")
    (folder / "execution.log").write_text(output, encoding="utf-8")
    report["returncode"] = completed.returncode

    if args.profile == "browser":
        path = folder / "pytest.xml"
        if not path.exists():
            return finish("fail", "pytest did not produce its evidence report; inspect execution.log")
        try:
            cases = list(ET.parse(path).iter("testcase"))
        except ET.ParseError:
            return finish("fail", "pytest evidence XML is invalid")
        names = {case.attrib.get("name"): case for case in cases}
        report["test_counts"] = {
            "collected": len(cases),
            "failed": sum(case.find("failure") is not None for case in cases),
            "errors": sum(case.find("error") is not None for case in cases),
            "skipped": sum(case.find("skipped") is not None for case in cases),
        }
        for requirement, name in REQUIRED_TESTS.items():
            case = names.get(name)
            status = "blocked" if case is None or case.find("skipped") is not None else (
                "fail" if case.find("failure") is not None or case.find("error") is not None else "pass")
            report["checks"].append({"requirement": requirement, "test": name, "verdict": status})
        if completed.returncode != 0 or any(c["verdict"] == "fail" for c in report["checks"]):
            return finish("fail", "One or more tests failed; inspect pytest.xml and execution.log")
        if not cases or report["test_counts"]["skipped"] or any(c["verdict"] == "blocked" for c in report["checks"]):
            return finish("blocked", "Required tests are missing or skipped; the gate cannot claim completion")
        return finish("pass", "Required browser and contract tests passed; live model validation remains unverified")

    summaries = list((folder / "model_runs").rglob("summary.json"))
    if not summaries:
        return finish("fail", "No model run summary was produced; inspect execution.log")
    try:
        sessions = [s for path in summaries for s in json.loads(path.read_text(encoding="utf-8"))["sessions"]]
    except (ValueError, KeyError, TypeError):
        return finish("fail", "Invalid model run summary")
    report["actual_model_call"] = any(s.get("model_calls", 0) > 0 for s in sessions)
    verified = bool(sessions) and all(s.get("policy") == "openai" and s.get("model_calls", 0) > 0
                                      and s.get("status") == "success" for s in sessions)
    report["checks"].append({"requirement": "MODEL-LIVE-01", "verdict": "pass" if verified else "fail",
                              "session_count": len(sessions)})
    if completed.returncode != 0 or not verified:
        return finish("fail", "Live model smoke test did not complete all normal-flow sessions")
    return finish("pass", "Live model completed the controlled normal-flow GUI smoke test")


if __name__ == "__main__":
    raise SystemExit(main())
