"""Injectable LLM providers.

Any object with `name`, `model` and `complete_json(system, prompt, schema, context)`
can be passed to the planner. Providers must raise `LLMUnavailableError` on any
failure so the planner can fall back to deterministic output.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


class LLMUnavailableError(Exception):
    pass


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str | None

    def complete_json(self, *, system: str, prompt: str, schema: dict, context: dict) -> dict:
        """Return a JSON object conforming to `schema`. `context` is the structured
        evidence packet already serialized into `prompt`."""


class MockLLMProvider:
    """Deterministic provider for tests and development (no network).

    By default it writes a narrative that cites only evidence ids present in the
    packet. Pass `response` (dict or callable(context) -> dict) to inject arbitrary
    output, e.g. hallucinated paths, or `fail=True` to simulate an outage.
    """

    name = "mock"
    model = "mock-1"

    def __init__(self, response: dict | Callable[[dict], dict] | None = None, fail: bool = False):
        self.response = response
        self.fail = fail
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, prompt: str, schema: dict, context: dict) -> dict:
        self.calls.append({"system": system, "prompt": prompt, "context": context})
        if self.fail:
            raise LLMUnavailableError("mock provider configured to fail")
        if self.response is not None:
            return self.response(context) if callable(self.response) else self.response
        return self._default(context)

    @staticmethod
    def _default(ctx: dict) -> dict:
        flag = ctx["flag_name"]
        ev = {e["id"]: e for e in ctx["evidence"]}
        refs = [i for i, e in ev.items() if e["kind"] == "code_reference"]
        supports = [i for i, e in ev.items() if e["kind"].startswith("stale:") and e.get("direction") == "supports"]
        blockers = [i for i, e in ev.items() if e["kind"] == "blocker"]
        edits = [i for i, e in ev.items() if e["kind"] == "patch_edit"]
        statements = []
        if supports:
            statements.append({"type": "fact", "text": f"The stale assessment found {len(supports)} supporting signal(s) for '{flag}'.",
                               "evidence_ids": supports[:5]})
        if refs:
            statements.append({"type": "fact", "text": f"'{flag}' has {len(refs)} code reference(s) that cleanup must address.",
                               "evidence_ids": refs[:5]})
        if blockers:
            statements.append({"type": "hypothesis", "text": "Unresolved blockers mean the flag may still be evaluated in ways the scan cannot see.",
                               "evidence_ids": blockers})
        statements.append({"type": "recommendation", "text": ctx["deterministic_recommendation"], "evidence_ids": []})
        return {
            "reasoning_summary": f"Mock narrative for '{flag}': stale status is {ctx['stale_status']}; "
                                 f"recommended action is {ctx['recommended_action']}.",
            "statements": statements,
            "risks": [{"level": "medium", "description": "Blocker requires human review before cleanup.", "evidence_ids": [b]}
                      for b in blockers],
            "patch_rationale": ("The patch keeps the code path that production currently executes and removes the flag check."
                                if edits else None),
        }


class AnthropicProvider:
    """Claude provider using the official `anthropic` SDK (optional dependency).

    Uses structured outputs (`output_config.format` with a JSON schema) and the
    server-side refusal fallback. Credentials are resolved by the SDK
    (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile).
    """

    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", max_tokens: int = 16000,
                 client: Any | None = None, fallbacks: str | None = "default"):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks
        self._client = client

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise LLMUnavailableError("the 'anthropic' package is not installed (pip install anthropic)") from exc
            self._client = anthropic.Anthropic()
        return self._client

    def complete_json(self, *, system: str, prompt: str, schema: dict, context: dict) -> dict:
        try:
            client = self._get_client()
            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            )
            if self.fallbacks:
                response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks=self.fallbacks, **kwargs)
            else:
                response = client.messages.create(**kwargs)
        except LLMUnavailableError:
            raise
        except Exception as exc:  # network, auth, rate limit, validation...
            raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMUnavailableError("model declined the request (refusal)")
        if response.stop_reason == "max_tokens":
            raise LLMUnavailableError("response truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMUnavailableError("response contained no text block")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError(f"response was not valid JSON: {exc}") from exc


CommandRunner = Callable[[list[str], str, float, str], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str], stdin: str, timeout: float, cwd: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=cwd)


def resolve_claude_executable(executable: str = "claude") -> str | None:
    """Locate the Claude Code CLI. On Windows the npm `claude.cmd` shim is bypassed in
    favour of the native `claude.exe` it forwards to, because cmd.exe re-parses
    arguments and would mangle the JSON schema and system prompt."""
    found = shutil.which(executable)
    if found is None:
        return None
    p = Path(found)
    if p.suffix.lower() in (".cmd", ".bat"):
        native = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.is_file():
            return str(native)
        return None  # refuse to pass structured arguments through cmd.exe
    return found


class ClaudeCodeProvider:
    """Runs the Claude Code CLI as a sub-process in headless mode (`claude -p`).

    Uses the signed-in Claude Code account, so no API key is needed. The call is made
    with no tools (`--tools ""`), no MCP servers, no session persistence, the engine's
    own system prompt instead of Claude Code's default, and an empty temporary working
    directory, so the sub-process can only read the evidence packet on stdin and
    return JSON matching `--json-schema`. `--max-budget-usd` caps the spend per call.
    """

    name = "claude-code"

    def __init__(self, model: str | None = None, effort: str | None = None, *, executable: str = "claude",
                 timeout_s: float = 300, max_budget_usd: float = 1.0, runner: CommandRunner | None = None):
        self.model = model
        self.effort = effort
        self.executable = executable
        self.timeout_s = timeout_s
        self.max_budget_usd = max_budget_usd
        self.runner = runner or _run
        self.last_cost_usd: float | None = None
        self.last_session_id: str | None = None

    def build_argv(self, exe: str, system: str, schema: dict) -> list[str]:
        argv = [exe, "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
                "--no-session-persistence", "--system-prompt", system,
                "--json-schema", json.dumps(schema, separators=(",", ":")),
                "--max-budget-usd", f"{self.max_budget_usd:g}"]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        return argv

    def complete_json(self, *, system: str, prompt: str, schema: dict, context: dict) -> dict:
        exe = resolve_claude_executable(self.executable)
        if exe is None:
            raise LLMUnavailableError(f"Claude Code CLI '{self.executable}' not found on PATH")
        argv = self.build_argv(exe, system, schema)
        workdir = tempfile.mkdtemp(prefix="flagpilot-claude-")
        try:
            proc = self.runner(argv, prompt, self.timeout_s, workdir)
        except subprocess.TimeoutExpired as exc:
            raise LLMUnavailableError(f"claude CLI timed out after {self.timeout_s}s") from exc
        except OSError as exc:
            raise LLMUnavailableError(f"could not start claude CLI: {exc}") from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        try:
            out = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise LLMUnavailableError(f"claude CLI exited {proc.returncode} without JSON output: {detail}") from exc
        self.last_cost_usd = out.get("total_cost_usd")
        self.last_session_id = out.get("session_id")
        if out.get("is_error") or proc.returncode != 0:
            raise LLMUnavailableError(f"claude CLI error ({out.get('subtype')}): {str(out.get('result'))[:300]}")
        structured = out.get("structured_output")
        if isinstance(structured, dict):
            return structured
        try:
            parsed = json.loads(out.get("result") or "")
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError("claude CLI returned no structured output") from exc
        if not isinstance(parsed, dict):
            raise LLMUnavailableError("claude CLI result is not a JSON object")
        return parsed


HttpPost = Callable[[str, dict, dict, float], tuple[int, str]]
JSON_MODES = ("json_schema", "json_object", "none")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _urllib_post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


class OpenAICompatibleProvider:
    """Any server implementing OpenAI's Chat Completions API: OpenAI, OpenRouter, Azure
    OpenAI-style gateways, Ollama, vLLM, LM Studio, LiteLLM...

    POSTs to `{base_url}/chat/completions`. `json_mode` controls how JSON is requested:
    'json_schema' (structured outputs; sent with strict=false because the plan schema has
    optional fields), 'json_object' (JSON mode, schema given in the prompt), or 'none'
    (schema in the prompt only, for servers that support neither). Uses only the standard
    library; the API key is optional (local servers usually need none) and never appears in
    error messages.
    """

    name = "openai-compatible"

    def __init__(self, model: str, base_url: str, api_key: str | None = None, *, json_mode: str = "json_schema",
                 timeout_s: float = 120, max_tokens: int | None = None, post: HttpPost | None = None):
        if not model:
            raise ValueError("OpenAI-compatible provider needs a model name (FLAGPILOT_LLM_MODEL)")
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("OpenAI-compatible provider needs an http(s) base URL (FLAGPILOT_LLM_BASE_URL)")
        if json_mode not in JSON_MODES:
            raise ValueError(f"FLAGPILOT_LLM_JSON_MODE must be one of {', '.join(JSON_MODES)}")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.json_mode = json_mode
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.post = post or _urllib_post

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def build_request(self, system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        user = prompt
        if self.json_mode != "json_schema":
            user = (f"{prompt}\n\nRespond with a single JSON object (no prose, no code fences) that matches "
                    f"this JSON Schema:\n{json.dumps(schema)}")
        body: dict[str, Any] = {"model": self.model,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.json_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "flagpilot_plan_narrative", "schema": schema, "strict": False}}
        elif self.json_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        return headers, body

    def complete_json(self, *, system: str, prompt: str, schema: dict, context: dict) -> dict:
        headers, body = self.build_request(system, prompt, schema)
        try:
            status, text = self.post(self.endpoint, headers, body, self.timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMUnavailableError(f"could not reach {self.endpoint}: {getattr(exc, 'reason', exc)}") from exc
        if status >= 400:
            raise LLMUnavailableError(f"{self.endpoint} returned HTTP {status}: {self._error_message(text)}")
        try:
            data = json.loads(text)
            choice = data["choices"][0]
            message = choice["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError("response is not an OpenAI-style chat completion") from exc
        if message.get("refusal"):
            raise LLMUnavailableError(f"model refused: {str(message['refusal'])[:200]}")
        if choice.get("finish_reason") == "length":
            raise LLMUnavailableError("response truncated (finish_reason=length); raise max_tokens")
        content = message.get("content")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content:
            raise LLMUnavailableError("response has no message content")
        fenced = _FENCE_RE.match(content)
        try:
            parsed = json.loads(fenced.group(1) if fenced else content)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError("message content is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMUnavailableError("message content is not a JSON object")
        return parsed

    @staticmethod
    def _error_message(text: str) -> str:
        try:
            err = json.loads(text).get("error")
            msg = err.get("message") if isinstance(err, dict) else err
        except (json.JSONDecodeError, AttributeError):
            msg = text
        return str(msg)[:300]


def provider_from_env() -> LLMProvider | None:
    """`FLAGPILOT_LLM_PROVIDER` = 'openai-compatible' | 'claude-code' | 'anthropic' | 'mock' | unset (no LLM).

    openai-compatible reads FLAGPILOT_LLM_MODEL, FLAGPILOT_LLM_BASE_URL, optional
    FLAGPILOT_LLM_API_KEY and FLAGPILOT_LLM_JSON_MODE (json_schema | json_object | none).
    """
    kind = os.environ.get("FLAGPILOT_LLM_PROVIDER", "").lower()
    if kind == "openai-compatible":
        return OpenAICompatibleProvider(
            model=os.environ.get("FLAGPILOT_LLM_MODEL", ""),
            base_url=os.environ.get("FLAGPILOT_LLM_BASE_URL", ""),
            api_key=os.environ.get("FLAGPILOT_LLM_API_KEY") or None,
            json_mode=os.environ.get("FLAGPILOT_LLM_JSON_MODE", "json_schema").lower(),
        )
    if kind == "claude-code":
        return ClaudeCodeProvider(model=os.environ.get("FLAGPILOT_LLM_MODEL") or None,
                                  max_budget_usd=float(os.environ.get("FLAGPILOT_LLM_MAX_BUDGET_USD", "1.0")))
    if kind == "anthropic":
        return AnthropicProvider(model=os.environ.get("FLAGPILOT_LLM_MODEL", "claude-opus-5-5"))
    if kind == "mock":
        return MockLLMProvider()
    return None
