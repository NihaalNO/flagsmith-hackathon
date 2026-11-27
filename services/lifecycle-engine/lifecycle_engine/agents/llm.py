"""Injectable LLM providers.

Any object with `name`, `model` and `complete_json(system, prompt, schema, context)`
can be passed to the planner. Providers must raise `LLMUnavailableError` on any
failure so the planner can fall back to deterministic output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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


def provider_from_env() -> LLMProvider | None:
    """`FLAGPILOT_LLM_PROVIDER` = 'claude-code' | 'anthropic' | 'mock' | unset (no LLM)."""
    kind = os.environ.get("FLAGPILOT_LLM_PROVIDER", "").lower()
    if kind == "claude-code":
        return ClaudeCodeProvider(model=os.environ.get("FLAGPILOT_LLM_MODEL") or None,
                                  max_budget_usd=float(os.environ.get("FLAGPILOT_LLM_MAX_BUDGET_USD", "1.0")))
    if kind == "anthropic":
        return AnthropicProvider(model=os.environ.get("FLAGPILOT_LLM_MODEL", "claude-opus-5-5"))
    if kind == "mock":
        return MockLLMProvider()
    return None
