import httpx
import pytest

from app.core.auth import CurrentUser, current_actor, get_current_user
from app.main import app
from app.services.github import GitHubAppClient, get_github_client_factory
from tests.conftest import seed_workspace
from tests.test_github import PEM


def as_user(uid, gh_id=None, login=None):
    async def _u():
        current_actor.set(uid)
        return CurrentUser(id=uid, email=None, role="authenticated", github_id=gh_id, github_login=login)
    app.dependency_overrides[get_current_user] = _u


def test_workspaces_are_isolated(client):
    seed_workspace("ws-other", installation_id="77", login="other", members=("user-2",))
    mine = client.post("/api/flags", json={"name": "shared_name"}).json()
    client.post("/api/repositories", json={"full_name": "acme/web"})

    as_user("user-2")
    assert client.get("/api/flags").json() == []                       # can't list
    assert client.get(f"/api/flags/{mine['id']}").status_code == 404   # can't fetch by id
    assert client.patch(f"/api/flags/{mine['id']}", json={"owner": "x"}).status_code == 404
    assert client.delete(f"/api/flags/{mine['id']}").status_code == 404
    assert client.get("/api/repositories").json() == []
    assert client.get("/api/audit-logs").json() == []
    # Same flag name is fine in another workspace.
    theirs = client.post("/api/flags", json={"name": "shared_name"})
    assert theirs.status_code == 201
    assert theirs.json()["id"] != mine["id"]

    as_user("user-1")
    assert [f["id"] for f in client.get("/api/flags").json()] == [mine["id"]]


def test_no_access_without_membership(client):
    as_user("stranger")
    r = client.get("/api/flags")
    assert r.status_code == 409 and "install the GitHub App" in r.json()["detail"]
    assert client.get("/api/flags", headers={"X-Workspace-Id": "ws-acme"}).status_code == 403


def test_multiple_workspaces_need_header(client):
    seed_workspace("ws-two", installation_id="88", login="two", members=("user-1",))
    assert client.get("/api/flags").status_code == 409
    assert client.get("/api/flags", headers={"X-Workspace-Id": "ws-two"}).status_code == 200


class FakeInstalls:
    """Installations: personal account of github user 1001, and org 'bigcorp'."""

    def __init__(self, org_members):
        self.org_members = org_members

    def handler(self, req):
        p = req.url.path
        if p == "/app/installations":
            return httpx.Response(200, json=[
                {"id": 501, "account": {"id": 1001, "login": "tester", "type": "User"}},
                {"id": 502, "account": {"id": 2002, "login": "bigcorp", "type": "Organization"}},
                {"id": 503, "account": {"id": 3003, "login": "someoneelse", "type": "User"}},
            ])
        if p.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_x"})
        if p.startswith("/orgs/bigcorp/members/"):
            return httpx.Response(204 if p.rsplit("/", 1)[1] in self.org_members else 404)
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture()
def installs(client):
    fake = FakeInstalls(org_members={"tester"})
    app.dependency_overrides[get_github_client_factory] = lambda: (
        lambda: GitHubAppClient(1, PEM, transport=httpx.MockTransport(fake.handler)))
    return fake


def test_discovery_personal_and_org(client, installs):
    as_user("u-tester", gh_id="1001", login="tester")
    r = client.get("/api/workspaces").json()
    got = {(w["github_account_login"], w["role"]) for w in r["workspaces"]}
    assert got == {("tester", "owner"), ("bigcorp", "member")}   # not "someoneelse"
    assert r["install_url"] is None or "installations/new" in r["install_url"]

    # Org member removed on GitHub -> access revoked on next check.
    installs.org_members = set()
    r = client.get("/api/workspaces").json()
    assert {w["github_account_login"] for w in r["workspaces"]} == {"tester"}


def test_discovery_new_user_without_install(client, installs):
    as_user("u-new", gh_id="4242", login="newbie")
    assert client.get("/api/workspaces").json()["workspaces"] == []
