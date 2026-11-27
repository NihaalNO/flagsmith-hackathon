"""Flagsmith sync of per-environment state + feature metadata, and its use by the lifecycle engine."""

import importlib.util
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.main import app
from app.models.integration import Integration
from app.services.flagsmith import FlagsmithClient, get_flagsmith_client_factory
from tests.test_analysis import add_repo, use_github, FakeGitHub
from tests.test_flagsmith import GOOD_KEY

needs_engine = pytest.mark.skipif(importlib.util.find_spec("lifecycle_engine") is None,
                                  reason="lifecycle engine not installed (pip install -r requirements-engine.txt)")

OLD = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat().replace("+00:00", "Z")
STABLE = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat().replace("+00:00", "Z")
RECENT = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")

FEATURES = [
    {"id": 11, "name": "new_checkout", "description": "One-page checkout", "default_enabled": False,
     "created_date": OLD, "tags": [1], "owners": [42], "group_owners": [], "type": "STANDARD", "is_archived": False},
    {"id": 12, "name": "dark_mode", "description": None, "default_enabled": False,
     "created_date": OLD, "tags": [], "owners": [], "type": "STANDARD", "is_archived": False},
    {"id": 13, "name": "button_copy", "description": None, "default_enabled": True,
     "created_date": OLD, "tags": [2], "owners": [], "type": "MULTIVARIATE", "is_archived": False},
]
# environment id -> feature id -> extra fields returned with ?environment=
ENV_STATES = {
    7: {  # Development
        11: {"environment_feature_state": {"enabled": True, "feature_state_value": None},
             "num_segment_overrides": 0, "num_identity_overrides": 0, "last_modified_in_current_environment": STABLE},
        12: {"environment_feature_state": {"enabled": True, "feature_state_value": None},
             "num_segment_overrides": 0, "num_identity_overrides": 0, "last_modified_in_current_environment": STABLE},
        13: {"environment_feature_state": {"enabled": True, "feature_state_value": "Buy now"},
             "num_segment_overrides": 0, "num_identity_overrides": 0, "last_modified_in_current_environment": STABLE},
    },
    8: {  # Production
        11: {"environment_feature_state": {"enabled": True, "feature_state_value": None},
             "num_segment_overrides": 0, "num_identity_overrides": None,  # Edge project: unknown
             "last_modified_in_current_environment": STABLE},
        12: {"environment_feature_state": {"enabled": True, "feature_state_value": None},
             "num_segment_overrides": 2, "num_identity_overrides": 0, "last_modified_in_current_environment": RECENT},
        13: {"environment_feature_state": {"enabled": True, "feature_state_value": "Buy now",
                                           "multivariate_feature_state_values": [
                                               {"multivariate_feature_option": 1, "percentage_allocation": 50},
                                               {"multivariate_feature_option": 2, "percentage_allocation": 50}]},
             "num_segment_overrides": 0, "num_identity_overrides": 0, "last_modified_in_current_environment": STABLE},
    },
}


class RichFlagsmith:
    def __init__(self):
        self.env_states = {e: dict(v) for e, v in ENV_STATES.items()}
        self.environments = [{"id": 7, "name": "Development", "api_key": "env-dev"},
                             {"id": 8, "name": "Production", "api_key": "env-prod"}]
        self.tags_status = 200

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("Authorization") != f"Api-Key {GOOD_KEY}":
            return httpx.Response(401, json={"detail": "Invalid key"})
        path, q = req.url.path, req.url.params
        if path == "/api/v1/projects/":
            return httpx.Response(200, json=[{"id": 5, "name": "web-app", "organisation": 1}])
        if path == "/api/v1/environments/" and q.get("project") == "5":
            return httpx.Response(200, json={"count": 2, "next": None, "results": self.environments})
        if path == "/api/v1/projects/5/tags/":
            if self.tags_status != 200:
                return httpx.Response(self.tags_status, json={"detail": "You do not have permission"})
            return httpx.Response(200, json={"count": 2, "next": None, "results": [
                {"id": 1, "label": "release"}, {"id": 2, "label": "experiment"}]})
        if path == "/api/v1/projects/5/features/":
            env = q.get("environment")
            if env is None:
                return httpx.Response(200, json={"count": 3, "next": None, "results": FEATURES})
            if env == "9":
                return httpx.Response(403, json={"detail": "no access"})
            states = self.env_states[int(env)]
            return httpx.Response(200, json={"count": 3, "next": None,
                                             "results": [{**f, **states[f["id"]]} for f in FEATURES]})
        if path == "/api/v1/environments/env-prod/featurestates/":
            return httpx.Response(200, json={"count": 3, "next": None, "results": [
                {"id": 100 + f["id"], "feature": f["id"], "enabled": True, "identity": None, "feature_segment": None}
                for f in FEATURES]})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture()
def rich(client):
    fake = RichFlagsmith()

    def factory(integration: Integration) -> FlagsmithClient:
        from app.core.security import decrypt_secret
        return FlagsmithClient(decrypt_secret(integration.encrypted_secret), transport=httpx.MockTransport(fake.handler))

    app.dependency_overrides[get_flagsmith_client_factory] = lambda: factory
    integ = client.post("/api/integrations", json={
        "provider": "flagsmith", "name": "Flagsmith", "api_token": GOOD_KEY,
        "config": {"project_id": 5, "environment_key": "env-prod"}}).json()
    return client, fake, integ


def sync(client, integ):
    r = client.post(f"/api/integrations/{integ['id']}/sync")
    assert r.status_code == 200, r.text
    return r.json()


def flags_by_name(client):
    return {f["name"]: f for f in client.get("/api/flags").json()}


