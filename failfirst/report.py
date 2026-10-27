"""Write one folder per run: report.md, run.json and the generated test file."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path


def render_markdown(result: dict, meta: dict) -> str:
    u = result["usage"]
    cost = f"${u['estimated_cost_usd']:.4f}" if u.get("estimated_cost_usd") is not None else "not estimated"
    lines = [
        f"# FailFirst report: {result['verdict']}", "",
        "| | |", "|---|---|",
        f"| Repository | {meta['repo']} |", f"| Commit | `{meta['commit']}` |",
        f"| Provider | {meta['provider']} ({meta['model']}) |", f"| Sandbox | {meta['sandbox']} |",
        f"| Model turns | {result['model_turns']} |", f"| Tokens (in / out) | {u['input_tokens']} / {u['output_tokens']} |",
        f"| Estimated cost | {cost} |", f"| Duration | {result.get('duration_seconds', 0)} s |",
        f"| Stop reason | {result['stop_reason']} |", f"| Harness check | {result['harness_check']} |", "",
    ]
    if meta["provider"] == "scripted":
        lines += ["> Demo run: the model is a SCRIPT that replays fixed tool calls, not a real LLM.", ""]
    lines += ["## Explanation", "", result["explanation"] or "(none)", ""]
    if result.get("test_file"):
        lines += [f"## Test file: `{result['test_file']}`", "", "```python", (result["test_content"] or "").rstrip(), "```", ""]
    if result.get("last_test_run"):
        run = result["last_test_run"]
        lines += [f"## Last test run ({run['kind']}, exit code {run['exit_code']})", "", "```text", run["output"].rstrip(), "```", ""]
    lines += ["## Step log", ""]
    for s in result["steps"]:
        if s["type"] == "model":
            lines.append(f"- **turn {s['step']}, model:** {s['text'].strip()}")
        else:
            flag = " (error)" if s["is_error"] else ""
            lines.append(f"- **turn {s['step']}, {s['tool']}**{flag} `{json.dumps(s['args'])[:200]}` ({s['seconds']} s)")
    lines += ["", "## Bug report given to the agent", "", "```text", meta["issue"].strip(), "```", ""]
    return "\n".join(lines)


def write_report(result: dict, meta: dict, out_dir: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", meta["repo"].lower().split("github.com/")[-1])[:40].strip("-")
    run_dir = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(render_markdown(result, meta), encoding="utf-8")
    data = {**{k: v for k, v in meta.items() if k != "issue"}, **result}
    (run_dir / "run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if result.get("test_file") and result.get("test_content"):
        (run_dir / result["test_file"]).write_text(result["test_content"], encoding="utf-8")
    return run_dir
