import argparse
import asyncio
import json
from pathlib import Path
from .schemas import RunInput, Persona
from .policies import ScriptedPolicy, ShopScriptedPolicy, OpenAIPolicy, ClaudePolicy, CLAUDE_MODEL, CLAUDE_ESCALATION_MODEL
from .runtime import execute_run
from .demo import demo_server
from .shop import shop_server


CREDENTIALS_ENV = "DROPSPOT_CREDENTIALS"


def read_credentials():
    """테스트 계정: 환경변수 DROPSPOT_CREDENTIALS = {"페르소나ID": {"username": ..., "password": ...}}.

    입력 파일(input.json)은 실행 폴더에 그대로 복사되므로 계정은 넣지 않고 이 환경변수로만 받는다.
    읽은 뒤 바로 지워서 이 프로세스가 띄우는 다른 프로세스에 물려주지 않는다.
    """
    import os
    raw = os.environ.pop(CREDENTIALS_ENV, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(pid): {"username": str(v["username"]), "password": str(v["password"])} for pid, v in data.items()}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SystemExit(f"{CREDENTIALS_ENV} must be JSON {{persona_id: {{username, password}}}}") from None


def main():
    parser = argparse.ArgumentParser(description="DropSpot agent: trusted test sites only")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--demo", choices=["normal", "problem", "fixed"])
    parser.add_argument("--shop", choices=["normal", "problem", "fixed"])
    parser.add_argument("--case", choices=["all", "signup", "fee", "cta"], default="all")
    # shop_scripted: 데모 쇼핑몰 화면 문구를 아는 고정 정책. 백엔드가 --input으로 쇼핑몰을 돌릴 때 쓴다 (API 키 불필요)
    # claude: 기본 모델이 고르고 막힌 스텝만 상위 모델에게 다시 묻는다 (ANTHROPIC_API_KEY 필요). 기본은 둘 다 Haiku, 서버 .env 의
    # DROPSPOT_CLAUDE_MODEL·DROPSPOT_ESCALATION_MODEL 이나 --model / --escalation-model 로 바꿀 수 있다 (agent/model_env.py)
    parser.add_argument("--policy", choices=["scripted", "shop_scripted", "openai", "claude"], default="scripted")
    parser.add_argument("--model", help=f"Required for openai; exact model ID available to your account (claude default: {CLAUDE_MODEL})")
    parser.add_argument("--escalation-model", help=f"claude only: model for stuck steps (default: {CLAUDE_ESCALATION_MODEL})")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", default="artifacts")
    args = parser.parse_args()
    if sum(map(bool, [args.input, args.demo, args.shop])) != 1:
        parser.error("Choose exactly one of --input, --demo or --shop")
    if args.repeat < 1 or args.repeat > 100:
        parser.error("repeat must be 1..100")
    if args.policy == "openai" and not args.model:
        parser.error("--model is required for model-driven execution")
    if args.escalation_model and args.policy != "claude":
        parser.error("--escalation-model is only for --policy claude")
    model_driven = args.policy in {"openai", "claude"}
    if args.policy == "openai":
        factory = lambda: OpenAIPolicy(args.model)
    elif args.policy == "claude":
        args.model = args.model or CLAUDE_MODEL
        escalation = args.escalation_model or CLAUDE_ESCALATION_MODEL
        factory = lambda: ClaudePolicy(args.model, escalation)
    elif args.policy == "shop_scripted" or args.shop:
        factory = ShopScriptedPolicy
    else:
        factory = ScriptedPolicy
    # Fail before launching a browser if authentication is unavailable.
    import os
    if args.policy == "openai" and not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is not set")
    if args.policy == "claude" and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        parser.error("ANTHROPIC_API_KEY is not set")

    credentials = read_credentials()

    async def run(config):
        # 말로 쓴 성공 기준은 행동 모델과 별도의 판정 모델이 확인한다 (agent/judge.py)
        judge = None
        if config.success_criteria:
            from .judge import ClaudeJudge
            try:
                judge = ClaudeJudge()
            except ValueError as exc:
                raise SystemExit(str(exc)) from None
        reports = []
        try:
            for _ in range(args.repeat):
                reports.append(await execute_run(config, factory, args.output, judge=judge, credentials=credentials))
        finally:
            if judge is not None:
                await judge.close()
        for report in reports:
            metadata = {"policy": args.policy, "model": args.model, "model_driven": model_driven}
            if args.policy == "claude":
                metadata["escalation_model"] = escalation
            report.update(metadata)
            Path(report["output_dir"], "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"policy": args.policy, "model_driven": model_driven,
            "runs": [{"output_dir": r["output_dir"], "metrics": r["metrics"]} for r in reports]}, ensure_ascii=False, indent=2))
        return all(s["status"] in {"success", "simulated_dropout"} for r in reports for s in r["sessions"])

    if args.shop:
        with shop_server() as base:
            variant = "problem" if args.shop == "problem" else "fixed"
            config = RunInput(run_id="shop_"+args.shop+"_"+args.case,
                target_url=base+"/?variant="+variant+"&case="+args.case,
                allowed_origins=[base], goal="반찬 식탁을 장바구니에 담고 합성 배송 정보로 모의 주문 완료",
                success_path="/order/success", max_steps=35,
                personas=[Persona(id="participant_A"), Persona(id="participant_B")])
            ok = asyncio.run(run(config))
    elif args.demo:
        with demo_server() as base:
            config = RunInput(run_id="demo_"+args.demo, target_url=base+"/"+args.demo,
                allowed_origins=[base], goal="식별자를 입력하고 모의 주문 완료", success_path="/success",
                personas=[Persona(id="participant_A"), Persona(id="participant_B")])
            ok = asyncio.run(run(config))
    else:
        ok = asyncio.run(run(RunInput.model_validate_json(args.input.read_text(encoding="utf-8"))))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
