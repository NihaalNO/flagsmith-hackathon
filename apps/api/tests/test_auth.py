import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import auth

PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": PRIVATE_KEY.public_key()})()


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", FakeJWKS())


def token(key=PRIVATE_KEY, **overrides):
    claims = {"sub": "gh-user-1", "email": "dev@example.com", "role": "authenticated",
              "aud": "authenticated", "iss": auth.ISSUER, "exp": int(time.time()) + 600, **overrides}
    return jwt.encode(claims, key, algorithm="ES256")


def get(client, path, tok=None):
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.get(path, headers=headers)


def test_health_is_public(anon_client):
    assert anon_client.get("/api/health").status_code == 200


def test_missing_token_rejected(anon_client):
    r = get(anon_client, "/api/flags")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("bad", [
    lambda: token(key=OTHER_KEY),                          # wrong signature
    lambda: token(exp=int(time.time()) - 10),              # expired
    lambda: token(aud="anon"),                             # wrong audience
    lambda: token(iss="https://evil.supabase.co/auth/v1"), # other project
    lambda: "not-a-jwt",
])
def test_bad_tokens_rejected(anon_client, bad):
    assert get(anon_client, "/api/flags", bad()).status_code == 401


def test_valid_token_and_me(anon_client):
    tok = token()
    assert get(anon_client, "/api/flags", tok).status_code == 200
    assert get(anon_client, "/api/me", tok).json() == {
        "id": "gh-user-1", "email": "dev@example.com", "role": "authenticated"}


def test_audit_records_actor(anon_client):
    h = {"Authorization": f"Bearer {token()}"}
    anon_client.post("/api/flags", json={"name": "x"}, headers=h)
    entries = anon_client.get("/api/audit-logs", headers=h).json()
    assert entries[0]["actor"] == "dev@example.com"