def test_sync_records_every_environment_and_metadata(rich):
    client, _, integ = rich
    result = sync(client, integ)
    assert result["created"] == 3 and result["environment_states"] == 6 and result["warnings"] == []

    flags = flags_by_name(client)
    envs = {e["environment_name"]: e for e in client.get(f"/api/flags/{flags['dark_mode']['id']}/environments").json()}
    assert set(envs) == {"Development", "Production"}
    prod = envs["Production"]
    assert prod["is_production"] is True and envs["Development"]["is_production"] is False
    assert prod["enabled"] is True and prod["segment_overrides"] == 2 and prod["identity_overrides"] == 0
    assert prod["last_modified_at"].startswith(RECENT[:10])

    mv = {e["environment_name"]: e for e in client.get(f"/api/flags/{flags['button_copy']['id']}/environments").json()}
    assert mv["Production"]["percentage_allocation"] == 50 and mv["Production"]["value"] == "Buy now"
    assert mv["Development"]["percentage_allocation"] is None

    edge = {e["environment_name"]: e for e in client.get(f"/api/flags/{flags['new_checkout']['id']}/environments").json()}
    assert edge["Production"]["identity_overrides"] is None  # not reported on Edge projects

    # The board's single mirrored state is unchanged behaviour.
    assert flags["new_checkout"]["environment"] == "Production" and flags["new_checkout"]["enabled"] is True


def test_resync_is_idempotent_and_tracks_changes(rich):
    client, fake, integ = rich
    sync(client, integ)
    again = sync(client, integ)
    assert again["unchanged"] == 3 and again["updated"] == 0 and again["environment_states"] == 6
    flags = flags_by_name(client)
    assert len(client.get(f"/api/flags/{flags['dark_mode']['id']}/environments").json()) == 2

    # Overrides removed upstream and an environment deleted.
    fake.env_states[8][12] = {**fake.env_states[8][12], "num_segment_overrides": 0}
    fake.environments = [e for e in fake.environments if e["id"] != 7]
    sync(client, integ)
    envs = client.get(f"/api/flags/{flags['dark_mode']['id']}/environments").json()
    assert [(e["environment_name"], e["segment_overrides"]) for e in envs] == [("Production", 0)]


def test_missing_permissions_are_warnings_not_failures(rich):
    client, fake, integ = rich
    fake.tags_status = 403
    fake.environments.append({"id": 9, "name": "Locked", "api_key": "env-locked"})
    result = sync(client, integ)
    assert result["created"] == 3 and result["environment_states"] == 6
    assert any("tags not read" in w for w in result["warnings"])
    assert any("'Locked' skipped" in w for w in result["warnings"])


def test_metadata_is_stored(rich):
    client, _, integ = rich
    sync(client, integ)
    from app.models.flag import Flag
    from tests.conftest import TestingSession
    with TestingSession() as db:
        f = {x.name: x for x in db.query(Flag).all()}
        assert f["new_checkout"].flagsmith_tags == ["release"] and f["new_checkout"].flagsmith_owner_ids == [42]
        assert f["button_copy"].flagsmith_type == "MULTIVARIATE" and f["button_copy"].flagsmith_tags == ["experiment"]
        assert f["new_checkout"].flagsmith_created_at.date().isoformat() == OLD[:10]
        assert f["new_checkout"].is_archived_in_flagsmith is False


# --- lifecycle engine uses the synced data -------------------------------------------------

def analyze(client, flag_id):
    r = client.post(f"/api/flags/{flag_id}/analysis", json={"use_llm": False})
    assert r.status_code == 201, r.text
    return r.json()


@needs_engine
def test_engine_sees_production_state_and_targeting(rich):
    client, _, integ = rich
    sync(client, integ)
    add_repo()
    use_github(FakeGitHub())
    flags = flags_by_name(client)

    checkout = analyze(client, flags["new_checkout"]["id"])
    cfg = checkout["result"]["flag_configuration"]
    assert {e["environment"]: e["is_production"] for e in cfg["environments"]} == {"Development": False, "Production": True}
    assert cfg["data_source"] == "flagsmith-sync" and cfg["tags"] == ["release"]
    assert cfg["owners"] == ["flagsmith-user:42"] and cfg["created_at"].startswith(OLD[:10])
    evidence = {e["kind"]: e for e in checkout["result"]["stale_finding"]["evidence"]}
    assert evidence["production_state"]["direction"] == "supports"      # uniform 100% in production
    assert evidence["config_stability"]["direction"] == "supports"      # unchanged for 120 days
    assert checkout["cleanup_direction"] == "keep_enabled_path"
    # Owner confirmation is still not collected, so never "ready for cleanup".
    assert checkout["stale_status"] == "potentially_stale"
    assert any("Edge project" in n for n in checkout["result"]["data_notes"])

    dark = analyze(client, flags["dark_mode"]["id"])
    assert dark["stale_status"] == "not_stale"                          # 2 segment overrides in production
    assert any(e["kind"] == "production_targeting" for e in dark["result"]["stale_finding"]["evidence"])

    split = analyze(client, flags["button_copy"]["id"])
    assert split["stale_status"] == "not_stale"                         # 50/50 multivariate split


@needs_engine
def test_without_a_production_environment_the_engine_says_so(rich, monkeypatch):
    from app.core.config import settings
    client, _, integ = rich
    sync(client, integ)
    add_repo()
    use_github(FakeGitHub())
    monkeypatch.setattr(settings, "production_environment_names", "live")
    body = analyze(client, flags_by_name(client)["new_checkout"]["id"])
    assert body["stale_status"] == "insufficient_evidence"
    assert any("PRODUCTION_ENVIRONMENT_NAMES" in n for n in body["result"]["data_notes"])
