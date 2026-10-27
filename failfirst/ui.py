"""A one-page local UI, served on 127.0.0.1 only, with the standard library."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import Budget, run_case, run_demo
from .providers import ProviderError, make_provider
from .sandbox import DockerSandbox

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FailFirst</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#2a2f3a;--text:#e6e8ec;--dim:#9aa3b2;--acc:#7aa2ff;--ok:#3fcf8e;--bad:#ff6b6b;--warn:#f5c451}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{margin:0;font-size:24px}p.sub{color:var(--dim);margin:4px 0 20px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
label{display:block;color:var(--dim);font-size:13px;margin:10px 0 4px}
input,select,textarea{width:100%;background:#0c0e12;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px;font:inherit}
textarea{min-height:160px;font-family:ui-monospace,monospace;font-size:13px}
button{margin-top:12px;margin-right:8px;background:var(--acc);color:#0b0d12;border:0;border-radius:6px;padding:9px 14px;font-weight:600;cursor:pointer}
button.alt{background:transparent;color:var(--acc);border:1px solid var(--acc)}button:disabled{opacity:.5;cursor:wait}
.badge{display:inline-block;padding:3px 10px;border-radius:99px;font-weight:700;font-size:13px}
.REPRODUCED{background:#123d2b;color:var(--ok)}.NOT_REPRODUCED{background:#43181b;color:var(--bad)}.NEEDS_INFO{background:#45391a;color:var(--warn)}
pre{background:#0c0e12;border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;font-size:12.5px;max-height:340px}
.dim{color:var(--dim);font-size:13px}.status span{margin-right:14px}ol{padding-left:20px}li{margin:4px 0}
</style></head><body><main>
<h1>FailFirst</h1><p class="sub">Turn a GitHub bug report into a failing pytest test. It never fixes the bug, never pushes, never comments.</p>
<div class="status dim card" id="status">Checking setup...</div><br>
<div class="grid"><div class="card">
<button id="demo">Run offline demo</button><span class="dim">scripted model, no key, no network</span>
<hr style="border-color:var(--line);margin:16px 0">
<label>Public GitHub repo URL</label><input id="repo" placeholder="https://github.com/owner/name">
<label>Buggy commit (SHA)</label><input id="commit" placeholder="e.g. 3f2a9c1">
<label>Bug report text</label><textarea id="issue" placeholder="Paste the issue text"></textarea>
<label>Model provider</label><select id="provider"><option value="openai-compatible">OpenAI-compatible (free local Ollama by default)</option><option value="anthropic">Anthropic (paid API)</option></select>
<label>Max model turns</label><input id="steps" type="number" value="25" min="1" max="100">
<button id="run" class="alt">Run on real repo</button><div class="dim" id="hint"></div>
</div><div class="card" id="out"><span class="dim">Results appear here.</span></div></div>
</main><script>
const $=id=>document.getElementById(id), esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
fetch("/api/status").then(r=>r.json()).then(s=>{$("status").innerHTML=
`<span>Docker: <b>${s.docker?"available":"not found (demo uses local fallback)"}</b></span><span>Anthropic key: <b>${s.anthropic?"set":"not set"}</b></span><span>Local model: <b>${s.openai_model||"not set"}</b></span>`});
async function go(url,body,btn){document.querySelectorAll("button").forEach(b=>b.disabled=true);
 $("out").innerHTML='<span class="dim">Running... this can take a few minutes for a real repo.</span>';
 try{const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json(); if(!r.ok) throw new Error(d.error||r.status); show(d);}catch(e){$("out").innerHTML=`<b style="color:var(--bad)">Error:</b> ${esc(e.message)}`}
 document.querySelectorAll("button").forEach(b=>b.disabled=false)}
function show(d){const u=d.usage, steps=d.steps.map(s=>s.type=="model"?`<li class="dim">${esc(s.text)}</li>`:
 `<li><b>${esc(s.tool)}</b> <code>${esc(JSON.stringify(s.args).slice(0,120))}</code>${s.is_error?' <span style="color:var(--bad)">error</span>':""}</li>`).join("");
 $("out").innerHTML=`<span class="badge ${d.verdict}">${d.verdict}</span>
 <p>${esc(d.explanation)}</p><p class="dim">Provider: ${esc(d.provider)} (${esc(d.model)}) · sandbox: ${esc(d.sandbox)} · ${d.model_turns} turns · ${u.input_tokens}/${u.output_tokens} tokens · ${d.duration_seconds}s<br>Harness check: ${esc(d.harness_check)}<br>Report: ${esc(d.run_dir)}</p>
 ${d.test_content?`<b>${esc(d.test_file)}</b><pre>${esc(d.test_content)}</pre>`:""}
 ${d.last_test_run?`<b>Last test run (${esc(d.last_test_run.kind)})</b><pre>${esc(d.last_test_run.output)}</pre>`:""}
 <b>Step log</b><ol>${steps}</ol>`}
$("demo").onclick=()=>go("/api/demo",{});
if(location.hash=="#demo")$("demo").click();
$("run").onclick=()=>go("/api/run",{repo:$("repo").value.trim(),commit:$("commit").value.trim(),issue:$("issue").value,provider:$("provider").value,max_steps:+$("steps").value});
</script></body></html>"""


def serve(port: int = 8765, out_dir: str = "runs") -> None:
    lock = threading.Lock()  # one run at a time
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _trusted(self):
            # Blocks DNS rebinding and cross-site form posts: only this page may start runs.
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.split("//", 1)[-1] in allowed_hosts

        def do_GET(self):
            if not self._trusted():
                return self._send(403, {"error": "forbidden"})
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._send(200, {"docker": DockerSandbox.available(),
                                        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                        "openai_model": os.environ.get("OPENAI_COMPAT_MODEL", "")})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._trusted() or self.headers.get("Content-Type") != "application/json":
                return self._send(403, {"error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 200_000)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            if not lock.acquire(blocking=False):
                return self._send(409, {"error": "a run is already in progress"})
            try:
                if self.path == "/api/demo":
                    result = run_demo(out_dir)
                elif self.path == "/api/run":
                    provider = make_provider(body.get("provider", ""))
                    budget = Budget.from_env(max_steps=int(body.get("max_steps") or 25))
                    result = run_case(provider, body.get("repo", ""), body.get("commit", ""),
                                      body.get("issue", ""), budget, out_dir)
                else:
                    return self._send(404, {"error": "not found"})
                run_json = json.loads((Path(result["run_dir"]) / "run.json").read_text("utf-8"))
                self._send(200, run_json | {"run_dir": result["run_dir"]})
            except (ProviderError, ValueError, RuntimeError) as exc:
                self._send(400, {"error": str(exc)})
            finally:
                lock.release()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"FailFirst UI: http://127.0.0.1:{port}  (local only, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
