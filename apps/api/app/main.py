import json
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit_logs, flags, integrations, repositories
from app.core.auth import CurrentUser, get_current_user
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Feature flag lifecycle management platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tables are managed by Alembic migrations (`alembic upgrade head`), not create_all().
# Every /api route except /api/health requires a valid Supabase Auth token.
for module in (flags, repositories, integrations, audit_logs):
    app.include_router(module.router, prefix="/api", dependencies=[Depends(get_current_user)])


@app.get("/api/health", tags=["Health"])
def health():
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/me", tags=["Auth"])
def me(user: CurrentUser = Depends(get_current_user)):
    """The signed-in user, as seen by the API."""
    return {"id": user.id, "email": user.email, "role": user.role}


_DEV_LOGIN = (Path(__file__).parent / "static" / "dev_login.html").read_text()


@app.get("/dev/login", include_in_schema=False, response_class=HTMLResponse)
def dev_login():
    """Test page: sign in with GitHub via Supabase and copy an access token for /docs."""
    return (_DEV_LOGIN
            .replace('"__SUPABASE_URL__"', json.dumps(settings.supabase_url))
            .replace('"__SUPABASE_KEY__"', json.dumps(settings.supabase_publishable_key)))
