from __future__ import annotations

import pytest

from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.policies.stale_detector import StaleFlagDetector
from lifecycle_engine.schemas.discovery import RepositoryTarget
from lifecycle_engine.schemas.flags import (
    ConsumerCoverage,
    DynamicReferenceReview,
    EnvironmentFlagState,
    FlagPurpose,
    OwnerConfirmation,
)
from lifecycle_engine.schemas.lifecycle import EvidenceKind as E, FlagLifecycle, LifecycleState as S, TransitionRecord
from lifecycle_engine.schemas.stale import CleanupDirection, StalePolicy, StaleStatus

from tests.builders import NOW, days_ago, ev, released_config, sample_configs

USES_FLAG = {"a.ts": """
    import f from "flagsmith";
    export function g() { if (f.hasFeature("f")) { a(); } else { b(); } }
"""}
FULL = ConsumerCoverage(all_consumers_scanned=True)


@pytest.fixture
def analysis(make_repo):
    def _a(files=USES_FLAG, flag="f"):
        root = make_repo(files)
        return CodeIntelligence.from_targets([RepositoryTarget(repository="t/r", root_path=str(root))]).analyze(flag)

    return _a


D = StaleFlagDetector()


def test_ready_for_cleanup_with_complete_evidence(analysis):
    f = D.evaluate("f", config=released_config(), analysis=analysis(), coverage=FULL, now=NOW)
    assert f.status == StaleStatus.READY_FOR_CLEANUP
    assert f.cleanup_direction == CleanupDirection.KEEP_ENABLED_PATH
    assert f.requires_human_decision is True
    assert not f.blockers and not [m for m in f.missing_evidence if "ready_for_cleanup" in m.blocks]
    kinds = {e.kind for e in f.evidence}
    assert {"flag_age", "production_state", "config_stability", "owner_confirmation", "code_reference_count"} <= kinds


def test_disabled_flag_keeps_disabled_path(analysis):
    f = D.evaluate("f", config=released_config(enabled=False), analysis=analysis(), coverage=FULL, now=NOW)
    assert f.cleanup_direction == CleanupDirection.KEEP_DISABLED_PATH


# --- mandatory safety tests --------------------------------------------------------

def test_zero_references_alone_is_never_safe(analysis):
    a = analysis({"other.ts": "export const x = 1;\n"})
    cfg = released_config(owner_says_done=False, evaluations=None)
    f = D.evaluate("f", config=cfg, analysis=a, now=NOW)
    assert f.status not in (StaleStatus.READY_FOR_CLEANUP, StaleStatus.SAFE_TO_RETIRE)
    missing = {m.kind for m in f.missing_evidence}
    assert {"consumer_coverage", "evaluation_activity", "owner_confirmation"} <= missing
    # even with owner sign-off, zero refs + unconfirmed coverage is not enough
    f2 = D.evaluate("f", config=released_config(evaluations=0), analysis=a, now=NOW)
    assert f2.status == StaleStatus.POTENTIALLY_STALE and f2.cleanup_direction == CleanupDirection.ARCHIVE_ONLY


def test_zero_references_with_live_evaluations_points_to_unscanned_consumer(analysis):
    a = analysis({"other.ts": "export const x = 1;\n"})
    f = D.evaluate("f", config=released_config(evaluations=500), analysis=a, coverage=FULL, now=NOW)
    assert f.status == StaleStatus.POTENTIALLY_STALE
    assert any(b.kind == "unscanned_consumer_activity" for b in f.blockers)


def test_100_percent_rollout_alone_is_not_ready(analysis):
    cfg = released_config(owner_says_done=False)
    f = D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW)
    assert f.status != StaleStatus.READY_FOR_CLEANUP
    assert any(m.kind == "owner_confirmation" for m in f.missing_evidence)
    assert any("does not prove" in e.description for e in f.evidence if e.kind == "production_state")


def test_unresolved_dynamic_reference_prevents_unconditional_recommendation(analysis):
    files = {**USES_FLAG, "dyn.ts": 'import f from "flagsmith";\nexport const on = (n: string) => f.hasFeature(n);\n'}
    a = analysis(files)
    f = D.evaluate("f", config=released_config(), analysis=a, coverage=FULL, now=NOW)
    assert f.status == StaleStatus.POTENTIALLY_STALE
    [b] = [b for b in f.blockers if b.kind == "dynamic_references"]
    assert "dyn.ts:2" in b.description

    review = DynamicReferenceReview(reference_id=a.unresolved_dynamic_references[0].id, reviewed_by="lead",
                                    reviewed_at=NOW, excluded_flags=["f"])
    f2 = D.evaluate("f", config=released_config(), analysis=a,
                    coverage=ConsumerCoverage(all_consumers_scanned=True, dynamic_reference_reviews=[review]), now=NOW)
    assert f2.status == StaleStatus.READY_FOR_CLEANUP
    assert any(e.kind == "dynamic_reference_review" for e in f2.evidence)


