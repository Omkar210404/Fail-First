"""Command line: failfirst run | demo | ui"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agent import Budget, run_case, run_demo
from .providers import ProviderError, make_provider


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from a local .env without overriding real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="failfirst", description="Turn a GitHub bug report into a failing pytest test.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the agent on a real repository (needs Docker and a model)")
    r.add_argument("--repo", required=True, help="public GitHub URL, e.g. https://github.com/owner/name")
    r.add_argument("--commit", required=True, help="the buggy commit (SHA, tag or branch)")
    r.add_argument("--issue-file", required=True, type=Path, help="text or Markdown file with the bug report")
    r.add_argument("--provider", required=True, choices=["anthropic", "openai-compatible"])
    r.add_argument("--max-steps", type=int, help="max model turns (default 25)")
    r.add_argument("--max-tokens", type=int, help="max input+output tokens per run (default 400000)")
    r.add_argument("--max-cost-usd", type=float, help="stop when the estimated cost reaches this "
                   "(needs FAILFIRST_PRICE_INPUT_PER_MTOK and FAILFIRST_PRICE_OUTPUT_PER_MTOK)")
    r.add_argument("--command-timeout", type=int, help="seconds per tool command (default 60)")
    r.add_argument("--out", default="runs", help="folder for reports (default runs/)")

    d = sub.add_parser("demo", help="offline demo: bundled project + SCRIPTED model, no key, no network")
    d.add_argument("--no-docker", action="store_true", help="force the local fallback (demo only)")
    d.add_argument("--provider", choices=["anthropic", "openai-compatible"],
                   help="use a real model on the demo project instead of the scripted one")
    d.add_argument("--out", default="runs")

    u = sub.add_parser("ui", help="local web page on 127.0.0.1 to run the demo or a real case")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--out", default="runs")

    a = ap.parse_args(argv)
    if a.cmd == "demo":
        try:
            provider = make_provider(a.provider) if a.provider else None
        except ProviderError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        result = run_demo(a.out, force_local=a.no_docker, provider=provider)
        print(f"\n{result['verdict']}: {result['explanation']}")
        return 0
    if a.cmd == "ui":
        from .ui import serve
        serve(a.port, a.out)
        return 0

    budget = Budget.from_env(max_steps=a.max_steps, max_tokens=a.max_tokens, max_cost_usd=a.max_cost_usd,
                             command_timeout=a.command_timeout)
    if budget.max_cost_usd is not None and budget.cost(0, 0) is None:
        print("error: --max-cost-usd needs FAILFIRST_PRICE_INPUT_PER_MTOK and FAILFIRST_PRICE_OUTPUT_PER_MTOK", file=sys.stderr)
        return 2
    try:
        provider = make_provider(a.provider)
        result = run_case(provider, a.repo, a.commit, a.issue_file.read_text(encoding="utf-8"), budget, a.out)
    except (ProviderError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"\n{result['verdict']}: {result['explanation']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
