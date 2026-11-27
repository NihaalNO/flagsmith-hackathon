"""GroqProvider: Groq's OpenAI-compatible API with per-model JSON mode.

Unit tests never touch the network. The live test calls api.groq.com and only runs with
FLAGPILOT_LIVE_GROQ=1 and GROQ_API_KEY set.
"""

from __future__ import annotations

import json
import os

import pytest

from lifecycle_engine.agents.llm import (
    GROQ_BASE_URL,
    GROQ_DEFAULT_MODEL,
    GroqProvider,
    LLMUnavailableError,
    provider_from_env,
)
from lifecycle_engine.agents.planner import CleanupPlanner
from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.policies.stale_detector import StaleFlagDetector
from lifecycle_engine.schemas.planning import LLMStatus

from tests.builders import NOW, sample_configs
from tests.test_openai_compatible_provider import SCHEMA, _FakeOpenAI, completion, fake_post, fake_server  # noqa: F401


def test_defaults_to_groq_url_and_flagship_model():
    calls = []
    p = GroqProvider(api_key="gsk_test", post=fake_post(calls=calls))
    assert p.complete_json(system="S", prompt="P", schema=SCHEMA, context={}) == {"a": 1}
    assert (p.name, p.model) == ("groq", GROQ_DEFAULT_MODEL) == ("groq", "openai/gpt-oss-120b")
    assert calls[0]["url"] == f"{GROQ_BASE_URL}/chat/completions" == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer gsk_test"
    assert calls[0]["body"]["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("model,expected", [
    ("openai/gpt-oss-120b", "json_schema"),
    ("openai/gpt-oss-20b", "json_schema"),
    ("llama-3.3-70b-versatile", "json_object"),
    ("llama-3.1-8b-instant", "json_object"),
])
def test_auto_json_mode_follows_model_support(model, expected):
    calls = []
    p = GroqProvider(model=model, api_key="k", post=fake_post(calls=calls))
    assert p.json_mode == expected
    p.complete_json(system="S", prompt="P", schema=SCHEMA, context={})
    body = calls[0]["body"]
    assert body["response_format"]["type"] == expected
    if expected == "json_object":  # JSON mode: schema and an explicit JSON instruction go in the prompt
        assert "JSON" in body["messages"][1]["content"] and json.dumps(SCHEMA) in body["messages"][1]["content"]


def test_explicit_json_mode_overrides_auto():
    assert GroqProvider(model="llama-3.3-70b-versatile", api_key="k", json_mode="none").json_mode == "none"
    assert GroqProvider(api_key="k", json_mode="json_object").json_mode == "json_object"


def test_api_key_is_required():
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqProvider(api_key=None)


def test_groq_schema_mismatch_400_falls_back():
    text = json.dumps({"error": {"message": "Generated JSON does not match the expected schema. Please adjust your prompt.",
                                 "type": "invalid_request_error"}})
    with pytest.raises(LLMUnavailableError, match="HTTP 400: Generated JSON does not match") as err:
        GroqProvider(api_key="gsk_secret", post=fake_post(status=400, text=text)).complete_json(
            system="S", prompt="P", schema=SCHEMA, context={})
    assert "gsk_secret" not in str(err.value)


def test_rate_limit_falls_back():
    text = json.dumps({"error": {"message": "Rate limit reached for model", "type": "tokens"}})
    with pytest.raises(LLMUnavailableError, match="HTTP 429"):
        GroqProvider(api_key="k", post=fake_post(status=429, text=text)).complete_json(
            system="S", prompt="P", schema=SCHEMA, context={})


def test_provider_from_env(monkeypatch):
    for var in ("FLAGPILOT_LLM_MODEL", "FLAGPILOT_LLM_BASE_URL", "FLAGPILOT_LLM_API_KEY", "FLAGPILOT_LLM_JSON_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FLAGPILOT_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_groq_var")
    p = provider_from_env()
    assert isinstance(p, GroqProvider)
    assert (p.model, p.endpoint, p.api_key, p.json_mode) == (
        "openai/gpt-oss-120b", "https://api.groq.com/openai/v1/chat/completions", "gsk_from_groq_var", "json_schema")
    monkeypatch.setenv("FLAGPILOT_LLM_API_KEY", "gsk_flagpilot")   # takes precedence
    monkeypatch.setenv("FLAGPILOT_LLM_MODEL", "llama-3.3-70b-versatile")
    p = provider_from_env()
    assert (p.api_key, p.model, p.json_mode) == ("gsk_flagpilot", "llama-3.3-70b-versatile", "json_object")
    monkeypatch.delenv("FLAGPILOT_LLM_API_KEY")
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        provider_from_env()


def _legacy_search_case(targets):
    cfg = sample_configs()["legacy_search"]
    analysis = CodeIntelligence.from_targets(targets).analyze("legacy_search", cfg)
    return analysis, StaleFlagDetector().evaluate("legacy_search", config=cfg, analysis=analysis, now=NOW)


def test_planner_end_to_end_against_fake_groq(fake_server, sample_targets):  # noqa: F811
    analysis, stale = _legacy_search_case(sample_targets)
    _FakeOpenAI.reply = {"reasoning_summary": "Narrative from Groq.", "statements": []}
    p = GroqProvider(model="llama-3.3-70b-versatile", api_key="gsk_local", base_url=fake_server)
    plan = CleanupPlanner(p).plan(analysis=analysis, stale=stale)
    assert plan.llm.status == LLMStatus.USED and plan.llm.provider == "groq"
    assert plan.llm.model == "llama-3.3-70b-versatile" and plan.reasoning_summary == "Narrative from Groq."
    [req] = _FakeOpenAI.received
    assert req["auth"] == "Bearer gsk_local" and req["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.groq_live
@pytest.mark.skipif(not (os.environ.get("FLAGPILOT_LIVE_GROQ") == "1" and os.environ.get("GROQ_API_KEY")),
                    reason="set FLAGPILOT_LIVE_GROQ=1 and GROQ_API_KEY to call the real Groq API")
@pytest.mark.parametrize("model", [GROQ_DEFAULT_MODEL, "llama-3.3-70b-versatile"])
def test_live_groq_plan(sample_targets, model):
    analysis, stale = _legacy_search_case(sample_targets)
    p = GroqProvider(model=model, api_key=os.environ["GROQ_API_KEY"])
    plan = CleanupPlanner(p).plan(analysis=analysis, stale=stale)
    print(f"\n[{model} / {p.json_mode}] llm={plan.llm.model_dump(mode='json')}\nsummary={plan.reasoning_summary}")
    assert plan.llm.status in (LLMStatus.USED, LLMStatus.REJECTED_INVALID_OUTPUT), plan.llm
