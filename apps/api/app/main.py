import json
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_workspace
from app.api.routes import analysis, audit_logs, experiments, flags, github, integrations, repositories, workspaces
from app.core.auth import CurrentUser, get_current_user
from app.core.config import settings
from sqlalchemy.orm import Session

from app.core.database import get_db

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
# Every /api route except /api/health requires a valid Supabase Auth token, and all
# data routes run inside one workspace (isolation enforced in core/tenancy.py).
app.include_router(workspaces.router, prefix="/api", dependencies=[Depends(get_current_user)])
for module in (flags, analysis, repositories, integrations, github, audit_logs):
    app.include_router(module.router, prefix="/api",
                       dependencies=[Depends(get_current_user), Depends(get_workspace)])
app.include_router(analysis.verdict_router, prefix="/api", dependencies=[Depends(get_current_user), Depends(get_workspace)])
app.include_router(experiments.router, prefix="/api", dependencies=[Depends(get_current_user), Depends(get_workspace)])
# Event ingestion is called by the customer's application with the experiment's own key,
# not a user token; it resolves the experiment (and its workspace) from that key.
app.include_router(experiments.events_router, prefix="/api")


@app.get("/api/health", tags=["Health"])
def health(session: Session = Depends(get_db)):
    """Public liveness check, including database reachability."""
    try:
        session.execute(text("select 1"))
        db = "ok"
    except Exception:
        db = "unreachable"
    # Lifecycle engine and LLM: versions/provider names only, never keys. Not part of the
    # status: the API works without them (analysis endpoints answer 503).
    from app.services.engine import EngineUnavailable, engine_version, llm_provider
    try:
        engine = engine_version()
    except EngineUnavailable:
        engine = "unavailable"
    try:
        provider = llm_provider()
        llm = f"{provider.name}:{provider.model}" if provider else "none"
    except EngineUnavailable:
        llm = "misconfigured" if engine != "unavailable" else "unavailable"
    return JSONResponse(
        {"status": "ok" if db == "ok" else "degraded", "service": settings.app_name, "database": db,
         "engine": engine, "llm": llm},
        status_code=200 if db == "ok" else 503,
    )


@app.get("/api/me", tags=["Auth"])
def me(user: CurrentUser = Depends(get_current_user)):
    """The signed-in user, as seen by the API."""
    return {"id": user.id, "email": user.email, "role": user.role,
            "github_login": user.github_login, "github_id": user.github_id}


_DEV_LOGIN = (Path(__file__).parent / "static" / "dev_login.html").read_text()


@app.get("/dev/login", include_in_schema=False, response_class=HTMLResponse)
def dev_login():
    """Test page: sign in with GitHub via Supabase and copy an access token for /docs."""
    return (_DEV_LOGIN
            .replace('"__SUPABASE_URL__"', json.dumps(settings.supabase_url))
            .replace('"__SUPABASE_KEY__"', json.dumps(settings.supabase_publishable_key)))
