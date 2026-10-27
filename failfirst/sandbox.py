"""Where the agent's tools run: a Docker sandbox, or a local fallback for the bundled demo only."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

DEMO_PROJECT = Path(__file__).resolve().parent.parent / "demo" / "shopcart"
IMAGE = "failfirst-sandbox:1"

# Small image: Python, git and pytest. /work and /repro belong to an unprivileged user,
# so new Docker volumes mounted there inherit that owner.
DOCKERFILE = """FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \\
 && rm -rf /var/lib/apt/lists/* \\
 && pip install --no-cache-dir pytest==8.3.3 \\
 && useradd -m -u 1000 runner && mkdir /work /repro && chown runner /work /repro
USER runner
WORKDIR /work
"""

# Runs with network ON, inside a throwaway container. Inputs arrive as env vars, never
# pasted into the script, so a crafted URL or commit cannot inject shell code.
SETUP_SCRIPT = r"""set -e
git clone --quiet --filter=blob:none "$REPO_URL" /work/repo
cd /work/repo
git -c advice.detachedHead=false checkout --quiet "$COMMIT"
python -m venv --system-site-packages /work/venv
PIP="/work/venv/bin/pip install --quiet --no-cache-dir --disable-pip-version-check"
if [ -f pyproject.toml ] || [ -f setup.py ]; then $PIP -e ".[test,tests,testing,dev]" || $PIP -e . ; fi
for f in requirements.txt requirements-dev.txt requirements-test.txt requirements_dev.txt; do
  if [ -f "$f" ]; then $PIP -r "$f"; fi
done
echo setup-ok
"""

# The file tools. Executed with `python -I` inside the sandbox; arguments come as JSON on stdin.
HELPER = r'''
import json, os, re, sys
a = json.loads(sys.stdin.read())
root = os.path.realpath(a["root"])
SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox", ".nox", ".mypy_cache",
        ".pytest_cache", ".eggs", "build", "dist"}
def safe(rel):
    full = os.path.realpath(os.path.join(root, rel or "."))
    if full != root and not full.startswith(root + os.sep):
        print("error: path is outside the allowed folder"); sys.exit(3)
    return full
def walk(base):
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in SKIP and not x.endswith(".egg-info"))
        for f in sorted(files):
            yield os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
op = a["op"]
if op == "list":
    base = safe(a.get("path"))
    if not os.path.isdir(base): print("error: not a directory"); sys.exit(3)
    files = list(walk(base))
    print("\n".join(files[:300]) or "(no files)")
    if len(files) > 300: print(f"... and {len(files) - 300} more files (pass a sub-folder as path)")
elif op == "read":
    p = safe(a["path"])
    if not os.path.isfile(p): print("error: file not found"); sys.exit(3)
    with open(p, encoding="utf-8", errors="replace") as fh: lines = fh.read().splitlines()
    s = max(1, int(a.get("start") or 1)); e = int(a.get("end") or s + 199)
    e = min(e, s + 399, len(lines))
    for i in range(s - 1, e): print(f"{i + 1:>5}| {lines[i]}")
    print(f"[showing lines {s}-{e} of {len(lines)}]")
elif op == "search":
    try: rx = re.compile(a["pattern"])
    except re.error as exc: print(f"error: bad regex: {exc}"); sys.exit(3)
    hits = 0
    for rel in walk(root):
        p = os.path.join(root, rel)
        try:
            if os.path.getsize(p) > 1_000_000: continue
            with open(p, encoding="utf-8") as fh: text = fh.read()
        except (OSError, UnicodeDecodeError): continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                print(f"{rel}:{n}: {line.strip()[:200]}"); hits += 1
                if hits >= 100: print("... stopped after 100 matches"); sys.exit(0)
    if not hits: print("no matches")
elif op == "write":
    p = safe(a["path"])
    with open(p, "w", encoding="utf-8", newline="\n") as fh: fh.write(a["content"])
    print(f"wrote {a['path']} ({len(a['content'])} characters)")
'''

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


class SandboxError(Exception):
    """Setup failed (clone, checkout or install). The run ends with NEEDS_INFO."""


@dataclass
class ExecResult:
    code: int
    output: str
    timed_out: bool = False


class Sandbox:
    mode = "base"
    repo_root = ""
    repro_root = ""
    helper_python = "python"
    test_python = "python"

    def exec(self, argv: list[str], stdin: str | None = None, timeout: int = 60) -> ExecResult:
        raise NotImplementedError

    def tool(self, op: str, root: str, timeout: int = 60, **args) -> ExecResult:
        payload = json.dumps({"op": op, "root": root, **args})
        return self.exec([self.helper_python, "-I", "-c", HELPER], stdin=payload, timeout=timeout)

    def pytest(self, filename: str, timeout: int = 120) -> ExecResult:
        path = f"{self.repro_root}/{filename}"
        argv = [self.test_python, "-m", "pytest", path, "-q", "-p", "no:cacheprovider",
                "--no-header", "--color=no", "-rfE"]
        return self.exec(argv, timeout=timeout)

    def close(self) -> None:
        pass


class LocalSandbox(Sandbox):
    """NO ISOLATION. Only for the bundled demo project, never for repositories from the internet."""

    mode = "local (no isolation, demo only)"

    def __init__(self, project_dir: Path = DEMO_PROJECT):
        if Path(project_dir).resolve() != DEMO_PROJECT:
            raise ValueError("Local mode only runs the bundled demo project. Use Docker for real repositories.")
        self.tmp = Path(tempfile.mkdtemp(prefix="failfirst-"))
        shutil.copytree(project_dir, self.tmp / "repo")
        (self.tmp / "repro").mkdir()
        self.repo_root = str(self.tmp / "repo")
        self.repro_root = str(self.tmp / "repro")
        self.helper_python = self.test_python = sys.executable
        # Child processes get no secrets from this environment.
        self.env = {k: v for k, v in os.environ.items() if not SECRET_NAME.search(k)}
        self.env.update(PYTHONPATH=self.repo_root, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

    def exec(self, argv, stdin=None, timeout=60):
        try:
            p = subprocess.run(argv, input=stdin or "", cwd=self.repo_root, env=self.env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=timeout)
            return ExecResult(p.returncode, p.stdout + p.stderr)
        except subprocess.TimeoutExpired:
            return ExecResult(124, f"command timed out after {timeout}s", timed_out=True)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class DockerSandbox(Sandbox):
    """Setup with network in a throwaway container, then an agent container with --network none."""

    mode = "docker"
    repo_root = "/work/repo"
    repro_root = "/repro"
    helper_python = "python"
    test_python = "/work/venv/bin/python"

    def __init__(self, cpus: str = "1", memory: str = "1g", setup_timeout: int = 900):
        self.id = "failfirst-" + uuid.uuid4().hex[:10]
        self.work_vol, self.repro_vol = self.id + "-work", self.id + "-repro"
        self.cpus, self.memory, self.setup_timeout = cpus, memory, setup_timeout
        self.started = False

    @staticmethod
    def available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _docker(self, *args: str, stdin: str | None = None, timeout: int = 120) -> ExecResult:
        try:
            # Always give stdin (empty if unused) so `docker exec -i` never waits on the parent's stdin.
            p = subprocess.run(["docker", *args], input=stdin or "", capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
            return ExecResult(p.returncode, p.stdout + p.stderr)
        except subprocess.TimeoutExpired:
            return ExecResult(124, f"docker command timed out after {timeout}s", timed_out=True)

    def _prepare(self) -> None:
        if self._docker("image", "inspect", IMAGE).code != 0:
            r = self._docker("build", "-t", IMAGE, "-", stdin=DOCKERFILE, timeout=900)
            if r.code != 0:
                raise SandboxError("could not build the sandbox image:\n" + r.output[-2000:])
        for vol in (self.work_vol, self.repro_vol):
            if self._docker("volume", "create", vol).code != 0:
                raise SandboxError("could not create a Docker volume")

    def setup_clone(self, repo_url: str, commit: str) -> None:
        self._prepare()
        r = self._docker("run", "--rm", "--name", self.id + "-setup", "--cpus", "2", "--memory", "2g",
                         "-v", f"{self.work_vol}:/work", "-e", f"REPO_URL={repo_url}", "-e", f"COMMIT={commit}",
                         IMAGE, "sh", "-c", SETUP_SCRIPT, timeout=self.setup_timeout)
        if r.code != 0 or "setup-ok" not in r.output:
            raise SandboxError("setup failed (clone, checkout or dependency install):\n" + r.output[-2000:])

    def setup_copy(self, project_dir: Path) -> None:
        """Copy a local folder in (used by the demo), so setup needs no network once the image exists."""
        self._prepare()
        seed = self.id + "-seed"
        steps = [("create", "--name", seed, "-v", f"{self.work_vol}:/work", IMAGE),
                 ("cp", f"{Path(project_dir).resolve()}{os.sep}.", f"{seed}:/work/repo"),
                 ("rm", seed)]
        for args in steps:
            r = self._docker(*args)
            if r.code != 0:
                self._docker("rm", "-f", seed)
                raise SandboxError("could not copy the project into the sandbox:\n" + r.output[-1000:])
        r = self._docker("run", "--rm", "--network", "none", "-v", f"{self.work_vol}:/work", IMAGE,
                         "python", "-m", "venv", "--system-site-packages", "/work/venv")
        if r.code != 0:
            raise SandboxError("could not create the venv:\n" + r.output[-1000:])

    def start(self) -> None:
        """The agent phase: no network, read-only repo, writable repro folder only, resource limits."""
        r = self._docker("run", "-d", "--name", self.id, "--network", "none", "--cpus", self.cpus,
                         "--memory", self.memory, "--pids-limit", "256", "--read-only",
                         "--tmpfs", "/tmp:size=64m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                         "-v", f"{self.work_vol}:/work:ro", "-v", f"{self.repro_vol}:/repro",
                         "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONPATH=/work/repo:/work/repo/src",
                         IMAGE, "sleep", "infinity")
        if r.code != 0:
            raise SandboxError("could not start the sandbox container:\n" + r.output[-1000:])
        self.started = True

    def exec(self, argv, stdin=None, timeout=60):
        r = self._docker("exec", "-i", "-w", self.repo_root, self.id, "timeout", "-k", "5", str(timeout), *argv,
                         stdin=stdin, timeout=timeout + 30)
        if r.code == 124:
            return ExecResult(124, f"command timed out after {timeout}s", timed_out=True)
        return r

    def close(self):
        self._docker("rm", "-f", self.id)
        self._docker("volume", "rm", "-f", self.work_vol, self.repro_vol)
