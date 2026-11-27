"""ClaudeCodeProvider: Claude Code CLI as an LLM sub-process.

Unit tests use a fake runner (no process, no network). The live test calls the real
`claude` CLI and costs money, so it only runs with FLAGPILOT_LIVE_CLAUDE=1.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from lifecycle_engine.agents import llm as llm_mod
from lifecycle_engine.agents.llm import ClaudeCodeProvider, LLMUnavailableError, resolve_claude_executable
from lifecycle_engine.agents.planner import CleanupPlanner
from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.cleanup.patcher import PatchGenerator
from lifecycle_engine.policies.stale_detector import StaleFlagDetector
from lifecycle_engine.schemas.flags import ConsumerCoverage, DynamicReferenceReview
from lifecycle_engine.schemas.planning import LLMPlanNarrative, LLMStatus, StatementType

from tests.builders import NOW, sample_configs


def fake(stdout: dict | str, returncode: int = 0, stderr: str = "", calls: list | None = None):
    def run(argv, stdin, timeout, cwd):
        if calls is not None:
            calls.append({"argv": argv, "stdin": stdin, "timeout": timeout, "cwd": cwd, "cwd_files": os.listdir(cwd)})
        body = stdout if isinstance(stdout, str) else json.dumps(stdout)
        return subprocess.CompletedProcess(argv, returncode, body, stderr)
    return run


@pytest.fixture(autouse=True)
def _cli_on_path(monkeypatch):
    monkeypatch.setattr(llm_mod, "resolve_claude_executable", lambda exe="claude": "/fake/claude")


def ok(structured, cost=0.02):
    return {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(structured),
            "structured_output": structured, "total_cost_usd": cost, "session_id": "s-1"}


def call(provider, schema=None):
    return provider.complete_json(system="SYS", prompt="PACKET", schema=schema or {"type": "object"}, context={})


def test_invocation_is_tool_less_isolated_and_budget_capped():
    calls = []
    p = ClaudeCodeProvider(model="claude-opus-5-5", effort="low", max_budget_usd=0.5, runner=fake(ok({"a": 1}), calls=calls))
    assert call(p, {"type": "object", "properties": {"a": {"type": "integer"}}}) == {"a": 1}
    c = calls[0]
    argv = c["argv"]
    assert argv[:2] == ["/fake/claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert json.loads(argv[argv.index("--json-schema") + 1])["properties"]["a"]["type"] == "integer"
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5"
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5" and argv[argv.index("--effort") + 1] == "low"
    assert c["stdin"] == "PACKET"            # evidence goes via stdin, not the command line
    assert c["cwd_files"] == []              # empty working directory: no project files visible
    assert not Path(c["cwd"]).exists()       # and it is removed afterwards
    assert p.last_cost_usd == 0.02 and p.last_session_id == "s-1"


def test_falls_back_to_result_text_when_no_structured_output():
    out = ok({"a": 1})
    out.pop("structured_output")
    assert call(ClaudeCodeProvider(runner=fake(out))) == {"a": 1}


@pytest.mark.parametrize("stdout,code,needle", [
    ({"is_error": True, "subtype": "success", "result": "Not logged in · Please run /login"}, 1, "Not logged in"),
    ({"is_error": True, "subtype": "error_max_budget_usd", "result": ""}, 1, "error_max_budget_usd"),
    ("not json at all", 1, "without JSON output"),
    ({"is_error": False, "subtype": "success", "result": "plain text"}, 0, "no structured output"),
    ({"is_error": False, "subtype": "success", "result": "[1, 2]"}, 0, "not a JSON object"),
])
def test_failures_raise_llm_unavailable(stdout, code, needle):
    with pytest.raises(LLMUnavailableError, match=needle):
        call(ClaudeCodeProvider(runner=fake(stdout, code)))


def test_timeout_and_missing_cli(monkeypatch):
    def boom(*_):
        raise subprocess.TimeoutExpired("claude", 1)
    with pytest.raises(LLMUnavailableError, match="timed out"):
        call(ClaudeCodeProvider(runner=boom, timeout_s=1))
    monkeypatch.setattr(llm_mod, "resolve_claude_executable", lambda exe="claude": None)
    with pytest.raises(LLMUnavailableError, match="not found"):
        call(ClaudeCodeProvider(runner=fake(ok({}))))


def test_windows_cmd_shim_is_bypassed(tmp_path, monkeypatch):
    monkeypatch.undo()  # real resolver for this test
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\n")
    native = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"")
    monkeypatch.setattr(llm_mod.shutil, "which", lambda exe: str(shim))
    assert resolve_claude_executable() == str(native)
    native.unlink()
    assert resolve_claude_executable() is None  # never route structured args through cmd.exe


# --- planner integration -------------------------------------------------------------

@pytest.fixture(scope="module")
def ready_case():
    from tests.conftest import CHECKOUT, PRICING

    targets = [CHECKOUT, PRICING]
    cfg = sample_configs()["new_checkout"]
    analysis = CodeIntelligence.from_targets(targets).analyze("new_checkout", cfg)
    review = DynamicReferenceReview(reference_id=analysis.unresolved_dynamic_references[0].id, reviewed_by="lead",
                                    reviewed_at=NOW, excluded_flags=["new_checkout"])
    stale = StaleFlagDetector().evaluate("new_checkout", config=cfg, analysis=analysis, now=NOW,
                                         coverage=ConsumerCoverage(all_consumers_scanned=True, dynamic_reference_reviews=[review]))
    patch = PatchGenerator(targets).generate("new_checkout", stale.cleanup_direction)
    return analysis, stale, patch


def test_planner_uses_cli_output_and_still_validates_it(ready_case):
    analysis, stale, patch = ready_case
    ref = analysis.references[0]
    good = {"reasoning_summary": "Production runs the new checkout for everyone.",
            "statements": [{"type": "fact", "text": "The flag is referenced in code.", "evidence_ids": [ref.id]}]}
    calls = []
    plan = CleanupPlanner(ClaudeCodeProvider(runner=fake(ok(good), calls=calls))).plan(analysis=analysis, stale=stale, patch=patch)
    assert plan.llm.status == LLMStatus.USED and plan.llm.provider == "claude-code"
    assert plan.reasoning_summary == good["reasoning_summary"]
    assert json.loads(calls[0]["argv"][calls[0]["argv"].index("--json-schema") + 1]) == LLMPlanNarrative.model_json_schema()

    bad = {"reasoning_summary": "Delete src/lib/payments.ts now, it is safe to delete.", "statements": []}
    plan = CleanupPlanner(ClaudeCodeProvider(runner=fake(ok(bad)))).plan(analysis=analysis, stale=stale, patch=patch)
    assert plan.llm.status == LLMStatus.REJECTED_INVALID_OUTPUT


def test_planner_survives_cli_failure(ready_case):
    analysis, stale, patch = ready_case
    p = ClaudeCodeProvider(runner=fake({"is_error": True, "subtype": "success", "result": "Not logged in"}, 1))
    plan = CleanupPlanner(p).plan(analysis=analysis, stale=stale, patch=patch)
    assert plan.llm.status == LLMStatus.UNAVAILABLE and "Not logged in" in plan.llm.error
    assert plan.statements and plan.proposed_changes


# --- live -------------------------------------------------------------------------------

@pytest.mark.claude_live
@pytest.mark.skipif(os.environ.get("FLAGPILOT_LIVE_CLAUDE") != "1", reason="set FLAGPILOT_LIVE_CLAUDE=1 to call the real claude CLI (costs money)")
def test_live_claude_cli_plan(ready_case, monkeypatch):
    monkeypatch.undo()
    analysis, stale, patch = ready_case
    provider = ClaudeCodeProvider(max_budget_usd=float(os.environ.get("FLAGPILOT_LLM_MAX_BUDGET_USD", "1.0")))
    plan = CleanupPlanner(provider).plan(analysis=analysis, stale=stale, patch=patch)
    print(f"\nllm={plan.llm.model_dump(mode='json')} cost_usd={provider.last_cost_usd}\nsummary={plan.reasoning_summary}")
    for s in plan.statements:
        print(f"  [{s.type.value}] {s.text} {s.evidence_ids}")
    assert plan.llm.status in (LLMStatus.USED, LLMStatus.REJECTED_INVALID_OUTPUT), plan.llm
    ids = {e.id for e in plan.evidence}
    if plan.llm.status == LLMStatus.USED:
        for s in plan.statements:
            assert set(s.evidence_ids) <= ids
            if s.type == StatementType.FACT:
                assert s.evidence_ids
