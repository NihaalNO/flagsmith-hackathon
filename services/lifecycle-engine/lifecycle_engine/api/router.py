"""FastAPI router for the engine. Member 1 mounts it in the shared backend:

    from lifecycle_engine.api.router import create_router
    app.include_router(create_router(service), prefix="/api/v1/engine", tags=["lifecycle-engine"])

Routes are a *proposal* pending agreement with Member 1 (see docs/api-contract.md).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..schemas.analysis import FlagAnalysisReport
from ..schemas.api import (
    AnalyzeFlagRequest,
    CleanupPlanRequest,
    CleanupPlanResponse,
    DetectStaleRequest,
    DiscoverRequest,
    EvaluateLifecycleRequest,
    GeneratePatchRequest,
    StaleDetectionResponse,
    TransitionRequest,
    VerifyCleanupRequest,
)
from ..schemas.common import ErrorResponse
from ..schemas.discovery import ScanResult
from ..schemas.lifecycle import FlagLifecycle, LifecycleEvaluation
from ..schemas.patch import CleanupPatch
from ..schemas.verification import VerificationResult
from .errors import to_error
from .service import EngineService

ERRORS = {code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 500)}


def _call(fn, req):
    try:
        return fn(req)
    except Exception as exc:  # map every failure to the shared error format
        status, body = to_error(exc)
        return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def create_router(service: EngineService | None = None) -> APIRouter:
    svc = service or EngineService()
    r = APIRouter()

    @r.post("/discover", response_model=ScanResult, responses=ERRORS, summary="Scan repositories for Flagsmith flag references")
    def discover(req: DiscoverRequest):
        return _call(svc.discover, req)

    @r.post("/flags/analyze", response_model=FlagAnalysisReport, responses=ERRORS, summary="Code intelligence report for one flag")
    def analyze(req: AnalyzeFlagRequest):
        return _call(svc.analyze_flag, req)

    @r.post("/lifecycle/evaluate", response_model=LifecycleEvaluation, responses=ERRORS, summary="Suggest a lifecycle state (never applied)")
    def evaluate(req: EvaluateLifecycleRequest):
        return _call(svc.evaluate_lifecycle, req)

    @r.post("/lifecycle/transition", response_model=FlagLifecycle, responses=ERRORS, summary="Apply an evidence-gated transition")
    def transition(req: TransitionRequest):
        return _call(svc.transition, req)

    @r.post("/stale/detect", response_model=StaleDetectionResponse, responses=ERRORS, summary="Evaluate flags against the stale policy")
    def stale(req: DetectStaleRequest):
        return _call(svc.detect_stale, req)

    @r.post("/cleanup/plan", response_model=CleanupPlanResponse, responses=ERRORS, summary="Evidence-backed cleanup plan")
    def plan(req: CleanupPlanRequest):
        return _call(svc.cleanup_plan, req)

    @r.post("/cleanup/patch", response_model=CleanupPatch, responses=ERRORS, summary="Suggested patch for review (not applied)")
    def patch(req: GeneratePatchRequest):
        return _call(svc.generate_patch, req)

    @r.post("/cleanup/verify", response_model=VerificationResult, responses=ERRORS, summary="Verify a patch in an isolated workspace")
    def verify(req: VerifyCleanupRequest):
        return _call(svc.verify_cleanup, req)

    return r
