import json

import httpx
import pytest

from app.main import app
from app.models.integration import Integration
from app.services.flagsmith import FlagsmithClient, get_flagsmith_client_factory

GOOD_KEY = "org-key-123"


class FakeFlagsmith:
    """In-memory stand-in for the Flagsmith Management API."""

    def __init__(self):
        self.features = [
            {"id": 11, "name": "new_checkout", "description": "New checkout", "default_enabled": False},
            {"id": 12, "name": "dark_mode", "description": None, "default_enabled": True},
        ]
        self.states = [
            {"id": 1, "feature": 11, "enabled": True, "identity": None, "feature_segment": None},
            {"id": 2, "feature": 12, "enabled": False, "identity": None, "feature_segment": None},
            # identity override must be ignored
            {"id": 3, "feature": 12, "enabled": True, "identity": 99, "feature_segment": None},
        ]
        self.requests: list[httpx.Request] = []
        self.projects = [{"id": 5, "name": "web-app", "organisation": 1}]
        self.repo_features: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Api-Key {GOOD_KEY}":
            return httpx.Response(401, json={"detail": "Invalid key"})
        path, q = request.url.path, request.url.params
        if path == "/api/v1/organisations/":
            return httpx.Response(200, json={"count": 1, "next": None, "results": [{"id": 1, "name": "Acme"}]})
        if path == "/api/v1/projects/" and request.method == "POST":
            body = json.loads(request.content)
            project = {"id": 5 + len(self.projects), "name": body["name"], "organisation": body["organisation"]}
            self.projects.append(project)
            return httpx.Response(201, json=project)
        if path == "/api/v1/projects/":
            return httpx.Response(200, json=self.projects)
        if path == "/api/v1/environments/" and q.get("project") == "6":
            return httpx.Response(200, json={"count": 2, "next": None, "results": [
                {"id": 8, "name": "Development", "api_key": "env-dev6"},
                {"id": 9, "name": "Production", "api_key": "env-prod6"}]})
        if path == "/api/v1/projects/6/features/" and request.method == "GET":
            return httpx.Response(200, json={"count": len(self.repo_features), "next": None,
                                             "results": self.repo_features})
        if path == "/api/v1/projects/6/features/" and request.method == "POST":
            body = json.loads(request.content)
            feature = {"id": 600 + len(self.repo_features), "description": None, "default_enabled": False, **body}
            self.repo_features.append(feature)
            return httpx.Response(201, json=feature)
        if path == "/api/v1/environments/env-dev6/featurestates/":
            return httpx.Response(200, json={"count": 1, "next": None, "results": [
                {"id": 60, "feature": f["id"], "enabled": True, "identity": None, "feature_segment": None}
                for f in self.repo_features]})
        if path == "/api/v1/environments/" and q.get("project") == "5":
            return httpx.Response(200, json={"count": 1, "next": None,
                                             "results": [{"id": 7, "name": "Production", "api_key": "env-prod"}]})
        if path == "/api/v1/projects/5/features/" and request.method == "GET":
            # two pages to exercise pagination
            if q.get("page") == "2":
                return httpx.Response(200, json={"count": 2, "next": None, "results": self.features[1:]})
            return httpx.Response(200, json={"count": 2, "results": self.features[:1],
                                             "next": "https://api.flagsmith.com/api/v1/projects/5/features/?page=2"})
        if path == "/api/v1/projects/5/features/" and request.method == "POST":
            body = json.loads(request.content)
            feature = {"id": 100 + len(self.features), "description": None, "default_enabled": False, **body}
            self.features.append(feature)
            return httpx.Response(201, json=feature)
        if path == "/api/v1/environments/env-prod/featurestates/":
            return httpx.Response(200, json={"count": 3, "next": None, "results": self.states})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture()
def fake(client):
    fake = FakeFlagsmith()

    def factory(integration: Integration) -> FlagsmithClient:
        from app.core.security import decrypt_secret
        return FlagsmithClient(decrypt_secret(integration.encrypted_secret),
                               transport=httpx.MockTransport(fake.handler))

    app.dependency_overrides[get_flagsmith_client_factory] = lambda: factory
    return fake


