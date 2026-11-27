from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lifecycle_engine.agents.llm import MockLLMProvider
from lifecycle_engine.api.router import create_router
from lifecycle_engine.api.service import EngineService, EngineSettings

from tests.builders import NOW, sample_configs
from tests.conftest import CHECKOUT, PRICING, SAMPLES

REPOS = [CHECKOUT.model_dump(), PRICING.model_dump()]


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    svc = EngineService(EngineSettings(allowed_roots=[SAMPLES]), llm=MockLLMProvider())
    app.include_router(create_router(svc), prefix="/api/v1/engine")
    return TestClient(app)


def cfg(name):
    return sample_configs()[name].model_dump(mode="json")


def test_discover(client):
    r = client.post("/api/v1/engine/discover", json={"repositories": REPOS})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["dynamic"] == 2 and body["repositories"] == ["acme/checkout-web", "acme/pricing-service"]


def test_analyze_and_evaluate(client):
    r = client.post("/api/v1/engine/flags/analyze", json={"repositories": REPOS, "flag_name": "new_checkout", "configuration": cfg("new_checkout")})
    assert r.status_code == 200 and r.json()["feature_scope"] == "full_feature_candidate"
    r = client.post("/api/v1/engine/lifecycle/evaluate", json={"repositories": REPOS, "flag_name": "dark_mode",
                                                              "configuration": cfg("dark_mode"), "as_of": NOW.isoformat()})
    assert r.status_code == 200 and r.json()["suggested_state"] == "rolling_out" and r.json()["is_recommendation"] is True


def test_stale_detect_sorted(client):
    r = client.post("/api/v1/engine/stale/detect", json={
        "repositories": REPOS, "configurations": list(map(cfg, sample_configs())), "as_of": NOW.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["not_stale"] >= 3
    assert all(f["requires_human_decision"] for f in body["findings"])


def test_cleanup_plan_and_patch(client):
    r = client.post("/api/v1/engine/cleanup/plan", json={"repositories": REPOS, "flag_name": "legacy_search",
                                                        "configuration": cfg("legacy_search"), "as_of": NOW.isoformat()})
    assert r.status_code == 200
    assert r.json()["plan"]["recommended_action"] == "request_owner_review" and r.json()["patch"] is None
    r = client.post("/api/v1/engine/cleanup/patch", json={"repositories": REPOS, "flag_name": "legacy_search",
                                                         "configuration": cfg("legacy_search")})
    assert r.status_code == 200 and r.json()["direction"] == "keep_disabled_path" and r.json()["files"]


def test_transition_errors_use_shared_format(client):
    lc = {"flag_id": "acme-web:new_checkout", "flag_name": "new_checkout", "state": "created"}
    r = client.post("/api/v1/engine/lifecycle/transition", json={"lifecycle": lc, "to_state": "retired", "actor": "a", "reason": "r"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    lc["state"] = "cleanup_in_progress"
    r = client.post("/api/v1/engine/lifecycle/transition", json={"lifecycle": lc, "to_state": "retired", "actor": "a", "reason": "r"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "transition_requirements_not_met"
    assert "cleanup_verified" in r.json()["error"]["details"]["missing_evidence"]


def test_path_outside_allowed_roots_rejected(client, tmp_path):
    r = client.post("/api/v1/engine/discover", json={"repositories": [{"repository": "x", "root_path": str(tmp_path)}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "path_not_allowed"
    r = client.post("/api/v1/engine/discover", json={"repositories": [{"repository": "x", "root_path": str(SAMPLES / "nope")}]})
    assert r.status_code == 404


def test_request_commands_rejected_by_default(client):
    patch = client.post("/api/v1/engine/cleanup/patch", json={"repositories": REPOS, "flag_name": "legacy_search",
                                                             "direction": "keep_disabled_path"}).json()
    r = client.post("/api/v1/engine/cleanup/verify", json={
        "repositories": REPOS, "patch": patch,
        "commands": [{"name": "x", "kind": "tests", "repository": "acme/checkout-web", "argv": ["rm", "-rf", "/"]}]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "commands_not_allowed"


def test_validation_errors_are_structured(client):
    r = client.post("/api/v1/engine/discover", json={"repositories": []})
    assert r.status_code == 422
