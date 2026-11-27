from __future__ import annotations

import pytest

from lifecycle_engine.agents.llm import LLMUnavailableError, MockLLMProvider
from lifecycle_engine.agents.planner import CleanupPlanner
from lifecycle_engine.agents.validation import validate_narrative
from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.cleanup.patcher import PatchGenerator
from lifecycle_engine.discovery.scanner import CodebaseSnapshot
from lifecycle_engine.policies.stale_detector import StaleFlagDetector
from lifecycle_engine.schemas.flags import ConsumerCoverage, DynamicReferenceReview
from lifecycle_engine.schemas.lifecycle import ApprovalRole
from lifecycle_engine.schemas.planning import LLMStatus, RecommendedAction, RiskLevel, StatementType
from lifecycle_engine.schemas.stale import StaleStatus

from tests.builders import NOW, sample_configs


@pytest.fixture(scope="module")
def ctx():
    from tests.conftest import CHECKOUT, PRICING

    targets = [CHECKOUT, PRICING]
    cfg = sample_configs()["new_checkout"]
    analysis = CodeIntelligence.from_targets(targets).analyze("new_checkout", cfg)
    review = DynamicReferenceReview(reference_id=analysis.unresolved_dynamic_references[0].id, reviewed_by="lead",
                                    reviewed_at=NOW, excluded_flags=["new_checkout"])
    stale = StaleFlagDetector().evaluate("new_checkout", config=cfg, analysis=analysis, now=NOW,
                                         coverage=ConsumerCoverage(all_consumers_scanned=True, dynamic_reference_reviews=[review]))
    patch = PatchGenerator(targets).generate("new_checkout", stale.cleanup_direction)
    return {"targets": targets, "analysis": analysis, "stale": stale, "patch": patch}


def test_deterministic_plan_without_llm(ctx):
    plan = CleanupPlanner().plan(analysis=ctx["analysis"], stale=ctx["stale"], patch=ctx["patch"])
    assert plan.stale_status == StaleStatus.READY_FOR_CLEANUP
    assert plan.recommended_action == RecommendedAction.PREPARE_CLEANUP_PATCH
    assert plan.llm.status == LLMStatus.NOT_CONFIGURED
    assert plan.is_recommendation is True
    assert plan.patch_id == ctx["patch"].id
    assert set(plan.affected_files) == {"acme/checkout-web:src/checkout/CheckoutPage.tsx", "acme/checkout-web:src/lib/cart.ts",
                                        "acme/pricing-service:src/handlers.ts"}
    assert any("LegacyCheckout" in p for p in plan.obsolete_code_paths)
    assert plan.required_approvals == [ApprovalRole.FLAG_OWNER, ApprovalRole.CODE_REVIEWER]
    assert {v.kind for v in plan.verification_requirements} == {"tests", "static_check", "rescan", "manual_review"}
    ids = {e.id for e in plan.evidence}
    for s in plan.statements:
        assert set(s.evidence_ids) <= ids
        if s.type == StatementType.FACT:
            assert s.evidence_ids
    assert any("2 repositories" in r.description for r in plan.risk.items)


def test_mock_llm_output_is_used_when_valid(ctx):
    llm = MockLLMProvider()
    plan = CleanupPlanner(llm).plan(analysis=ctx["analysis"], stale=ctx["stale"], patch=ctx["patch"])
    assert plan.llm.status == LLMStatus.USED, plan.llm.validation_errors
    assert plan.reasoning_summary.startswith("Mock narrative")
    assert "Evidence packet" in llm.calls[0]["prompt"]
    # deterministic decisions are untouched by the LLM
    assert plan.recommended_action == RecommendedAction.PREPARE_CLEANUP_PATCH
    assert plan.proposed_changes == CleanupPlanner().plan(analysis=ctx["analysis"], stale=ctx["stale"], patch=ctx["patch"]).proposed_changes


