"""Standalone dev server for the engine (the shared backend in apps/api is Member 1's).

    uvicorn lifecycle_engine.api.app:app --reload

FLAGPILOT_ALLOWED_ROOTS (os.pathsep-separated) restricts repository paths;
FLAGPILOT_LLM_PROVIDER=mock|anthropic enables the planner's narrative provider.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from ..agents.llm import provider_from_env
from .router import create_router
from .service import EngineService, EngineSettings


def create_app() -> FastAPI:
    roots = [Path(p) for p in os.environ.get("FLAGPILOT_ALLOWED_ROOTS", "").split(os.pathsep) if p]
    service = EngineService(EngineSettings(allowed_roots=roots), llm=provider_from_env())
    app = FastAPI(title="FlagPilot Lifecycle Engine (dev)", version="0.1.0")
    app.include_router(create_router(service), prefix="/api/v1/engine", tags=["lifecycle-engine"])
    return app


app = create_app()
