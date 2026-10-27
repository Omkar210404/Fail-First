"""Evaluate FailFirst on real, closed bug reports.

A case counts as truly REPRODUCED only if the generated test FAILS on the buggy commit (checked by the
harness during the run) and PASSES on the fix commit (checked here in a fresh sandbox).
A REPRODUCED verdict that does not meet both is recorded as a false claim.

  python scripts/eval.py --check                         # validate eval/cases.json, runs nothing
  python scripts/eval.py --provider openai-compatible    # free with a local Ollama model
  python scripts/eval.py --provider anthropic            # PAID: every case costs API money

Needs Docker. Results go to eval/results/<timestamp>.json (gitignored).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from failfirst.agent import COMMIT, REPO_URL, Budget, check_test_on_commit, run_case  # noqa: E402
from failfirst.cli import load_dotenv  # noqa: E402
from failfirst.providers import make_provider  # noqa: E402

REQUIRED = ("id", "repo", "buggy_commit", "fix_commit", "licence")


def load_cases(path: Path) -> list[dict]:
    cases, problems = [], []
    for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
        if not c.get("enabled"):
            continue
        missing = [k for k in REQUIRED if not c.get(k)]
        if missing:
            problems.append(f"{c.get('id', '?')}: missing {', '.join(missing)}")
        elif not REPO_URL.match(c["repo"]) or not COMMIT.match(c["buggy_commit"]) or not COMMIT.match(c["fix_commit"]):
            problems.append(f"{c['id']}: repo or commit has the wrong format")
        elif not (c.get("issue_text") or c.get("issue_file")):
            problems.append(f"{c['id']}: needs issue_text or issue_file")
        else:
            if c.get("issue_file"):
                c["issue_text"] = (path.parent / c["issue_file"]).read_text(encoding="utf-8")
            cases.append(c)
    if problems:
        sys.exit("cases file problems:\n  " + "\n  ".join(problems))
    return cases


def main() -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=Path, default=ROOT / "eval" / "cases.json")
    ap.add_argument("--provider", choices=["anthropic", "openai-compatible"])
    ap.add_argument("--only", help="run a single case id")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--max-cost-usd", type=float, help="per-case cost cap (needs the price env vars)")
    ap.add_argument("--check", action="store_true", help="validate the cases file and exit")
    a = ap.parse_args()

    cases = [c for c in load_cases(a.cases) if not a.only or c["id"] == a.only]
    if a.check or not a.provider:
        print(f"{len(cases)} enabled case(s) in {a.cases}:")
        for c in cases:
            print(f"  {c['id']}: {c['repo']} buggy={c['buggy_commit']} fix={c['fix_commit']}")
        if not a.provider and not a.check:
            print("Pass --provider to run them.")
        return 0

    provider = make_provider(a.provider)
    rows = []
    for c in cases:
        print(f"\n=== {c['id']} ===")
        budget = Budget.from_env(max_steps=a.max_steps, max_cost_usd=a.max_cost_usd)
        r = run_case(provider, c["repo"], c["buggy_commit"], c["issue_text"], budget, ROOT / "runs" / "eval")
        on_fix = None
        if r["verdict"] == "REPRODUCED":
            on_fix = check_test_on_commit(c["repo"], c["fix_commit"], r["test_file"], r["test_content"])
        true_repro = r["verdict"] == "REPRODUCED" and on_fix == "passed"
        rows.append({"id": c["id"], "verdict": r["verdict"], "fails_on_buggy": r["harness_check"].startswith("passed"),
                     "result_on_fix_commit": on_fix, "true_reproduced": true_repro,
                     "false_claim": r["verdict"] == "REPRODUCED" and not true_repro,
                     "model_turns": r["model_turns"], "usage": r["usage"], "duration_seconds": r["duration_seconds"],
                     "run_dir": r["run_dir"]})
        print(f"verdict={r['verdict']} on_fix={on_fix} true_reproduced={true_repro}")

    summary = {k: sum(1 for x in rows if pred(x)) for k, pred in {
        "cases": lambda x: True, "true_reproduced": lambda x: x["true_reproduced"],
        "false_claims": lambda x: x["false_claim"], "not_reproduced": lambda x: x["verdict"] == "NOT_REPRODUCED",
        "needs_info": lambda x: x["verdict"] == "NEEDS_INFO"}.items()}
    out = ROOT / "eval" / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"provider": provider.name, "model": provider.model, "summary": summary, "cases": rows},
                              indent=2), encoding="utf-8")
    print(f"\nsummary: {summary}\nwritten to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
