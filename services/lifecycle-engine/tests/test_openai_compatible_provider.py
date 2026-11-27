"""OpenAICompatibleProvider: any server implementing OpenAI's Chat Completions API.

No external network: unit tests inject a fake `post`; the end-to-end test runs a local
HTTP server that speaks the OpenAI response format.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from lifecycle_engine.agents.llm import LLMUnavailableError, OpenAICompatibleProvider, provider_from_env
from lifecycle_engine.agents.planner import CleanupPlanner
from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.policies.stale_detector import StaleFlagDetector
from lifecycle_engine.schemas.planning import LLMPlanNarrative, LLMStatus

from tests.builders import NOW, sample_configs

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


def completion(content, finish="stop", refusal=None):
    msg = {"role": "assistant", "content": content}
    if refusal:
        msg["refusal"] = refusal
    return json.dumps({"id": "c1", "object": "chat.completion", "choices": [{"index": 0, "message": msg, "finish_reason": finish}]})


def fake_post(status=200, text=None, calls=None, exc=None):
    def post(url, headers, body, timeout):
        if calls is not None:
            calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if exc:
            raise exc
        return status, text if text is not None else completion('{"a": 1}')
    return post


def provider(**kw):
    defaults = dict(model="gpt-test", base_url="https://llm.example/v1/", api_key="sk-secret")
    defaults.update(kw)
    return OpenAICompatibleProvider(**defaults)


def call(p):
    return p.complete_json(system="SYS", prompt="PACKET", schema=SCHEMA, context={})


# --- request shape ------------------------------------------------------------------

def test_json_schema_request():
    calls = []
    assert call(provider(post=fake_post(calls=calls))) == {"a": 1}
    c = calls[0]
    assert c["url"] == "https://llm.example/v1/chat/completions"   # trailing slash handled
    assert c["headers"]["Authorization"] == "Bearer sk-secret"
    body = c["body"]
    assert body["model"] == "gpt-test"
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "PACKET"}]
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == SCHEMA
    assert rf["json_schema"]["strict"] is False   # plan schema has optional fields
    assert "max_tokens" not in body


@pytest.mark.parametrize("mode,response_format", [("json_object", {"type": "json_object"}), ("none", None)])
def test_fallback_json_modes_put_schema_in_prompt(mode, response_format):
    calls = []
    call(provider(json_mode=mode, max_tokens=2000, post=fake_post(calls=calls)))
    body = calls[0]["body"]
    assert body.get("response_format") == response_format
    assert json.dumps(SCHEMA) in body["messages"][1]["content"]
    assert body["max_tokens"] == 2000


def test_no_api_key_means_no_auth_header():
    calls = []
    call(provider(api_key=None, base_url="http://localhost:11434/v1", post=fake_post(calls=calls)))
    assert "Authorization" not in calls[0]["headers"]


def test_sends_explicit_user_agent():
    # Cloudflare-fronted APIs (Groq) answer 403 to urllib's default "Python-urllib" agent.
    calls = []
    call(provider(post=fake_post(calls=calls)))
    assert not calls[0]["headers"]["User-Agent"].startswith("Python-urllib")


# --- response handling ------------------------------------------------------------------

@pytest.mark.parametrize("content", ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '  {"a": 1}  '])
def test_fenced_or_padded_json_is_accepted(content):
    assert call(provider(post=fake_post(text=completion(content)))) == {"a": 1}


def test_content_parts_are_joined():
    text = json.dumps({"choices": [{"message": {"content": [{"type": "text", "text": '{"a"'}, {"type": "text", "text": ': 1}'}]},
                                    "finish_reason": "stop"}]})
    assert call(provider(post=fake_post(text=text))) == {"a": 1}


@pytest.mark.parametrize("kwargs,needle", [
    (dict(status=401, text='{"error": {"message": "Incorrect API key provided"}}'), "HTTP 401: Incorrect API key"),
    (dict(status=404, text="not found"), "HTTP 404"),
    (dict(text=completion("", refusal="I can't help with that")), "model refused"),
    (dict(text=completion('{"a": 1', finish="length")), "truncated"),
    (dict(text=completion("Sure! Here is the plan.")), "not valid JSON"),
    (dict(text=completion("[1, 2]")), "not a JSON object"),
    (dict(text=completion(None)), "no message content"),
    (dict(text="<html>gateway</html>"), "not an OpenAI-style"),
    (dict(exc=OSError("connection refused")), "could not reach"),
])
def test_failures_raise_llm_unavailable(kwargs, needle):
    with pytest.raises(LLMUnavailableError, match=needle) as err:
        call(provider(post=fake_post(**kwargs)))
    assert "sk-secret" not in str(err.value)


@pytest.mark.parametrize("kwargs,needle", [
    (dict(model=""), "model name"),
    (dict(base_url=""), "base URL"),
    (dict(base_url="ftp://x"), "base URL"),
    (dict(json_mode="xml"), "JSON_MODE"),
])
def test_configuration_is_validated(kwargs, needle):
    with pytest.raises(ValueError, match=needle):
        provider(**kwargs)


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("FLAGPILOT_LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("FLAGPILOT_LLM_MODEL", "llama3.1")
    monkeypatch.setenv("FLAGPILOT_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("FLAGPILOT_LLM_JSON_MODE", "json_object")
    monkeypatch.delenv("FLAGPILOT_LLM_API_KEY", raising=False)
    p = provider_from_env()
    assert isinstance(p, OpenAICompatibleProvider)
    assert (p.model, p.endpoint, p.api_key, p.json_mode) == ("llama3.1", "http://localhost:11434/v1/chat/completions", None, "json_object")
    monkeypatch.delenv("FLAGPILOT_LLM_BASE_URL")
    with pytest.raises(ValueError, match="base URL"):
        provider_from_env()


# --- end to end over real HTTP ---------------------------------------------------------

class _FakeOpenAI(BaseHTTPRequestHandler):
    received: list[dict] = []
    reply: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOpenAI.received.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        payload = completion(json.dumps(_FakeOpenAI.reply)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _FakeOpenAI.received = []
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_planner_end_to_end_over_http(fake_server, sample_targets):
    cfg = sample_configs()["legacy_search"]
    analysis = CodeIntelligence.from_targets(sample_targets).analyze("legacy_search", cfg)
    stale = StaleFlagDetector().evaluate("legacy_search", config=cfg, analysis=analysis, now=NOW)
    ref = analysis.references[0]
    _FakeOpenAI.reply = {
        "reasoning_summary": "legacy_search is disabled everywhere; the owner should confirm it can go.",
        "statements": [{"type": "fact", "text": "The flag is checked in search.ts.", "evidence_ids": [ref.id]},
                       {"type": "recommendation", "text": "Ask the search team to confirm.", "evidence_ids": []}],
    }
    p = OpenAICompatibleProvider(model="gpt-test", base_url=fake_server, api_key="sk-local")
    plan = CleanupPlanner(p).plan(analysis=analysis, stale=stale)

    assert plan.llm.status == LLMStatus.USED, plan.llm
    assert plan.llm.provider == "openai-compatible" and plan.llm.model == "gpt-test"
    assert plan.reasoning_summary.startswith("legacy_search is disabled")
    [req] = _FakeOpenAI.received
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer sk-local"
    assert req["body"]["response_format"]["json_schema"]["schema"] == LLMPlanNarrative.model_json_schema()
    assert "Evidence packet" in req["body"]["messages"][1]["content"]


def test_invented_facts_from_the_server_are_still_rejected(fake_server, sample_targets):
    cfg = sample_configs()["legacy_search"]
    analysis = CodeIntelligence.from_targets(sample_targets).analyze("legacy_search", cfg)
    stale = StaleFlagDetector().evaluate("legacy_search", config=cfg, analysis=analysis, now=NOW)
    _FakeOpenAI.reply = {"reasoning_summary": "Delete src/payments/old.ts; it is safe to delete.", "statements": []}
    plan = CleanupPlanner(OpenAICompatibleProvider(model="m", base_url=fake_server)).plan(analysis=analysis, stale=stale)
    assert plan.llm.status == LLMStatus.REJECTED_INVALID_OUTPUT


def test_unreachable_server_falls_back_to_deterministic_plan(sample_targets):
    cfg = sample_configs()["legacy_search"]
    analysis = CodeIntelligence.from_targets(sample_targets).analyze("legacy_search", cfg)
    stale = StaleFlagDetector().evaluate("legacy_search", config=cfg, analysis=analysis, now=NOW)
    p = OpenAICompatibleProvider(model="m", base_url="http://127.0.0.1:9/v1", timeout_s=3)  # discard port
    plan = CleanupPlanner(p).plan(analysis=analysis, stale=stale)
    assert plan.llm.status == LLMStatus.UNAVAILABLE and "could not reach" in plan.llm.error
    assert plan.statements
