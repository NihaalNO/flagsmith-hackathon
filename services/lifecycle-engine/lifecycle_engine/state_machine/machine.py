"""Lifecycle state machine with explicit, evidence-gated transitions.

The machine is pure: it takes a `FlagLifecycle` and returns a new one. Persistence
belongs to the backend. Every applied transition is appended to `history` with
timestamp, actor, reason, evidence and approvals.
"""

from __future__ import annotations

from datetime import datetime

from ..schemas.common import stable_id, utcnow
from ..schemas.lifecycle import (
    Approval,
    ApprovalRole,
    EvidenceKind as E,
    FlagLifecycle,
    LifecycleState as S,
    TransitionCheck,
    TransitionEvidence,
    TransitionRecord,
    TransitionRule,
)


class LifecycleError(Exception):
    code = "lifecycle_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class InvalidTransitionError(LifecycleError):
    code = "invalid_transition"


class TransitionRequirementsError(LifecycleError):
    code = "transition_requirements_not_met"


class SafetyViolationError(LifecycleError):
    code = "safety_violation"


_ROLLBACK = [E.ROLLBACK_REASON]

TRANSITION_RULES: list[TransitionRule] = [
    TransitionRule(from_state=S.CREATED, to_state=S.IN_DEVELOPMENT, any_of_evidence=[E.CODE_REFERENCES_PRESENT, E.MANUAL_NOTE],
                   description="Code using the flag exists, or a developer states work has started."),
    TransitionRule(from_state=S.IN_DEVELOPMENT, to_state=S.TESTING, required_evidence=[E.ENABLED_IN_NON_PRODUCTION],
                   description="Flag is enabled in at least one non-production environment."),
    TransitionRule(from_state=S.TESTING, to_state=S.ROLLING_OUT,
                   any_of_evidence=[E.PRODUCTION_ROLLOUT_STARTED, E.FULL_PRODUCTION_EXPOSURE],
                   description="Flag is exposed to some or all production users."),
    TransitionRule(from_state=S.ROLLING_OUT, to_state=S.FULLY_RELEASED,
                   required_evidence=[E.FULL_PRODUCTION_EXPOSURE, E.ROLLOUT_STABLE],
                   required_approvals=[ApprovalRole.FLAG_OWNER],
                   description="Uniform 100% production exposure, stable configuration, and the owner confirms the rollout is complete "
                               "(100% exposure alone does not prove completion)."),
    TransitionRule(from_state=S.FULLY_RELEASED, to_state=S.READY_FOR_CLEANUP,
                   required_evidence=[E.STALE_ASSESSMENT, E.OWNER_CONFIRMED_NOT_NEEDED],
                   required_approvals=[ApprovalRole.FLAG_OWNER],
                   description="Stale assessment recommends cleanup and the owner confirms the flag is no longer needed."),
    TransitionRule(from_state=S.READY_FOR_CLEANUP, to_state=S.CLEANUP_IN_PROGRESS,
                   required_evidence=[E.CLEANUP_PLAN_APPROVED], required_approvals=[ApprovalRole.FLAG_OWNER],
                   description="A cleanup plan was reviewed and approved."),
    TransitionRule(from_state=S.CLEANUP_IN_PROGRESS, to_state=S.RETIRED,
                   required_evidence=[E.CLEANUP_VERIFIED, E.CLEANUP_MERGED, E.NO_REMAINING_REFERENCES],
                   required_approvals=[ApprovalRole.FLAG_OWNER, ApprovalRole.CODE_REVIEWER],
                   description="Cleanup verified, merged, no references remain, and owner + reviewer approve archiving. "
                               "Zero references alone is never sufficient."),
    # Rollbacks / cancellations
    TransitionRule(from_state=S.TESTING, to_state=S.IN_DEVELOPMENT, required_evidence=_ROLLBACK, is_rollback=True,
                   description="Testing paused; back to development."),
    TransitionRule(from_state=S.ROLLING_OUT, to_state=S.TESTING, required_evidence=_ROLLBACK, is_rollback=True,
                   description="Production rollout rolled back."),
    TransitionRule(from_state=S.FULLY_RELEASED, to_state=S.ROLLING_OUT, required_evidence=_ROLLBACK, is_rollback=True,
                   description="Exposure reduced after full release."),
    TransitionRule(from_state=S.READY_FOR_CLEANUP, to_state=S.FULLY_RELEASED, required_evidence=_ROLLBACK, is_rollback=True,
                   description="Cleanup cancelled; flag still needed."),
    TransitionRule(from_state=S.CLEANUP_IN_PROGRESS, to_state=S.READY_FOR_CLEANUP, required_evidence=_ROLLBACK, is_rollback=True,
                   description="Cleanup attempt abandoned or failed verification."),
]

# Evidence kinds that must point at a concrete artifact (verification id, PR URL...).
EVIDENCE_REQUIRING_REFERENCE = {E.CLEANUP_VERIFIED: "verification", E.CLEANUP_MERGED: "github", E.CLEANUP_PLAN_APPROVED: None}
OVERRIDE_APPROVER_ROLES = {ApprovalRole.FLAG_OWNER, ApprovalRole.RELEASE_MANAGER}


