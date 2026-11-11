import argparse
import asyncio
import json
from pathlib import Path
from .schemas import RunInput, Persona
from .policies import ScriptedPolicy, ShopScriptedPolicy, OpenAIPolicy
from .runtime import execute_run
from .demo import demo_server
from .shop import shop_server


def main():
    parser = argparse.ArgumentParser(description="DropSpot agent: trusted test sites only")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--demo", choices=["normal", "problem", "fixed"])
    parser.add_argument("--shop", choices=["normal", "problem", "fixed"])
    parser.add_argument("--case", choices=["all", "signup", "fee", "cta"], default="all")
    parser.add_argument("--policy", choices=["scripted", "openai"], default="scripted")
    parser.add_argument("--model", help="Required for openai; exact model ID available to your account")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", default="artifacts")
    args = parser.parse_args()
    if sum(map(bool, [args.input, args.demo, args.shop])) != 1:
        parser.error("Choose exactly one of --input, --demo or --shop")
    if args.repeat < 1 or args.repeat > 100:
        parser.error("repeat must be 1..100")
    if args.policy == "openai" and not args.model:
        parser.error("--model is required for model-driven execution")
    factory = (ShopScriptedPolicy if args.shop else ScriptedPolicy) if args.policy == "scripted" else lambda: OpenAIPolicy(args.model)
    # Fail before launching a browser if authentication is unavailable.
    if args.policy == "openai":
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            parser.error("OPENAI_API_KEY is not set")

    async def run(config):
        reports = []
        for _ in range(args.repeat):
            report = await execute_run(config, factory, args.output)
            metadata = {"policy": args.policy, "model": args.model, "model_driven": args.policy == "openai"}
            report.update(metadata)
            Path(report["output_dir"], "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            reports.append(report)
        print(json.dumps({"policy": args.policy, "model_driven": args.policy == "openai",
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
