"""Safe command-line interface for the Recall Relay package."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from recall_relay import RecallRelay, RecallRelayError

def _intent(values: list[str]) -> dict[str, str]:
    out = {}
    for value in values:
        key, separator, setting = value.partition("=")
        if not separator or not key or not setting: raise SystemExit("--intent must use key=value")
        out[key] = setting
    return out

def _relay() -> RecallRelay:
    return RecallRelay(os.environ.get("RECALL_URL", ""), recall_key=os.environ.get("RECALL_API_KEY"), gemini_key=os.environ.get("GEMINI_API_KEY"), openai_key=os.environ.get("OPENAI_API_KEY"), workspace_id=os.environ.get("RECALL_WORKSPACE_ID"), workspace_key=os.environ.get("RECALL_WORKSPACE_KEY"))

def main() -> None:
    parser=argparse.ArgumentParser(prog="recall-relay", description="Use your Gemini key locally and keep a private Recall memory.")
    commands=parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check configuration without printing secrets.")
    for name, description, default_model in [("gemini", "Check Recall, then call Gemini only on a safe miss.", "gemini-3.1-flash-image"), ("openai", "Check Recall, then call OpenAI only on a safe miss.", "gpt-image-1")]:
        generate=commands.add_parser(name, help=description)
        generate.add_argument("prompt")
        generate.add_argument("--tag", action="append", default=[])
        generate.add_argument("--intent", action="append", default=[], metavar="KEY=VALUE")
        generate.add_argument("--model", default=default_model)
        generate.add_argument("--output", default="recall-output.png")
        generate.add_argument("--cost-usd", type=float, help="Effective provider cost for this output; stored as caller-reported USD.")
        if name == "openai": generate.add_argument("--size")
    args=parser.parse_args()
    if args.command == "doctor":
        missing=[]
        if not os.environ.get("RECALL_URL"): missing.append("RECALL_URL")
        workspace_id, workspace_key = os.environ.get("RECALL_WORKSPACE_ID"), os.environ.get("RECALL_WORKSPACE_KEY")
        if bool(workspace_id) != bool(workspace_key):
            missing.append("RECALL_WORKSPACE_ID and RECALL_WORKSPACE_KEY must be supplied together")
        if not (workspace_id and workspace_key) and not os.environ.get("RECALL_API_KEY"):
            missing.append("RECALL workspace credentials or RECALL_API_KEY")
        if not any(os.environ.get(name) for name in ("GEMINI_API_KEY", "OPENAI_API_KEY")):
            missing.append("GEMINI_API_KEY or OPENAI_API_KEY")
        print(json.dumps({"ready":not missing,"missing":missing,"note":"Secrets are intentionally not displayed."}))
        raise SystemExit(1 if missing else 0)
    relay=_relay()
    try:
        result=relay.generate_gemini(args.prompt, model=args.model, tags=args.tag, intent=_intent(args.intent), cost_usd=args.cost_usd) if args.command == "gemini" else relay.generate_openai(args.prompt, model=args.model, tags=args.tag, intent=_intent(args.intent), size=getattr(args, "size", None), cost_usd=args.cost_usd)
    except RecallRelayError as exc:
        print(json.dumps({"error":str(exc), "status":exc.status}), file=sys.stderr)
        raise SystemExit(2) from None
    if result.media: Path(args.output).write_bytes(result.media)
    print(json.dumps({"status":result.status,"generation_id":result.generation.get("gen_id"),"asset_url":result.generation.get("asset_url"),"receipt_id":result.receipt.get("receipt_id"),"output":args.output if result.media else None}))

if __name__ == "__main__": main()