@pytest.mark.parametrize("bad,needle", [
    ({"reasoning_summary": "Remove src/lib/payments.ts", "statements": []}, "src/lib/payments.ts"),
    ({"reasoning_summary": "See src/lib/cart.ts:999", "statements": []}, "cart.ts:999"),
    ({"reasoning_summary": "ok", "statements": [{"type": "fact", "text": "x", "evidence_ids": ["ref_invented"]}]}, "unknown evidence"),
    ({"reasoning_summary": "ok", "statements": [{"type": "fact", "text": "uncited", "evidence_ids": []}]}, "cites no evidence"),
    ({"reasoning_summary": "The flag is safe to delete.", "statements": []}, "claims safety"),
    ({"reasoning_summary": "All tests passed.", "statements": []}, "test results"),
    ({"reasoning_summary": "The flag has been archived.", "statements": []}, "lifecycle/repository state"),
    ({"reasoning_summary": "ok", "statements": [], "proposed_changes": [{"file": "x.ts"}]}, "schema"),
])
def test_llm_cannot_invent_paths_locations_or_claims(ctx, bad, needle):
    plan = CleanupPlanner(MockLLMProvider(response=bad)).plan(analysis=ctx["analysis"], stale=ctx["stale"], patch=ctx["patch"])
    assert plan.llm.status == LLMStatus.REJECTED_INVALID_OUTPUT
    assert any(needle in e for e in plan.llm.validation_errors), plan.llm.validation_errors
    # deterministic narrative used instead
    assert plan.reasoning_summary.startswith("'new_checkout' is assessed")


def test_negated_claims_are_allowed():
    n, errors = validate_narrative(
        {"reasoning_summary": "It is not safe to delete until the cleanup is merged; tests have not been run.", "statements": []},
        evidence_ids=set(), locations={}, stale_status=StaleStatus.READY_FOR_CLEANUP)
    assert errors == [] and n is not None


def test_known_file_and_line_are_allowed(ctx):
    ref = next(r for r in ctx["analysis"].references if r.location.file_path == "src/lib/cart.ts")
    plan = CleanupPlanner(MockLLMProvider(response={
        "reasoning_summary": f"The check at src/lib/cart.ts:{ref.location.line} gates the legacy total.",
        "statements": [{"type": "fact", "text": "cart.ts references the flag", "evidence_ids": [ref.id]}],
    })).plan(analysis=ctx["analysis"], stale=ctx["stale"], patch=ctx["patch"])
    assert plan.llm.status == LLMStatus.USED, plan.llm.validation_errors


class _Exploding:
    name, model = "boom", "x"

    def complete_json(self, **_):
        raise LLMUnavailableError("network down")


def test_unavailable_llm_does_not_break_planning_or_analysis(ctx):
    plan = CleanupPlanner(_Exploding()).plan(analysis=ctx["analysis"], stale=ctx["stale"], patch=ctx["patch"])
    assert plan.llm.status == LLMStatus.UNAVAILABLE and plan.llm.error == "network down"
    assert plan.recommended_action == RecommendedAction.PREPARE_CLEANUP_PATCH and plan.statements
    # deterministic discovery/analysis never touches the provider
    snap = CodebaseSnapshot(ctx["targets"])
    assert snap.scan.summary.confirmed > 0


def test_not_ready_flag_gets_no_patch_and_owner_review(sample_targets):
    cfg = sample_configs()["legacy_search"]
    a = CodeIntelligence.from_targets(sample_targets).analyze("legacy_search", cfg)
    stale = StaleFlagDetector().evaluate("legacy_search", config=cfg, analysis=a, now=NOW)
    plan = CleanupPlanner().plan(analysis=a, stale=stale)
    assert plan.recommended_action == RecommendedAction.REQUEST_OWNER_REVIEW
    assert plan.patch_id is None and plan.proposed_changes == []
    assert plan.risk.overall == RiskLevel.HIGH  # dynamic reference blocker
