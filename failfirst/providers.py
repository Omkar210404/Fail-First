"""Model providers behind one small interface: complete(system, messages, tools, max_tokens) -> Reply.

Messages use a neutral format:
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall], "raw": provider-specific content}
  {"role": "tool", "results": [{"id": str, "content": str, "is_error": bool}]}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    bad_json: str | None = None  # set when the model sent arguments that are not valid JSON


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    raw: object = None


class ProviderError(Exception):
    pass


class AnthropicProvider:
    """Claude through the official SDK. Key: ANTHROPIC_API_KEY. Model: ANTHROPIC_MODEL."""

    name = "anthropic"

    def __init__(self):
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("pip install anthropic") from exc
        self.anthropic = anthropic
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
        self.effort = os.environ.get("ANTHROPIC_EFFORT", "high")
        # Server-side refusal fallback: if the model declines, the API retries on a fallback model.
        self.fallbacks = os.environ.get("ANTHROPIC_FALLBACKS", "1") != "0"
        self.client = anthropic.Anthropic()

    def complete(self, system, messages, tools, max_tokens):
        msgs = []
        for m in messages:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msgs.append({"role": "assistant", "content": m["raw"]})  # unchanged, incl. thinking blocks
            else:
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r["is_error"]}
                    for r in m["results"]]})
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system, messages=msgs,
                      tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                             for t in tools],
                      output_config={"effort": self.effort}, cache_control={"type": "ephemeral"})
        a = self.anthropic
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key (check ANTHROPIC_API_KEY)") from exc
        except a.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit reached") from exc
        except a.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise ProviderError("could not reach the Anthropic API") from exc
        u = resp.usage
        tokens_in = u.input_tokens + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        return Reply(
            text="\n".join(b.text for b in resp.content if b.type == "text"),
            tool_calls=[ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"],
            input_tokens=tokens_in, output_tokens=u.output_tokens, stop_reason=resp.stop_reason or "",
            raw=resp.content)


class OpenAICompatibleProvider:
    """Any OpenAI-compatible chat endpoint. Defaults to a local Ollama server, which is free."""

    name = "openai-compatible"

    def __init__(self):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("pip install openai") from exc
        self.base_url = os.environ.get("OPENAI_COMPAT_BASE_URL", "http://localhost:11434/v1")
        self.model = os.environ.get("OPENAI_COMPAT_MODEL", "")
        if not self.model:
            raise ProviderError("set OPENAI_COMPAT_MODEL (for Ollama, e.g. qwen2.5-coder:7b)")
        # Ollama ignores the key; hosted endpoints need a real one.
        key = os.environ.get("OPENAI_COMPAT_API_KEY", "not-needed-for-ollama")
        self.client = OpenAI(base_url=self.base_url, api_key=key)

    def complete(self, system, messages, tools, max_tokens):
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m["text"] or None}
                if m["tool_calls"]:
                    msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                        "name": c.name, "arguments": c.bad_json or json.dumps(c.args)}} for c in m["tool_calls"]]
                msgs.append(msg)
            else:
                msgs.extend({"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"])
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=msgs, max_tokens=max_tokens,
                tools=[{"type": "function", "function": t} for t in tools])
        except Exception as exc:  # the openai package has many error types; report them all the same way
            hint = " (is the server running? For Ollama: start the Ollama app or run `ollama serve`)" \
                if type(exc).__name__ == "APIConnectionError" else ""
            raise ProviderError(f"{self.base_url}: {type(exc).__name__}: {str(exc)[:300]}{hint}") from exc
        choice = resp.choices[0]
        calls = []
        for i, tc in enumerate(choice.message.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
                calls.append(ToolCall(tc.id or f"call-{i}", tc.function.name, args if isinstance(args, dict) else {}))
            except json.JSONDecodeError:
                calls.append(ToolCall(tc.id or f"call-{i}", tc.function.name, {}, bad_json=tc.function.arguments))
        usage = resp.usage
        return Reply(text=choice.message.content or "", tool_calls=calls,
                     input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                     output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                     stop_reason=choice.finish_reason or "")


class ScriptedProvider:
    """NOT an LLM. Replays a fixed list of turns from a JSON file, for the offline demo."""

    name = "scripted"
    model = "replay of a fixed script, not a real LLM"

    def __init__(self, script_path: Path):
        self.turns = json.loads(Path(script_path).read_text(encoding="utf-8"))["turns"]
        self.i = 0

    def complete(self, system, messages, tools, max_tokens):
        if self.i >= len(self.turns):
            return Reply(text="(end of script)", stop_reason="end_turn")
        turn = self.turns[self.i]
        self.i += 1
        calls = [ToolCall(f"scripted-{self.i}-{k}", c["name"], c["args"]) for k, c in enumerate(turn["tool_calls"])]
        return Reply(text=turn.get("text", ""), tool_calls=calls, stop_reason="tool_use")


def make_provider(name: str):
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai-compatible":
        return OpenAICompatibleProvider()
    raise ProviderError(f"unknown provider {name!r}")
