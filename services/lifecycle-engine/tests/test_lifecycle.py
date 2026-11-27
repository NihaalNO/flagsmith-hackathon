from __future__ import annotations

import pytest

from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.schemas.lifecycle import (
    ApprovalRole as R,
    EvidenceKind as E,
    FlagLifecycle,
    LifecycleState as S,
)
from lifecycle_engine.state_machine.evaluator import evaluate_lifecycle
from lifecycle_engine.state_machine.machine import (
    InvalidTransitionError,
    LifecycleStateMachine,
    SafetyViolationError,
    TransitionRequirementsError,
)

from tests.builders import NOW, approval, ev, released_config, sample_configs

M = LifecycleStateMachine()


def lc(state: S = S.CREATED) -> FlagLifecycle:
    return FlagLifecycle(flag_id="p:f", flag_name="f", state=state, created_at=NOW, updated_at=NOW)


def test_full_happy_path_records_history():
    x = lc()
    x = M.transition(x, S.IN_DEVELOPMENT, actor="dev", reason="code merged", evidence=[ev(E.CODE_REFERENCES_PRESENT, "code_scan")], at=NOW)
    x = M.transition(x, S.TESTING, actor="dev", reason="on in staging", evidence=[ev(E.ENABLED_IN_NON_PRODUCTION, "flagsmith")], at=NOW)
    x = M.transition(x, S.ROLLING_OUT, actor="dev", reason="10% prod", evidence=[ev(E.PRODUCTION_ROLLOUT_STARTED, "flagsmith")], at=NOW)
    x = M.transition(x, S.FULLY_RELEASED, actor="pm", reason="done", at=NOW,
                     evidence=[ev(E.FULL_PRODUCTION_EXPOSURE, "flagsmith"), ev(E.ROLLOUT_STABLE, "flagsmith")],
                     approvals=[approval(R.FLAG_OWNER)])
    x = M.transition(x, S.READY_FOR_CLEANUP, actor="pm", reason="stale", at=NOW,
                     evidence=[ev(E.STALE_ASSESSMENT, "stale_detector", "stale_1"), ev(E.OWNER_CONFIRMED_NOT_NEEDED, "owner")],
                     approvals=[approval(R.FLAG_OWNER)])
    x = M.transition(x, S.CLEANUP_IN_PROGRESS, actor="pm", reason="plan ok", at=NOW,
                     evidence=[ev(E.CLEANUP_PLAN_APPROVED, "owner", "plan_1")], approvals=[approval(R.FLAG_OWNER)])
    x = M.transition(x, S.RETIRED, actor="pm", reason="merged", at=NOW,
                     evidence=[ev(E.CLEANUP_VERIFIED, "verification", "verify_1"), ev(E.CLEANUP_MERGED, "github", "https://github.com/acme/x/pull/1"),
                               ev(E.NO_REMAINING_REFERENCES, "code_scan")],
                     approvals=[approval(R.FLAG_OWNER), approval(R.CODE_REVIEWER, "bob")])
    assert x.state == S.RETIRED
    assert [h.to_state for h in x.history] == [S.IN_DEVELOPMENT, S.TESTING, S.ROLLING_OUT, S.FULLY_RELEASED,
                                               S.READY_FOR_CLEANUP, S.CLEANUP_IN_PROGRESS, S.RETIRED]
    last = x.history[-1]
    assert last.from_state == S.CLEANUP_IN_PROGRESS and last.actor == "pm" and last.reason == "merged" and last.at == NOW
    assert {e.kind for e in last.evidence} == {E.CLEANUP_VERIFIED, E.CLEANUP_MERGED, E.NO_REMAINING_REFERENCES}
    assert len({h.id for h in x.history}) == 7


def test_transition_is_pure():
    before = lc()
    after = M.transition(before, S.IN_DEVELOPMENT, actor="d", reason="r", evidence=[ev(E.MANUAL_NOTE)])
    assert before.state == S.CREATED and before.history == []
    assert after.state == S.IN_DEVELOPMENT


