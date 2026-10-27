# FailFirst

An AI agent that turns a GitHub bug report into a **failing pytest test**.

You give it a public GitHub repo, the buggy commit and the bug report text. It explores the code in a sandbox, writes the smallest test that should capture the bug, runs it, and returns a report with one verdict:

- **REPRODUCED**: the test fails, for the reason in the report
- **NOT_REPRODUCED**: no valid failing test (or a budget ran out)
- **NEEDS_INFO**: the report is too vague, or the repo could not be set up

Each report includes the test file, the reasoning and a step log. FailFirst never fixes the bug, never pushes anything and never comments on GitHub.

> Evaluation tooling is included but results have not been measured yet.

## Screenshots

The local web page (`failfirst ui`), before a run and after the offline demo. The demo uses a scripted model, not a real LLM.

![FailFirst home](docs/ui-home.png)

![FailFirst demo result](docs/ui-demo-result.png)

## Architecture

A **provider** layer hides the model behind one `complete()` call: Anthropic (official SDK, tool use), any OpenAI-compatible endpoint (including free local models through Ollama), or a **scripted** replay used by the demo. The **agent loop** sends the bug report (marked as untrusted data) and six tools to the model: `list_files`, `read_file`, `search_code`, `write_test`, `run_tests` and `finish`. It runs each tool call in the **sandbox** and enforces budgets (steps, tokens, optional cost cap, per-command timeouts, output trimming). The sandbox works in two phases. First, a throwaway Docker container with network access clones the repo at the commit and installs it. Then a second container with `--network none` runs the agent's commands, with CPU, memory and process limits, a read-only repo and one writable repro folder. When the model claims REPRODUCED, the harness checks that claim before accepting it: the test must fail twice in a row with a plain test failure, not an import, syntax, collection or fixture error. Every run writes `report.md`, `run.json` and the test file into `runs/`.

```mermaid
flowchart LR
  I[repo + commit + bug report] --> S1[Setup container<br/>network on: clone, venv, install]
  S1 --> S2[Agent container<br/>--network none, limits]
  P[Provider: Anthropic / OpenAI-compatible / scripted] <--> L[Agent loop + guardrails]
  L <--> S2
  L --> R[report.md + run.json + test file]
```

## What you need

