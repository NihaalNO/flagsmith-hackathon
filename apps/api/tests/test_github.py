import io
import json
import tarfile

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.main import app
from app.services.code_scan import scan_tarball
from app.services.github import GitHubAppClient, get_github_client_factory

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                        serialization.NoEncryption()).decode()


def make_tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(f"acme-web-abc123/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


REPO_FILES = {
    "src/checkout.ts": "if (flags.isEnabled('new_checkout')) {\n  render();\n}\nconst x = new_checkout_v2;\n",
    "src/banner.py": "show = client.is_enabled(\"promo_banner\")\n# promo_banner is old\n",
    "node_modules/lib/index.js": "new_checkout\n",          # skipped dir
    "assets/logo.png": "new_checkout",                       # skipped suffix
    "README.md": "Nothing here",
}


class FakeGitHub:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        auth = req.headers.get("Authorization", "")
        p = req.url.path
        if p == "/app/installations":
            assert auth.startswith("Bearer ")
            return httpx.Response(200, json=[{"id": 42, "account": {"login": "acme"}}])
        if p == "/app/installations/42/access_tokens":
            assert auth.startswith("Bearer ")
            return httpx.Response(201, json={"token": "ghs_inst"})
        assert auth == "token ghs_inst", f"{p} used {auth}"
        if p == "/installation/repositories":
            return httpx.Response(200, json={"total_count": 1, "repositories": [
                {"id": 7, "name": "web", "full_name": "acme/web", "default_branch": "main"}]})
        if p == "/repos/acme/web/commits/main":
            return httpx.Response(200, json={"sha": "abc123"})
        if p == "/repos/acme/web/tarball/abc123":
            return httpx.Response(200, content=make_tarball(REPO_FILES),
                                  headers={"content-type": "application/x-gzip"})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture()
def gh(client):
    fake = FakeGitHub()
    app.dependency_overrides[get_github_client_factory] = lambda: (
        lambda: GitHubAppClient(1, PEM, transport=httpx.MockTransport(fake.handler)))
    return fake


def test_scanner_matches_whole_identifiers_and_skips_noise():
    res = scan_tarball(make_tarball(REPO_FILES), ["new_checkout", "promo_banner", "unused"])
    found = sorted((r.flag_name, r.path, r.line) for r in res.references)
    assert found == [("new_checkout", "src/checkout.ts", 1),
                     ("promo_banner", "src/banner.py", 1), ("promo_banner", "src/banner.py", 2)]
    assert res.files_scanned == 3


def test_app_jwt_is_valid_rs256():
    import jwt
    tok = GitHubAppClient(123, PEM).app_jwt()
    claims = jwt.decode(tok, KEY.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 600


def test_sync_scan_and_references(client, gh):
    client.post("/api/flags", json={"name": "new_checkout"})
    promo = client.post("/api/flags", json={"name": "promo_banner"}).json()

    r = client.post("/api/github/sync")
    assert r.status_code == 200, r.text
    assert r.json() == {"installations": 1, "repositories_added": 1, "repositories_updated": 0,
                        "repositories": ["acme/web"]}
    repo = client.get("/api/repositories").json()[0]
    assert repo["installation_id"] == "42"
    assert any(i["provider"] == "github" for i in client.get("/api/integrations").json())

    r = client.post(f"/api/repositories/{repo['id']}/scan")
    assert r.status_code == 200, r.text
    assert r.json()["references"] == 3 and r.json()["commit_sha"] == "abc123"
    assert r.json()["flags"] == [{"flag": "new_checkout", "references": 1},
                                 {"flag": "promo_banner", "references": 2}]

    refs = client.get(f"/api/flags/{promo['id']}/references").json()
    assert [x["line"] for x in refs] == [1, 2]
    assert refs[0]["url"] == "https://github.com/acme/web/blob/abc123/src/banner.py#L1"

    # Re-scan replaces old references instead of duplicating them; second sync updates.
    client.post(f"/api/repositories/{repo['id']}/scan")
    assert len(client.get(f"/api/flags/{promo['id']}/references").json()) == 2
    assert client.post("/api/github/sync").json()["repositories_updated"] == 1
    # The installation token was fetched once per client, not per request.
    assert sum(q.url.path.endswith("/access_tokens") for q in gh.requests) == 4


def test_scan_requires_app_connected_repo(client, gh):
    repo = client.post("/api/repositories", json={"full_name": "acme/manual"}).json()
    assert client.post(f"/api/repositories/{repo['id']}/scan").status_code == 400


def test_install_url(client, monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "github_app_slug", "flagpilot-hackathon")
    assert client.get("/api/github/install-url").json() == {
        "url": "https://github.com/apps/flagpilot-hackathon/installations/new"}