@pytest.mark.parametrize("start,target", [
    (S.CREATED, S.RETIRED), (S.CREATED, S.FULLY_RELEASED), (S.TESTING, S.READY_FOR_CLEANUP),
    (S.FULLY_RELEASED, S.RETIRED), (S.RETIRED, S.CREATED), (S.ROLLING_OUT, S.CLEANUP_IN_PROGRESS),
])
def test_invalid_transitions_rejected(start, target):
    with pytest.raises(InvalidTransitionError) as exc:
        M.transition(lc(start), target, actor="a", reason="r", evidence=[ev(k) for k in E])
    assert exc.value.code == "invalid_transition"


def test_missing_evidence_and_approval_rejected():
    with pytest.raises(TransitionRequirementsError) as exc:
        M.transition(lc(S.ROLLING_OUT), S.FULLY_RELEASED, actor="a", reason="r", evidence=[ev(E.FULL_PRODUCTION_EXPOSURE)])
    assert exc.value.details["missing_evidence"] == ["rollout_stable"]
    assert exc.value.details["missing_approvals"] == ["flag_owner"]


def test_100_percent_rollout_alone_cannot_mark_fully_released():
    with pytest.raises(TransitionRequirementsError):
        M.transition(lc(S.ROLLING_OUT), S.FULLY_RELEASED, actor="a", reason="100%", evidence=[ev(E.FULL_PRODUCTION_EXPOSURE, "flagsmith")])


def test_reason_required():
    with pytest.raises(TransitionRequirementsError):
        M.transition(lc(), S.IN_DEVELOPMENT, actor="a", reason="  ", evidence=[ev(E.MANUAL_NOTE)])


# --- mandatory safety: cannot archive before merged + verified + approved --------

RETIRE_EVIDENCE = [ev(E.CLEANUP_VERIFIED, "verification", "verify_1"), ev(E.CLEANUP_MERGED, "github", "pr/1"), ev(E.NO_REMAINING_REFERENCES, "code_scan")]
RETIRE_APPROVALS = [approval(R.FLAG_OWNER), approval(R.CODE_REVIEWER, "bob")]


@pytest.mark.parametrize("drop", [E.CLEANUP_VERIFIED, E.CLEANUP_MERGED, E.NO_REMAINING_REFERENCES])
def test_cannot_retire_without_each_required_evidence(drop):
    evidence = [e for e in RETIRE_EVIDENCE if e.kind != drop]
    with pytest.raises(TransitionRequirementsError):
        M.transition(lc(S.CLEANUP_IN_PROGRESS), S.RETIRED, actor="a", reason="r", evidence=evidence, approvals=RETIRE_APPROVALS)


@pytest.mark.parametrize("drop", [R.FLAG_OWNER, R.CODE_REVIEWER])
def test_cannot_retire_without_each_approval(drop):
    with pytest.raises(TransitionRequirementsError):
        M.transition(lc(S.CLEANUP_IN_PROGRESS), S.RETIRED, actor="a", reason="r", evidence=RETIRE_EVIDENCE,
                     approvals=[a for a in RETIRE_APPROVALS if a.role != drop])


def test_verification_and_merge_evidence_must_reference_artifacts():
    bad = [ev(E.CLEANUP_VERIFIED, "verification"), ev(E.CLEANUP_MERGED, "manual", "pr/1"), ev(E.NO_REMAINING_REFERENCES, "code_scan")]
    with pytest.raises(TransitionRequirementsError) as exc:
        M.transition(lc(S.CLEANUP_IN_PROGRESS), S.RETIRED, actor="a", reason="r", evidence=bad, approvals=RETIRE_APPROVALS)
    assert len(exc.value.details["problems"]) == 2