- **Python 3.11+**
- **Docker** (Docker Desktop is free for personal use). It is required for real repositories. The demo runs without it.
- **A model**, only for real runs. Either:
  - a **free local model** through [Ollama](https://ollama.com) that supports tool calling (for example `qwen2.5-coder:7b`), or
  - **your own Anthropic API key**. This is a paid API and every run costs money.

## Run the offline demo (no key, no network)

```bash
git clone https://github.com/Omkar210404/failfirst && cd failfirst
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e .
failfirst demo
```

**Demo mode uses a SCRIPTED model, not a real LLM.** [demo/script.json](demo/script.json) replays the tool calls a real agent would plausibly make. Everything else is real: the sandbox, tools, test run, harness check and report. The bundled project [demo/shopcart](demo/shopcart) has a seeded off-by-one bug: `total()` uses `> 10` where the README promises a discount for "10 or more" items. The bug report is [demo/issue.md](demo/issue.md).

If Docker is running, the demo uses the Docker sandbox. The first time, Docker downloads `python:3.11-slim` and builds a small image, so that one step needs the network. Without Docker, the demo falls back to a **local subprocess mode with no isolation**, prints a warning, and refuses to run any project except the bundled demo. Use `failfirst demo --no-docker` to force the fallback.

Output from a real run on Windows without Docker (lines shortened):

```text
WARNING: Docker is not available, so the demo runs in LOCAL MODE with NO ISOLATION.
NOTE: demo mode uses a SCRIPTED model that replays fixed tool calls. It is not a real LLM.
[turn 3] read_file {"path": "shopcart/cart.py", ...} ->     1| """Cart totals with a bulk discount."""
[turn 4] write_test {"filename": "test_bulk_discount_threshold.py", ...} -> wrote test_bulk_discount_threshold.py
[turn 5] run_tests {"filename": "test_bulk_discount_threshold.py"} -> [harness: exit code 1, classified as failed]
[turn 6] finish {"verdict": "REPRODUCED", ...} -> finished
verdict: REPRODUCED  report: runs\20261027-123635-demo-shopcart-bundled
```

### Local web page

```bash
failfirst ui        # then open http://127.0.0.1:8765  (or /#demo to start the demo right away)
```

It has a **Run offline demo** button and a form for real runs: repo URL, commit, bug report and provider. It shows the verdict, the test, the last test output and the step log. It listens on 127.0.0.1 only and rejects requests from other sites.

## Run on a real bug

1. Start Docker.
2. Choose a model and copy `.env.example` to `.env` (gitignored):
   - **Free, local:** install Ollama, run `ollama pull qwen2.5-coder:7b`, then set `OPENAI_COMPAT_MODEL=qwen2.5-coder:7b`. The base URL defaults to `http://localhost:11434/v1`.
   - **Anthropic (paid):** set `ANTHROPIC_API_KEY`. The model defaults to `claude-opus-5-5`; change it with `ANTHROPIC_MODEL`. Requests use adaptive thinking, `ANTHROPIC_EFFORT=high` and the server-side refusal fallback (`ANTHROPIC_FALLBACKS=0` turns that off).
3. Save the issue text to a file and run:

```bash
failfirst run --repo https://github.com/OWNER/REPO --commit <buggy-sha> \
  --issue-file issue.md --provider openai-compatible
```

Options: `--max-steps` (default 25), `--max-tokens` (default 400000 input+output), `--command-timeout` (default 60 s) and `--max-cost-usd`. The cost cap only works after you set `FAILFIRST_PRICE_INPUT_PER_MTOK` and `FAILFIRST_PRICE_OUTPUT_PER_MTOK` to your model's current prices. When any budget is reached, the run stops with NOT_REPRODUCED and gives the reason.

## Safety

- Real repositories only run inside Docker. Without Docker, `failfirst run` refuses to start.
- The agent phase has **no network** (`--network none`), 1 CPU, 1 GB memory and 256 processes. The root filesystem and the repo are read-only, all capabilities are dropped, and every command is killed after its timeout.
- The agent can write only test files named `test_*.py`, and only into the separate repro folder. It never touches the host.
- Setup (clone and `pip install`) runs the repo's build code with network access, inside a throwaway container.
- The bug report, repo files and test output are treated as untrusted data, never as instructions. A REPRODUCED claim is checked by the harness, not just trusted.
- Keys come only from environment variables or `.env`. They are never logged, and child processes in local demo mode get no variables whose names look like secrets.
- FailFirst never pushes, opens PRs or comments anywhere.

## Evaluation (built, not run)

> Evaluation tooling is included but results have not been measured yet.

[scripts/eval.py](scripts/eval.py) runs the agent on the cases in [eval/cases.json](eval/cases.json). A case counts as **truly REPRODUCED** only if the generated test **fails on the buggy commit** and **passes on the fix commit**. Any other REPRODUCED verdict is recorded as a **false claim**. Results go to `eval/results/` (gitignored).

**To add a case:** pick a closed bug issue in a small, pure-Python repo with a clear open-source licence, where a merged fix PR adds a test. Set `buggy_commit` to the parent of the fix and `fix_commit` to the fix. Paste the issue text (as written before the fix), fill in `licence`, and set `"enabled": true`. Then check the file with `python scripts/eval.py --check`.

```bash
python scripts/eval.py --check                         # validate only
python scripts/eval.py --provider openai-compatible    # free with Ollama
python scripts/eval.py --provider anthropic            # costs money for every case
```

**Paid APIs cost money.** Run one case first (`--only <id>`) and use `--max-cost-usd`.

## Limitations

- **Not verified with Docker yet.** It was built on a machine without Docker, so only the local demo path has been run end to end. The Docker sandbox code (image build, two-phase setup, `--network none` exec) has not been run.
- **Not tested against a real model.** The Anthropic and OpenAI-compatible providers were only tested against a local stub that imitates each API's request and response format.
- Setup is basic: `pip install -e .` (with common test extras) plus `requirements*.txt`. Projects that need system libraries, services or other build tools will end in NEEDS_INFO.
- The test runs from a separate folder, so the repo's `conftest.py` fixtures and pytest settings are not applied.
- The harness can rule out import, syntax, collection and fixture failures. Whether the failure matches the report's reason is still the model's judgement. A bug that really is an ImportError would be rejected.
- Only Python projects with pytest. Small local models may not follow the tool-calling protocol well.

## Project layout

```text
failfirst/   cli.py, agent.py (loop, tools, guardrails, pipelines), providers.py, sandbox.py, report.py, ui.py
demo/        shopcart/ (seeded bug), issue.md, script.json (scripted provider)
eval/        cases.json (template)
scripts/     eval.py
```

## License

MIT
