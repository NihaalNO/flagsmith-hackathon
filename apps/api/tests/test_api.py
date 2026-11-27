def make_flag(client, name="new_checkout", **extra):
    r = client.post("/api/flags", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert {k: body[k] for k in ("status", "service", "database")} == {
        "status": "ok", "service": "FlagPilot API", "database": "ok"}
    assert "engine" in body and "llm" in body  # names/versions only
    assert "key" not in str(body).lower()


def test_create_list_get_flag(client):
    flag = make_flag(client, owner="payments-team")
    assert flag["lifecycle_status"] == "created"
    assert flag["enabled"] is False
    assert [f["id"] for f in client.get("/api/flags").json()] == [flag["id"]]
    assert client.get(f"/api/flags/{flag['id']}").json()["owner"] == "payments-team"


def test_duplicate_flag_name_conflicts(client):
    make_flag(client)
    assert client.post("/api/flags", json={"name": "new_checkout"}).status_code == 409


def test_missing_flag_404(client):
    assert client.get("/api/flags/nope").status_code == 404


def test_update_flag(client):
    flag = make_flag(client)
    r = client.patch(f"/api/flags/{flag['id']}", json={"enabled": True})
    assert r.json()["enabled"] is True


def test_lifecycle_valid_and_invalid(client):
    flag = make_flag(client)
    r = client.post(f"/api/flags/{flag['id']}/lifecycle", json={"status": "in_development"})
    assert r.status_code == 200 and r.json()["lifecycle_status"] == "in_development"

    r = client.post(f"/api/flags/{flag['id']}/lifecycle", json={"status": "fully_released"})
    assert r.status_code == 422

    r = client.get("/api/flags", params={"status": "in_development"})
    assert len(r.json()) == 1


def test_transitions_endpoint(client):
    t = client.get("/api/flags/lifecycle/transitions").json()
    assert t["retired"] == []
    assert "in_development" in t["created"]


def test_repositories(client):
    r = client.post("/api/repositories", json={"full_name": "acme/web-app"})
    assert r.status_code == 201 and r.json()["name"] == "web-app"
    assert client.post("/api/repositories", json={"full_name": "acme/web-app"}).status_code == 409
    assert client.post("/api/repositories", json={"full_name": "not a repo"}).status_code == 422


def test_integrations(client):
    r = client.post("/api/integrations", json={"provider": "flagsmith", "name": "Flagsmith prod"})
    assert r.status_code == 201 and r.json()["status"] == "pending"
    assert client.post("/api/integrations", json={"provider": "jira", "name": "x"}).status_code == 422


def test_audit_trail(client):
    flag = make_flag(client)
    client.post(f"/api/flags/{flag['id']}/lifecycle", json={"status": "in_development"})
    client.delete(f"/api/flags/{flag['id']}")
    actions = [a["action"] for a in client.get("/api/audit-logs", params={"resource_id": flag["id"]}).json()]
    assert sorted(actions) == ["flag.created", "flag.deleted", "flag.lifecycle_changed"]