class LifecycleStateMachine:
    def __init__(self, rules: list[TransitionRule] | None = None):
        self.rules = rules or TRANSITION_RULES
        self._by_edge = {(r.from_state, r.to_state): r for r in self.rules}

    def rule(self, from_state: S, to_state: S) -> TransitionRule | None:
        return self._by_edge.get((from_state, to_state))

    def rules_from(self, state: S) -> list[TransitionRule]:
        return [r for r in self.rules if r.from_state == state]

    def check(self, current: S, to_state: S, evidence: list[TransitionEvidence], approvals: list[Approval]) -> TransitionCheck:
        rule = self.rule(current, to_state)
        if rule is None:
            return TransitionCheck(to_state=to_state, allowed=False, description="not a valid transition",
                                   problems=[f"no transition {current.value} -> {to_state.value}"])
        kinds = {e.kind for e in evidence}
        problems = []
        for e in evidence:
            if e.kind in EVIDENCE_REQUIRING_REFERENCE and not e.reference:
                problems.append(f"evidence '{e.kind.value}' must reference a concrete artifact (id or URL)")
                kinds.discard(e.kind)
            expected_source = EVIDENCE_REQUIRING_REFERENCE.get(e.kind)
            if expected_source and e.source != expected_source:
                problems.append(f"evidence '{e.kind.value}' must come from source '{expected_source}', got '{e.source}'")
                kinds.discard(e.kind)
        missing = [k for k in rule.required_evidence if k not in kinds]
        missing_any = [] if not rule.any_of_evidence or kinds & set(rule.any_of_evidence) else list(rule.any_of_evidence)
        roles = {a.role for a in approvals}
        missing_appr = [r for r in rule.required_approvals if r not in roles]
        return TransitionCheck(
            to_state=to_state, allowed=not (missing or missing_any or missing_appr or problems), is_rollback=rule.is_rollback,
            description=rule.description, missing_evidence=missing, missing_any_of=missing_any,
            missing_approvals=missing_appr, problems=problems,
        )

    def transition(self, lifecycle: FlagLifecycle, to_state: S, *, actor: str, reason: str,
                   evidence: list[TransitionEvidence] | None = None, approvals: list[Approval] | None = None,
                   at: datetime | None = None) -> FlagLifecycle:
        evidence, approvals = evidence or [], approvals or []
        if not reason.strip():
            raise TransitionRequirementsError("a reason is required for every transition")
        if self.rule(lifecycle.state, to_state) is None:
            raise InvalidTransitionError(
                f"invalid transition {lifecycle.state.value} -> {to_state.value}",
                from_state=lifecycle.state.value, to_state=to_state.value,
                valid_targets=[r.to_state.value for r in self.rules_from(lifecycle.state)],
            )
        chk = self.check(lifecycle.state, to_state, evidence, approvals)
        if not chk.allowed:
            raise TransitionRequirementsError(
                f"requirements not met for {lifecycle.state.value} -> {to_state.value}",
                missing_evidence=[k.value for k in chk.missing_evidence],
                missing_any_of=[k.value for k in chk.missing_any_of],
                missing_approvals=[r.value for r in chk.missing_approvals],
                problems=chk.problems,
            )
        return self._append(lifecycle, to_state, actor, reason, evidence, approvals, at, override=None)

    def override(self, lifecycle: FlagLifecycle, to_state: S, *, actor: str, justification: str,
                 approvals: list[Approval], evidence: list[TransitionEvidence] | None = None,
                 at: datetime | None = None) -> FlagLifecycle:
        """Manual state correction with an explicit audit record.

        Overrides can skip intermediate states (e.g. importing a flag that is already
        fully released) but can never bypass the requirements for RETIRED.
        """
        evidence = evidence or []
        if not justification.strip():
            raise TransitionRequirementsError("an override requires a justification")
        if not any(a.role in OVERRIDE_APPROVER_ROLES for a in approvals):
            raise TransitionRequirementsError("an override requires approval from a flag owner or release manager",
                                              missing_approvals=[r.value for r in OVERRIDE_APPROVER_ROLES])
        if to_state == lifecycle.state:
            raise InvalidTransitionError("override target equals the current state")
        if to_state in (S.RETIRED, S.CLEANUP_IN_PROGRESS):
            retire_rule = self.rule(S.CLEANUP_IN_PROGRESS, S.RETIRED) if to_state == S.RETIRED else self.rule(S.READY_FOR_CLEANUP, S.CLEANUP_IN_PROGRESS)
            chk = self.check(retire_rule.from_state, to_state, evidence, approvals)
            if not chk.allowed:
                raise SafetyViolationError(
                    f"override to '{to_state.value}' cannot bypass its safety requirements",
                    missing_evidence=[k.value for k in chk.missing_evidence],
                    missing_approvals=[r.value for r in chk.missing_approvals], problems=chk.problems,
                )
        return self._append(lifecycle, to_state, actor, justification, evidence, approvals, at, override=justification)

    def _append(self, lc: FlagLifecycle, to_state: S, actor: str, reason: str, evidence, approvals, at, override) -> FlagLifecycle:
        at = at or utcnow()
        record = TransitionRecord(
            id=stable_id("transition", lc.flag_id, len(lc.history), lc.state.value, to_state.value, at.isoformat()),
            flag_id=lc.flag_id, from_state=lc.state, to_state=to_state, at=at, actor=actor, reason=reason,
            evidence=list(evidence), approvals=list(approvals),
            is_manual_override=override is not None, override_justification=override,
        )
        return lc.model_copy(update={"state": to_state, "updated_at": at, "history": [*lc.history, record]})