def test_zero_references_alone_cannot_retire():
    with pytest.raises((TransitionRequirementsError, InvalidTransitionError)):
        M.transition(lc(S.CLEANUP_IN_PROGRESS), S.RETIRED, actor="a", reason="no refs",
                     evidence=[ev(E.NO_REMAINING_REFERENCES, "code_scan")], approvals=RETIRE_APPROVALS)
    for start in S:
        if start != S.CLEANUP_IN_PROGRESS:
            with pytest.raises(InvalidTransitionError):
                M.transition(lc(start), S.RETIRED, actor="a", reason="no refs", evidence=[ev(E.NO_REMAINING_REFERENCES)])


# --- overrides ---------------------------------------------------------------------

def test_override_requires_approval_and_is_audited():
    with pytest.raises(TransitionRequirementsError):
        M.override(lc(), S.FULLY_RELEASED, actor="a", justification="imported legacy flag", approvals=[])
    x = M.override(lc(), S.FULLY_RELEASED, actor="admin", justification="imported legacy flag",
                   approvals=[approval(R.FLAG_OWNER)], at=NOW)
    assert x.state == S.FULLY_RELEASED
    rec = x.history[-1]
    assert rec.is_manual_override and rec.override_justification == "imported legacy flag" and rec.actor == "admin"


@pytest.mark.parametrize("target", [S.RETIRED, S.CLEANUP_IN_PROGRESS])
def test_override_cannot_bypass_cleanup_safety(target):
    with pytest.raises(SafetyViolationError):
        M.override(lc(S.FULLY_RELEASED), target, actor="admin", justification="just do it",
                   approvals=[approval(R.FLAG_OWNER), approval(R.RELEASE_MANAGER)])


def test_override_to_retired_with_full_requirements_is_allowed():
    x = M.override(lc(S.READY_FOR_CLEANUP), S.RETIRED, actor="admin", justification="cleanup merged outside FlagPilot",
                   approvals=RETIRE_APPROVALS, evidence=RETIRE_EVIDENCE)
    assert x.state == S.RETIRED and x.history[-1].is_manual_override


def test_rollback_requires_reason_evidence():
    with pytest.raises(TransitionRequirementsError):
        M.transition(lc(S.ROLLING_OUT), S.TESTING, actor="a", reason="incident")
    x = M.transition(lc(S.ROLLING_OUT), S.TESTING, actor="a", reason="incident", evidence=[ev(E.ROLLBACK_REASON)])
    assert x.state == S.TESTING


# --- evaluation --------------------------------------------------------------------

def test_evaluation_is_only_a_recommendation(sample_targets):
    cfg = sample_configs()["new_checkout"]
    analysis = CodeIntelligence.from_targets(sample_targets).analyze("new_checkout", cfg)
    record = lc(S.ROLLING_OUT)
    result = evaluate_lifecycle("new_checkout", lifecycle=record, config=cfg, analysis=analysis, now=NOW)
    assert result.is_recommendation is True
    assert result.current_state == S.ROLLING_OUT
    assert result.suggested_state == S.FULLY_RELEASED
    assert any("does not prove" in w for w in result.warnings)
    to_released = next(t for t in result.transitions if t.to_state == S.FULLY_RELEASED)
    assert not to_released.allowed and to_released.missing_approvals == [R.FLAG_OWNER]
    assert record.state == S.ROLLING_OUT  # nothing applied


def test_evaluation_never_suggests_retired_or_cleanup_in_progress():
    cfg = released_config(is_archived=True)
    result = evaluate_lifecycle("f", lifecycle=lc(S.READY_FOR_CLEANUP), config=cfg, analysis=None, now=NOW)
    assert result.suggested_state == S.READY_FOR_CLEANUP
    assert any("never infers 'retired'" in w for w in result.warnings)


def test_evaluation_partial_rollout(sample_targets):
    cfg = sample_configs()["dark_mode"]
    r = evaluate_lifecycle("dark_mode", lifecycle=None, config=cfg, analysis=None, now=NOW)
    assert r.suggested_state == S.ROLLING_OUT and r.current_state == S.CREATED
    assert any("no lifecycle record" in w for w in r.warnings)
