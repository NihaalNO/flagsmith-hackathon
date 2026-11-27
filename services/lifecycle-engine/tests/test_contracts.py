"""Mock files for the frontend must stay valid against the engine's schemas."""

from __future__ import annotations

import json

import pytest

from lifecycle_engine.cli import main
from lifecycle_engine.schemas.analysis import FlagAnalysisReport
from lifecycle_engine.schemas.api import CleanupPlanResponse, StaleDetectionResponse
from lifecycle_engine.schemas.common import ErrorResponse
from lifecycle_engine.schemas.discovery import ScanResult
from lifecycle_engine.schemas.lifecycle import LifecycleEvaluation
from lifecycle_engine.schemas.patch import CleanupPatch
from lifecycle_engine.schemas.verification import VerificationResult

from tests.conftest import ENGINE_ROOT

MOCKS = {
    "discover.json": ScanResult,
    "analysis_new_checkout.json": FlagAnalysisReport,
    "lifecycle_evaluation_new_checkout.json": LifecycleEvaluation,
    "stale_findings.json": StaleDetectionResponse,
    "cleanup_plan_new_checkout.json": CleanupPlanResponse,
    "cleanup_plan_legacy_search.json": CleanupPlanResponse,
    "patch_new_checkout.json": CleanupPatch,
    "verification_ready_new_checkout.json": VerificationResult,
    "verification_incomplete_legacy_search.json": VerificationResult,
}


@pytest.mark.parametrize("name,model", MOCKS.items())
def test_mock_matches_schema(name, model):
    data = json.loads((ENGINE_ROOT / "mocks" / name).read_text(encoding="utf-8"))
    model.model_validate(data)


def test_error_mock_matches_schema():
    data = json.loads((ENGINE_ROOT / "mocks" / "error_transition_requirements.json").read_text(encoding="utf-8"))
    data.pop("http_status")
    ErrorResponse.model_validate(data)


def test_cli_scan(capsys):
    assert main(["scan", str(ENGINE_ROOT / "sample_repos" / "pricing-service"), "--repo", "acme/pricing-service"]) == 0
    out = ScanResult.model_validate_json(capsys.readouterr().out)
    assert out.repositories == ["acme/pricing-service"] and out.summary.confirmed == 3
