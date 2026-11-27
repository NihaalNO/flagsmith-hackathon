from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit_logs, flags, integrations, repositories
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
for module in (flags, repositories, integrations, audit_logs):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health", tags=["Health"])
def health():
    return {"status": "ok", "service": settings.app_name}
