"""Lifecycle-engine integration: POST/GET /api/flags/{id}/analysis."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app.main import app
from app.models.flag import Flag
from app.models.repository import Repository
from app.services.engine import EngineUnavailable
from app.services.github import GitHubAppClient, get_github_client_factory
from tests.conftest import WS_ID, TestingSession
from tests.test_github import PEM, make_tarball

needs_engine = pytest.mark.skipif(importlib.util.find_spec("lifecycle_engine") is None,
                                  reason="lifecycle engine not installed (pip install -r requirements-engine.txt)")

# A small TypeScript repo using the Flagsmith JS SDK.
TS_FILES = {
    "package.json": '{"name": "web", "scripts": {}}',
    "src/checkout.ts": (
        'import flagsmith from "flagsmith";\n'
        "export function total(items: number[]) {\n"
        '  if (flagsmith.hasFeature("new_checkout")) {\n'
        "    return items.reduce((a, b) => a + b, 0);\n"
        "  } else {\n"
        "    return Math.round(items.reduce((a, b) => a + b, 0));\n"
        "  }\n"
        "}\n"
    ),
    "src/experiments.ts": 'import flagsmith from "flagsmith";\nexport const on = (n: string) => flagsmith.hasFeature(n);\n',
    "src/comment.ts": "// new_checkout is mentioned here but never evaluated\n",
}


class FakeGitHub:
    def __init__(self, files=TS_FILES, fail=False):
        self.files, self.fail, self.tarballs = files, fail, 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if self.fail:
            return httpx.Response(500, json={"message": "boom"})
        if p == "/app/installations/42/access_tokens":
            return httpx.Response(201, json={"token": "ghs_inst"})
        if p == "/repos/acme/web/commits/main":
            return httpx.Response(200, json={"sha": "abc123"})
        if p == "/repos/acme/web/tarball/abc123":
            self.tarballs += 1
            return httpx.Response(200, content=make_tarball(self.files), headers={"content-type": "application/x-gzip"})
        return httpx.Response(404, json={"message": "Not Found"})


def use_github(fake: FakeGitHub):
    app.dependency_overrides[get_github_client_factory] = lambda: (
        lambda: GitHubAppClient(1, PEM, transport=httpx.MockTransport(fake.handler)))
    return fake


def add_repo(ws=WS_ID, installation_id="42"):
    with TestingSession() as db:
        db.add(Repository(workspace_id=ws, full_name="acme/web", name="web", default_branch="main",
                          installation_id=installation_id))
        db.commit()


@pytest.fixture()
def setup(client):
    add_repo()
    fake = use_github(FakeGitHub())
    flag = client.post("/api/flags", json={"name": "new_checkout", "environment": "Production", "enabled": True}).json()
    return client, fake, flag


@needs_engine
def test_analysis_runs_engine_and_is_stored(setup):
    client, fake, flag = setup
    r = client.post(f"/api/flags/{flag['id']}/analysis", json={"use_llm": False})
    assert r.status_code == 201, r.text
    body = r.json()
    assert fake.tarballs == 1
    assert body["repositories"] == [{"full_name": "acme/web", "commit_sha": "abc123"}]
    # SDK-aware discovery: the comment mention is not a reference; the dynamic call is reported separately
    refs = body["result"]["analysis"]["references"]
    assert [(x["location"]["file_path"], x["location"]["line"]) for x in refs] == [("src/checkout.ts", 3)]
    assert body["confirmed_references"] == 1 and body["dynamic_references"] == 1
    branch = body["result"]["analysis"]["branches"][0]
    assert branch["kind"] == "if_else" and branch["determinable"] is True
    # A flag created just now is young: never recommended for cleanup
    assert body["stale_status"] == "not_stale"
    assert any(e["kind"] == "flag_age" for e in body["result"]["stale_finding"]["evidence"])
    assert body["result"]["lifecycle_evaluation"]["is_recommendation"] is True
    assert body["result"]["cleanup_plan"]["llm"]["status"] == "not_configured"
    assert body["result"]["patch"] is None
    assert body["created_by"] == "tester@example.com"

    latest = client.get(f"/api/flags/{flag['id']}/analysis").json()
    assert latest["id"] == body["id"] and latest["result"] == body["result"]
    history = client.get(f"/api/flags/{flag['id']}/analyses").json()
    assert [h["id"] for h in history] == [body["id"]]
    logs = client.get("/api/audit-logs?resource_type=flag").json()
    assert any(l["action"] == "flag.analyzed" for l in logs)


@needs_engine
def test_analysis_never_changes_the_flag(setup):
    client, _, flag = setup
    client.post(f"/api/flags/{flag['id']}/analysis", json={"use_llm": False})
    after = client.get(f"/api/flags/{flag['id']}").json()
    assert after["lifecycle_status"] == flag["lifecycle_status"] == "created"
    assert after["enabled"] is True and after["updated_at"] == flag["updated_at"]


@needs_engine
def test_non_production_environment_means_insufficient_evidence(client):
    add_repo()
    use_github(FakeGitHub())
    flag = client.post("/api/flags", json={"name": "new_checkout", "environment": "Development", "enabled": True}).json()
    with TestingSession() as db:  # make it old enough that age doesn't decide
        from datetime import timedelta
        f = db.get(Flag, flag["id"])
        f.created_at = f.created_at - timedelta(days=400)
        db.commit()
    body = client.post(f"/api/flags/{flag['id']}/analysis", json={"use_llm": False}).json()
    assert body["stale_status"] == "insufficient_evidence"
    missing = {m["kind"] for m in body["result"]["stale_finding"]["missing_evidence"]}
    assert "production_state" in missing


@needs_engine
def test_history_keeps_every_run(setup):
    client, _, flag = setup
    ids = [client.post(f"/api/flags/{flag['id']}/analysis", json={"use_llm": False}).json()["id"] for _ in range(2)]
    history = [h["id"] for h in client.get(f"/api/flags/{flag['id']}/analyses").json()]
    assert sorted(history) == sorted(ids)


def test_not_analyzed_yet_and_unknown_flag(client):
    flag = client.post("/api/flags", json={"name": "x"}).json()
    assert client.get(f"/api/flags/{flag['id']}/analysis").status_code == 404
    assert client.post("/api/flags/nope/analysis", json={}).status_code == 404


@needs_engine
def test_needs_a_connected_repository(client):
    flag = client.post("/api/flags", json={"name": "x"}).json()
    r = client.post(f"/api/flags/{flag['id']}/analysis", json={})
    assert r.status_code == 400 and "GitHub" in r.json()["detail"]


@needs_engine
def test_github_failure_is_502(client):
    add_repo()
    use_github(FakeGitHub(fail=True))
    flag = client.post("/api/flags", json={"name": "x"}).json()
    assert client.post(f"/api/flags/{flag['id']}/analysis", json={}).status_code == 502


def test_engine_missing_is_503_not_a_crash(setup, monkeypatch):
    client, _, flag = setup

    def missing():
        raise EngineUnavailable("lifecycle engine is not installed")

    monkeypatch.setattr("app.api.routes.analysis.engine_version", missing)
    r = client.post(f"/api/flags/{flag['id']}/analysis", json={})
    assert r.status_code == 503 and "not installed" in r.json()["detail"]
    assert client.get("/api/flags").status_code == 200  # rest of the API unaffected


def test_other_workspaces_analyses_are_invisible(setup):
    client, _, _ = setup
    from app.models.flag_analysis import FlagAnalysis
    from app.models.workspace import Workspace
    with TestingSession() as db:
        db.add(Workspace(id="ws-other", installation_id="99", name="other", github_account_id="901",
                         github_account_login="other", github_account_type="Organization"))
        db.add(Flag(id="flag-other", workspace_id="ws-other", name="secret_flag"))
        db.add(FlagAnalysis(id="an-other", workspace_id="ws-other", flag_id="flag-other", engine_version="0",
                            stale_status="not_stale", stale_confidence=0.5, cleanup_direction="undetermined",
                            suggested_state="created", recommended_action="no_action", llm_status="not_configured",
                            result={}))
        db.commit()
    assert client.get("/api/flags/flag-other/analysis").status_code == 404
    assert client.get("/api/flags/flag-other/analyses").status_code == 404
    assert client.post("/api/flags/flag-other/analysis", json={}).status_code == 404


def test_app_starts_without_importing_the_engine():
    """The engine is imported lazily, so a deployment without it still serves the rest of the API."""
    code = ("import os; os.environ.setdefault('DATABASE_URL','sqlite://'); "
            "os.environ.setdefault('ENCRYPTION_KEY','x'*43+'='); os.environ.setdefault('SUPABASE_URL','https://t.supabase.co'); "
            "import sys, app.main; print('lifecycle_engine' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], check=True)
    assert out.stdout.strip() == "False"


@needs_engine
def test_llm_provider_comes_from_settings(setup, monkeypatch):
    """FLAGPILOT_LLM_PROVIDER in .env reaches the planner (pydantic-settings, not os.environ)."""
    from app.core.config import settings
    client, _, flag = setup
    monkeypatch.setattr(settings, "flagpilot_llm_provider", "mock")
    body = client.post(f"/api/flags/{flag['id']}/analysis", json={}).json()
    assert body["llm_status"] == "used" and body["result"]["cleanup_plan"]["llm"]["provider"] == "mock"
    off = client.post(f"/api/flags/{flag['id']}/analysis", json={"use_llm": False}).json()
    assert off["llm_status"] == "not_configured"