def test_safe_to_retire_requires_verified_and_merged_cleanup(analysis):
    a = analysis({"other.ts": "export const x = 1;\n"})
    cfg = released_config(evaluations=0)
    assert D.evaluate("f", config=cfg, analysis=a, coverage=FULL, now=NOW).status == StaleStatus.READY_FOR_CLEANUP
    rec = TransitionRecord(id="t", flag_id="p:f", from_state=S.READY_FOR_CLEANUP, to_state=S.CLEANUP_IN_PROGRESS, at=NOW,
                           actor="a", reason="r", evidence=[ev(E.CLEANUP_VERIFIED, "verification", "v1"),
                                                            ev(E.CLEANUP_MERGED, "github", "pr/1")])
    lifecycle = FlagLifecycle(flag_id="p:f", flag_name="f", state=S.CLEANUP_IN_PROGRESS, history=[rec])
    assert D.evaluate("f", config=cfg, analysis=a, coverage=FULL, lifecycle=lifecycle, now=NOW).status == StaleStatus.SAFE_TO_RETIRE
    # references still present -> never safe to retire
    assert D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, lifecycle=lifecycle, now=NOW).status != StaleStatus.SAFE_TO_RETIRE


# --- other policy behaviour ------------------------------------------------------------

def test_old_flag_with_active_targeting_is_not_stale(analysis):
    cfg = released_config()
    cfg.environments[1] = EnvironmentFlagState(environment="production", is_production=True, enabled=True,
                                               segment_overrides=3, updated_at=days_ago(300))
    f = D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW)
    assert f.status == StaleStatus.NOT_STALE
    assert any(e.kind == "production_targeting" and e.direction.value == "opposes" for e in f.evidence)


def test_long_lived_purpose_and_tags_are_not_stale(analysis):
    cfg = released_config(tags=["kill-switch"])
    assert D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW).status == StaleStatus.NOT_STALE
    cfg2 = released_config()
    cfg2.owner_confirmation = OwnerConfirmation(confirmed_by="o", confirmed_at=NOW, purpose=FlagPurpose.OPS_KILL_SWITCH)
    assert D.evaluate("f", config=cfg2, analysis=analysis(), coverage=FULL, now=NOW).status == StaleStatus.NOT_STALE


def test_young_flag_and_recent_change(analysis):
    young = released_config(created_at=days_ago(5))
    assert D.evaluate("f", config=young, analysis=analysis(), coverage=FULL, now=NOW).status == StaleStatus.NOT_STALE
    recent = released_config()
    recent.environments[1].updated_at = days_ago(2)
    f = D.evaluate("f", config=recent, analysis=analysis(), coverage=FULL, now=NOW)
    assert f.status != StaleStatus.READY_FOR_CLEANUP
    assert any(e.kind == "config_stability" and e.direction.value == "opposes" for e in f.evidence)


def test_age_threshold_is_configurable_not_definitive(analysis):
    cfg = released_config(created_at=days_ago(60))
    strict = StaleFlagDetector(StalePolicy(id="strict", min_age_days=90))
    assert strict.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW).status == StaleStatus.NOT_STALE
    assert D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW).status == StaleStatus.READY_FOR_CLEANUP


def test_missing_configuration_is_insufficient_evidence(analysis):
    f = D.evaluate("f", config=None, analysis=analysis(), now=NOW)
    assert f.status == StaleStatus.INSUFFICIENT_EVIDENCE and f.confidence <= 0.2


def test_contradictory_environments_reduce_support(analysis):
    cfg = released_config()
    cfg.environments[0] = EnvironmentFlagState(environment="staging", enabled=False, updated_at=days_ago(150))
    f = D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW)
    agree = next(e for e in f.evidence if e.kind == "environments_agree")
    assert agree.direction.value == "opposes"


def test_contradictory_production_environments_not_stale(analysis):
    cfg = released_config()
    cfg.environments.append(EnvironmentFlagState(environment="production-eu", is_production=True, enabled=False, updated_at=days_ago(90)))
    f = D.evaluate("f", config=cfg, analysis=analysis(), coverage=FULL, now=NOW)
    assert f.status == StaleStatus.NOT_STALE


def test_value_flag_direction_is_inline(sample_targets):
    cfg = sample_configs()["shipping_rate"]
    a = CodeIntelligence.from_targets(sample_targets).analyze("shipping_rate", cfg)
    f = D.evaluate("shipping_rate", config=cfg, analysis=a, now=NOW)
    assert f.cleanup_direction == CleanupDirection.INLINE_VALUE and f.inline_value == 4.99
    assert any(m.kind == "ownership" for m in f.missing_evidence)


def test_sample_new_checkout_held_by_dynamic_reference(sample_targets):
    cfg = sample_configs()["new_checkout"]
    a = CodeIntelligence.from_targets(sample_targets).analyze("new_checkout", cfg)
    f = D.evaluate("new_checkout", config=cfg, analysis=a, now=NOW)
    assert f.status == StaleStatus.POTENTIALLY_STALE
    assert [b.kind for b in f.blockers] == ["dynamic_references"]