def make_integration(client, token=GOOD_KEY, **config):
    r = client.post("/api/integrations", json={
        "provider": "flagsmith", "name": "Flagsmith", "api_token": token,
        "config": {"project_id": 5, "environment_key": "env-prod", **config},
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_token_is_write_only_and_encrypted(client, fake):
    from app.core.database import get_db
    integ = make_integration(client)
    assert integ["has_secret"] is True
    assert GOOD_KEY not in json.dumps(integ)
    db = next(app.dependency_overrides[get_db]())
    stored = db.get(Integration, integ["id"]).encrypted_secret
    assert stored and GOOD_KEY not in stored


def test_connection_ok_and_bad_key(client, fake):
    integ = make_integration(client)
    r = client.post(f"/api/integrations/{integ['id']}/test").json()
    assert r == {"ok": True, "detail": "Connected", "organisations": ["Acme"]}
    assert client.get(f"/api/integrations/{integ['id']}").json()["status"] == "connected"

    bad = make_integration(client, token="wrong")
    r = client.post(f"/api/integrations/{bad['id']}/test").json()
    assert r["ok"] is False and "rejected" in r["detail"]
    assert client.get(f"/api/integrations/{bad['id']}").json()["status"] == "error"


def test_browse_projects_and_environments(client, fake):
    integ = make_integration(client)
    assert client.get(f"/api/integrations/{integ['id']}/flagsmith/projects").json()[0]["name"] == "web-app"
    envs = client.get(f"/api/integrations/{integ['id']}/flagsmith/projects/5/environments").json()
    assert envs == [{"id": 7, "name": "Production", "api_key": "env-prod"}]


def test_sync_imports_links_updates(client, fake):
    # Pre-existing FlagPilot-only flag with a matching name gets adopted, keeping lifecycle.
    local = client.post("/api/flags", json={"name": "dark_mode"}).json()
    client.post(f"/api/flags/{local['id']}/lifecycle", json={"status": "in_development"})

    integ = make_integration(client)
    r = client.post(f"/api/integrations/{integ['id']}/sync")
    assert r.status_code == 200, r.text
    result = r.json()
    # This fake serves neither tags nor per-environment state (see test_flagsmith_environments.py).
    assert result.pop("environment_states") == 0 and len(result.pop("warnings")) == 2
    assert result == {"projects": ["web-app"], "created": 1, "linked": 1, "updated": 1,
                      "unchanged": 0, "conflicts": [], "missing_in_flagsmith": []}

    flags = {f["name"]: f for f in client.get("/api/flags").json()}
    assert flags["new_checkout"]["flagsmith_id"] == "11"
    assert flags["new_checkout"]["enabled"] is True          # environment state, not default
    assert flags["new_checkout"]["project_name"] == "web-app"
    assert flags["new_checkout"]["environment"] == "Production"
    assert flags["dark_mode"]["enabled"] is False            # identity override ignored
    assert flags["dark_mode"]["lifecycle_status"] == "in_development"  # sync never touches lifecycle

    # Second sync: nothing changes. Then remove a feature upstream.
    assert client.post(f"/api/integrations/{integ['id']}/sync").json()["unchanged"] == 2
    fake.features = fake.features[:1]
    assert client.post(f"/api/integrations/{integ['id']}/sync").json()["missing_in_flagsmith"] == ["dark_mode"]


def test_sync_without_linked_projects_syncs_visible_ones(client, fake):
    # Used to fail with "Nothing to sync"; now it imports from the projects the key can see.
    r = client.post("/api/integrations", json={"provider": "flagsmith", "name": "x", "api_token": GOOD_KEY})
    r = client.post(f"/api/integrations/{r.json()['id']}/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projects"] == ["web-app"] and body["created"] == 2
    assert any("No repository is linked" in w for w in body["warnings"])


def test_create_flag_in_flagsmith(client, fake):
    integ = make_integration(client)
    r = client.post("/api/flags", json={"name": "beta_search", "integration_id": integ["id"], "enabled": True})
    assert r.status_code == 201, r.text
    assert r.json()["flagsmith_id"] == "102"
    post = [q for q in fake.requests if q.method == "POST"][0]
    assert json.loads(post.content) == {"name": "beta_search", "default_enabled": True}


def test_create_flag_flagsmith_failure_stores_nothing(client, fake):
    integ = make_integration(client, token="wrong")
    r = client.post("/api/flags", json={"name": "beta_search", "integration_id": integ["id"]})
    assert r.status_code == 502
    assert client.get("/api/flags").json() == []
