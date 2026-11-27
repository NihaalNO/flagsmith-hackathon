"""Supabase Auth: verify the access token the frontend gets from supabase-js.

Clients send `Authorization: Bearer <access_token>`. Tokens are verified with the
project's public signing keys (JWKS), so no shared secret is stored here.
"""

from contextvars import ContextVar
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

ISSUER = f"{settings.supabase_url.rstrip('/')}/auth/v1"
AUDIENCE = "authenticated"

_jwks = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
_bearer = HTTPBearer(auto_error=False, description="Supabase Auth access token")

# Who is making the current request; read by the audit service.
current_actor: ContextVar[str | None] = ContextVar("current_actor", default=None)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    role: str | None


def decode_token(token: str) -> dict:
    signing_key = _jwks.get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        signing_key,
        algorithms=["ES256", "RS256"],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "sub", "aud", "iss"]},
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    # Async on purpose: a ContextVar set here is copied into the threadpool that runs
    # sync endpoints, so audit entries see the actor. (Set inside a sync dependency,
    # it would be lost.) The JWKS fetch can block, so it runs in a thread.
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("Missing bearer token")
    try:
        claims = await run_in_threadpool(decode_token, creds.credentials)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired")
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise _unauthorized("Invalid token")
    user = CurrentUser(id=claims["sub"], email=claims.get("email"), role=claims.get("role"))
    current_actor.set(user.email or user.id)
    return user
