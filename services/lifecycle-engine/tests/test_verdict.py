"""Flag verdict: active/inactive and useful/not useful."""

from __future__ import annotations

import pytest

from lifecycle_engine.agents.llm import LLMUnavailableError, MockLLMProvider
from lifecycle_engine.agents.verdict import assess_flag
from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.policies.stale_detector import StaleFlagDetector
from lifecycle_engine.schemas.flags import EnvironmentFlagState, FlagConfiguration
from lifecycle_engine.schemas.planning import LLMStatus
from lifecycle_engine.schemas.verdict import Activity as A, ExperimentStatusInput, Usefulness as U

from tests.builders import NOW, days_ago, sample_configs


@pytest.fixture(scope="module")
def ci():
    from tests.conftest import CHECKOUT, PRICING
    return CodeIntelligence.from_targets([CHECKOUT, PRICING])


def verdict(ci, name, config=None, **kw):
    cfg = config if config is not None else sample_configs().get(name)
    analysis = ci.analyze(name, cfg)
    stale = StaleFlagDetector().evaluate(name, config=cfg, analysis=analysis, now=NOW)
    return assess_flag(config=cfg, analysis=analysis, stale=stale, now=NOW, **kw)


@pytest.mark.parametrize("flag,activity,usefulness", [
    ("dark_mode", A.ACTIVE, U.USEFUL),                    # segment overrides in production
    ("recommendations_v2", A.ACTIVE, U.USEFUL),           # 30% split
    ("banner_text", A.ACTIVE, U.USEFUL),                  # tagged remote-config
    ("maintenance_mode", A.INACTIVE, U.USEFUL),           # kill switch: off and unreferenced, but meant to stay
    ("old_promo_banner", A.INACTIVE, U.NOT_USEFUL),       # no code, 0 evaluations, off
    ("new_checkout", A.ACTIVE, U.NOT_USEFUL),             # 100% on for months, owner says no longer needed
    ("legacy_search", A.ACTIVE, U.NOT_USEFUL),            # off everywhere since January, still evaluated by code
    ("free_shipping", A.ACTIVE, U.USEFUL),                # changed 7 days ago
])
def test_sample_flags(ci, flag, activity, usefulness):
    v = verdict(ci, flag)
    assert (v.activity, v.usefulness) == (activity, usefulness), [r.text for r in v.activity_reasons + v.usefulness_reasons]
    assert v.is_recommendation is True and 0.1 <= v.confidence <= 0.95
    ids = {e.id for e in v.evidence}
    for r in v.activity_reasons + v.usefulness_reasons:
        assert set(r.evidence_ids) <= ids, r


def test_recommendations_follow_the_verdict(ci):
    assert verdict(ci, "dark_mode").recommendation.startswith("Keep it")
    rec = verdict(ci, "new_checkout").recommendation
    assert "keeping the enabled path" in rec and "Don't archive" in rec
    assert "archive it in Flagsmith after the cleanup is merged" in verdict(ci, "old_promo_banner").recommendation


def test_running_experiment_makes_a_flag_active_and_useful(ci):
    v = verdict(ci, "new_checkout", experiments=[ExperimentStatusInput(name="checkout test", status="running")])
    assert (v.activity, v.usefulness) == (A.ACTIVE, U.USEFUL)
    assert any("experiment" in r.text for r in v.usefulness_reasons)


def test_archived_flag_still_in_code_is_not_useful(ci):
    cfg = sample_configs()["new_checkout"].model_copy(update={"is_archived": True, "owner_confirmation": None})
    v = verdict(ci, "new_checkout", cfg)
    assert v.activity == A.INACTIVE and v.usefulness == U.NOT_USEFUL
    assert any("archived" in r.text for r in v.usefulness_reasons)
    assert "archived flag" in v.recommendation


def test_without_configuration_it_is_uncertain(ci):
    analysis = ci.analyze("analytics_v2", None)
    stale = StaleFlagDetector().evaluate("analytics_v2", config=None, analysis=analysis, now=NOW)
    v = assess_flag(config=None, analysis=analysis, stale=stale, now=NOW)
    assert v.usefulness == U.UNCERTAIN and v.confidence <= 0.35
    assert any("no Flagsmith configuration" in c for c in v.caveats)


def test_dynamic_references_lower_confidence_of_not_useful(ci):
    v = verdict(ci, "new_checkout")
    assert any("dynamic flag reference" in c for c in v.caveats)
    assert v.confidence < 0.95


def test_uniform_but_recently_changed_is_still_useful(ci):
    cfg = FlagConfiguration(flag_name="new_checkout", project="p", created_at=days_ago(200), environments=[
        EnvironmentFlagState(environment="production", is_production=True, enabled=True, updated_at=days_ago(3))])
    v = verdict(ci, "new_checkout", cfg)
    assert v.usefulness == U.USEFUL and any("changed recently" in r.text for r in v.usefulness_reasons)


# --- AI explanation ------------------------------------------------------------------------

def test_llm_writes_the_explanation_but_not_the_labels(ci):
    good = {"reasoning_summary": "Everyone in production gets the new checkout, so the flag no longer changes anything.",
            "statements": [{"type": "recommendation", "text": "Ask the owner to confirm, then clean up.", "evidence_ids": []}]}
    v = verdict(ci, "new_checkout", llm=MockLLMProvider(response=good))
    assert v.llm.status == LLMStatus.USED and v.explanation == good["reasoning_summary"]
    assert (v.activity, v.usefulness) == (A.ACTIVE, U.NOT_USEFUL)


def test_invented_facts_are_rejected(ci):
    bad = {"reasoning_summary": "It is only used in src/payments/legacy.ts:12, so it is safe to delete.", "statements": []}
    v = verdict(ci, "new_checkout", llm=MockLLMProvider(response=bad))
    assert v.llm.status == LLMStatus.REJECTED_INVALID_OUTPUT and v.llm.validation_errors
    assert v.explanation.startswith("'new_checkout' is active")


def test_llm_outage_falls_back(ci):
    class Down:
        name, model = "down", None

        def complete_json(self, **_):
            raise LLMUnavailableError("network down")

    v = verdict(ci, "dark_mode", llm=Down())
    assert v.llm.status == LLMStatus.UNAVAILABLE and v.llm.error == "network down"
    assert v.explanation and v.usefulness == U.USEFUL


def test_default_mock_narrative_passes_validation(ci):
    v = verdict(ci, "dark_mode", llm=MockLLMProvider())
    assert v.llm.status == LLMStatus.USED, v.llm.validation_errors
