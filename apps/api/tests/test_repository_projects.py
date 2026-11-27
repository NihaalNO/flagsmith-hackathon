"""Linking repositories to the Flagsmith project that actually holds their flags."""

import httpx
import pytest

from app.core.security import decrypt_secret
from app.main import app
from app.models.repository import Repository
from app.services.flagsmith import FlagsmithClient, get_flagsmith_client_factory
from app.services.projects import match_existing_project
from tests.test_flagsmith import GOOD_KEY, FakeFlagsmith
from tests.test_github import gh  # noqa: F401  (fixture: fake GitHub with repo acme/web)


@pytest.fixture()
def fs(client):
    fake = FakeFlagsmith()  # existing project 5 "web-app" holding new_checkout + dark_mode
    app.dependency_overrides[get_flagsmith_client_factory] = lambda: (
        lambda integ: FlagsmithClient(decrypt_secret(integ.encrypted_secret), transport=httpx.MockTransport(fake.handler)))
    integ = client.post("/api/integrations", json={
        "provider": "flagsmith", "name": "Flagsmith", "api_token": GOOD_KEY}).json()
    return fake, integ


def test_existing_project_with_flags_can_be_linked_and_scanned(client, gh, fs):  # noqa: F811
    fake, _ = fs
    # The reported problem: repo "acme/web" doesn't match project "web-app", so import creates
    # a new empty project and scans find none of the flags that exist in Flagsmith.
    imported = client.post("/api/github/sync").json()
    assert imported["projects_created"] == ["acme/web"]
    repo = client.get("/api/repositories").json()[0]
    assert repo["flagsmith_project_id"] == 6
    assert client.post(f"/api/repositories/{repo['id']}/scan").json()["flags"] == []

    # Fix: link the repo to the project that holds its flags.
    r = client.put(f"/api/repositories/{repo['id']}/flagsmith-project", json={"flagsmith_project_id": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repository"]["flagsmith_project_id"] == 5 and body["flagsmith_project_name"] == "web-app"
    assert body["sync_error"] is None and "web-app" in body["sync"]["projects"] and body["sync"]["created"] == 2

    flags = {f["name"]: f for f in client.get("/api/flags").json()}
    assert flags["new_checkout"]["flagsmith_project_id"] == 5
    scan = client.post(f"/api/repositories/{repo['id']}/scan").json()
    assert scan["flags"] == [{"flag": "new_checkout", "references": 1}]

    logs = client.get("/api/audit-logs?resource_type=repository").json()
    link = next(l for l in logs if l["action"] == "repository.flagsmith_project_linked")
    assert link["details"] == {"from": 6, "to": 5, "project": "web-app"}


def test_import_links_a_project_whose_name_matches_the_repo(client, gh, fs):  # noqa: F811
    fake, _ = fs
    fake.projects.append({"id": 77, "name": "Web", "organisation": 1})  # "acme/web" -> "Web"
    r = client.post("/api/github/sync").json()
    assert r["projects_linked"] == ["acme/web"] and r["projects_created"] == []
    assert client.get("/api/repositories").json()[0]["flagsmith_project_id"] == 77
    assert [p["name"] for p in fake.projects] == ["web-app", "Web"]  # nothing created in Flagsmith


def test_ambiguous_names_are_not_guessed(client, gh, fs):  # noqa: F811
    fake, _ = fs
    fake.projects += [{"id": 77, "name": "Web", "organisation": 1}, {"id": 78, "name": "web!", "organisation": 1}]
    r = client.post("/api/github/sync").json()
    assert r["projects_created"] == [] and r["projects_linked"] == []
    assert "several Flagsmith projects match (Web, web!)" in r["project_errors"][0]
    assert client.get("/api/repositories").json()[0]["flagsmith_project_id"] is None
    assert len(fake.projects) == 3


@pytest.mark.parametrize("projects,expected", [
    ({"acme/web-app": 1, "web-app": 2}, (1, [])),          # exact full name wins
    ({"Web App": 3}, (3, [])),                              # case and punctuation ignored
    ({"WEB_APP": 4}, (4, [])),
    ({"web-application": 5}, (None, [])),                   # no fuzzy guessing
    ({"Web App": 6, "web.app": 7}, (None, ["Web App", "web.app"])),
])
def test_project_name_matching(projects, expected):
    repo = Repository(full_name="acme/web-app", name="web-app")
    assert match_existing_project(repo, projects) == expected


def test_link_errors(client, gh, fs):  # noqa: F811
    client.post("/api/github/sync")
    repo = client.get("/api/repositories").json()[0]
    r = client.put(f"/api/repositories/{repo['id']}/flagsmith-project", json={"flagsmith_project_id": 999})
    assert r.status_code == 404 and "999 not found" in r.json()["detail"]
    assert client.get("/api/repositories").json()[0]["flagsmith_project_id"] == 6  # unchanged
    assert client.put("/api/repositories/nope/flagsmith-project", json={"flagsmith_project_id": 5}).status_code == 404


def test_link_needs_a_flagsmith_integration(client):
    repo = client.post("/api/repositories", json={"full_name": "acme/manual"}).json()
    r = client.put(f"/api/repositories/{repo['id']}/flagsmith-project", json={"flagsmith_project_id": 5})
    assert r.status_code == 400 and "Connections" in r.json()["detail"]


def test_link_is_kept_when_the_follow_up_sync_fails(client, gh, fs):  # noqa: F811
    fake, _ = fs
    client.post("/api/github/sync")
    repo = client.get("/api/repositories").json()[0]
    original = fake.handler

    def failing_sync(req):  # project list works, feature listing for the sync fails
        if req.url.path.endswith("/features/"):
            return httpx.Response(500, text="boom")
        return original(req)

    fake.handler = failing_sync
    body = client.put(f"/api/repositories/{repo['id']}/flagsmith-project", json={"flagsmith_project_id": 5}).json()
    assert body["repository"]["flagsmith_project_id"] == 5
    assert body["sync"] is None and body["sync_error"].startswith("Linked, but the Flagsmith sync failed")
    assert client.get("/api/repositories").json()[0]["flagsmith_project_id"] == 5


# --- "Nothing to sync" -------------------------------------------------------------------

def _sync(client, integ_id):
    return client.post(f"/api/integrations/{integ_id}/sync")


def test_reconnecting_flagsmith_keeps_repo_projects(client, gh, fs):  # noqa: F811
    """Removing and re-adding the Flagsmith connection used to orphan every repo link."""
    _, integ = fs
    client.post("/api/github/sync")
    repo = client.get("/api/repositories").json()[0]
    client.put(f"/api/repositories/{repo['id']}/flagsmith-project", json={"flagsmith_project_id": 5})
    assert client.delete(f"/api/integrations/{integ['id']}").status_code == 204
    new = client.post("/api/integrations", json={"provider": "flagsmith", "name": "Flagsmith", "api_token": GOOD_KEY}).json()

    r = _sync(client, new["id"])
    assert r.status_code == 200, r.text
    assert r.json()["projects"] == ["web-app"]
    assert not any("No repository is linked" in w or "isn't visible" in w for w in r.json()["warnings"])
    from tests.conftest import TestingSession
    with TestingSession() as db:
        assert db.get(Repository, repo["id"]).flagsmith_integration_id == new["id"]  # re-attached


def test_repo_linked_through_another_connection_is_synced(client, gh, fs):  # noqa: F811
    _, first = fs
    client.post("/api/github/sync")
    repo = client.get("/api/repositories").json()[0]
    client.put(f"/api/repositories/{repo['id']}/flagsmith-project", json={"flagsmith_project_id": 5})
    second = client.post("/api/integrations", json={"provider": "flagsmith", "name": "Flagsmith 2", "api_token": GOOD_KEY}).json()
    r = _sync(client, second["id"]).json()
    assert "web-app" in r["projects"]
    assert not any("No repository is linked" in w for w in r["warnings"])


def test_link_to_an_invisible_project_is_a_warning_not_a_failure(client, gh, fs):  # noqa: F811
    _, integ = fs
    client.post("/api/github/sync")  # creates and links project 6 "acme/web"
    from tests.conftest import TestingSession
    with TestingSession() as db:
        db.get(Repository, client.get("/api/repositories").json()[0]["id"]).flagsmith_project_id = 4040
        db.commit()
    r = _sync(client, integ["id"])
    assert r.status_code == 200, r.text
    assert any("#4040 isn't visible" in w for w in r.json()["warnings"])


def _serve_empty_projects(fake, ids):
    """Make the fake answer (with nothing) for extra project ids."""
    original = fake.handler

    def handler(req):
        path, q = req.url.path, req.url.params
        if path == "/api/v1/environments/" and q.get("project") and int(q["project"]) in ids:
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        if any(path == f"/api/v1/projects/{i}/{part}/" for i in ids for part in ("features", "tags")):
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        return original(req)

    fake.handler = handler


def test_fallback_is_capped(client, fs):
    fake, integ = fs
    extra = [100 + i for i in range(12)]
    fake.projects += [{"id": pid, "name": f"proj-{pid - 100:02d}", "organisation": 1} for pid in extra]
    _serve_empty_projects(fake, set(extra))
    r = _sync(client, integ["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["projects"]) == 10 and body["projects"][0] == "proj-00"
    assert any("first 10 of 13" in w for w in body["warnings"])


def test_key_that_sees_no_projects_is_a_clear_error(client, fs):
    fake, integ = fs
    fake.projects.clear()
    r = _sync(client, integ["id"])
    assert r.status_code == 502 and "can't see any projects" in r.json()["detail"]


def test_configured_project_the_key_cannot_see_is_still_an_error(client, fs):
    _, integ = fs
    client.patch(f"/api/integrations/{integ['id']}", json={"config": {"project_id": 999}})
    r = _sync(client, integ["id"])
    assert r.status_code == 502 and "999 (config.project_id) not found" in r.json()["detail"]


def test_one_failing_project_does_not_stop_the_others(client, fs):
    fake, integ = fs
    fake.projects.append({"id": 200, "name": "locked", "organisation": 1})  # fake has no data for it -> 404
    r = _sync(client, integ["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projects"] == ["web-app"] and body["created"] == 2
    assert any(w.startswith("locked: skipped") for w in body["warnings"])

    # When every project fails, the sync is an error rather than an empty success.
    original = fake.handler
    fake.handler = lambda req: (httpx.Response(500, text="boom") if req.url.path == "/api/v1/projects/5/features/"
                                else original(req))
    r2 = _sync(client, integ["id"])
    assert r2.status_code == 502 and "No project could be synced" in r2.json()["detail"]


def test_partial_failure_keeps_flags_of_skipped_projects(client, fs):
    fake, integ = fs
    fake.projects.append({"id": 300, "name": "other", "organisation": 1})
    _serve_empty_projects(fake, {300})
    assert _sync(client, integ["id"]).json()["created"] == 2
    original = fake.handler
    fake.handler = lambda req: (httpx.Response(500, text="boom") if req.url.path == "/api/v1/projects/5/features/"
                                else original(req))
    body = _sync(client, integ["id"]).json()
    assert body["projects"] == ["other"] and any(w.startswith("web-app: skipped") for w in body["warnings"])
    assert body["missing_in_flagsmith"] == []   # not checked, so not called missing
