"""Lifecycle evaluation: gather evidence and *suggest* a state.

A suggestion is never applied. Moving a flag requires calling
`LifecycleStateMachine.transition` with the evidence and approvals.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..schemas.analysis import FlagAnalysisReport
from ..schemas.discovery import ReferenceClassification
from ..schemas.flags import FlagConfiguration
from ..schemas.lifecycle import (
    EvidenceKind as E,
    FlagLifecycle,
    LifecycleEvaluation,
    LifecycleState as S,
    TransitionEvidence,
)
from ..schemas.common import utcnow
from ..schemas.stale import StaleFlagFinding, StaleStatus
from .machine import LifecycleStateMachine


def collect_evidence(config: FlagConfiguration | None, analysis: FlagAnalysisReport | None,
                     stale: StaleFlagFinding | None, *, stable_config_days: int, now: datetime) -> list[TransitionEvidence]:
    ev: list[TransitionEvidence] = []
    if analysis is not None:
        confirmed = [r for r in analysis.references if r.classification == ReferenceClassification.CONFIRMED]
        if confirmed:
            ev.append(TransitionEvidence(kind=E.CODE_REFERENCES_PRESENT, source="code_scan", reference=analysis.id, observed_at=now,
                                         description=f"{len(confirmed)} confirmed code reference(s)"))
        elif not analysis.references and not analysis.unresolved_dynamic_references:
            ev.append(TransitionEvidence(kind=E.NO_REMAINING_REFERENCES, source="code_scan", reference=analysis.id, observed_at=now,
                                         description=f"no static or possibly-matching dynamic references in {', '.join(analysis.repositories)}"))
    if config is not None:
        non_prod = [e for e in config.environments if not e.is_production]
        if any(e.enabled for e in non_prod):
            ev.append(TransitionEvidence(kind=E.ENABLED_IN_NON_PRODUCTION, source="flagsmith", observed_at=now,
                                         description="enabled in " + ", ".join(e.environment for e in non_prod if e.enabled)))
        prod = config.production
        uniform = config.uniform_production_state()
        if uniform is True:
            ev.append(TransitionEvidence(kind=E.FULL_PRODUCTION_EXPOSURE, source="flagsmith", observed_at=now,
                                         description="uniformly enabled for all identities in every production environment"))
        elif any(e.enabled for e in prod):
            ev.append(TransitionEvidence(kind=E.PRODUCTION_ROLLOUT_STARTED, source="flagsmith", observed_at=now,
                                         description="enabled for some production identities/environments"))
        changes = [c.at for c in config.recent_changes] + [e.updated_at for e in prod if e.updated_at]
        if prod and changes and now - max(changes) >= timedelta(days=stable_config_days):
            ev.append(TransitionEvidence(kind=E.ROLLOUT_STABLE, source="flagsmith", observed_at=now,
                                         description=f"production configuration unchanged since {max(changes).date().isoformat()}"))
        oc = config.owner_confirmation
        if oc is not None and oc.still_needed is False:
            ev.append(TransitionEvidence(kind=E.OWNER_CONFIRMED_NOT_NEEDED, source="owner", observed_at=oc.confirmed_at,
                                         description=f"{oc.confirmed_by} confirmed the flag is no longer needed"))
    if stale is not None and stale.status in (StaleStatus.READY_FOR_CLEANUP, StaleStatus.SAFE_TO_RETIRE):
        ev.append(TransitionEvidence(kind=E.STALE_ASSESSMENT, source="stale_detector", reference=stale.id, observed_at=stale.evaluated_at,
                                     description=f"stale assessment: {stale.status.value} (confidence {stale.confidence})"))
    return ev


def evaluate_lifecycle(
    flag_name: str,
    *,
    lifecycle: FlagLifecycle | None,
    config: FlagConfiguration | None,
    analysis: FlagAnalysisReport | None,
    stale: StaleFlagFinding | None = None,
    stable_config_days: int = 14,
    machine: LifecycleStateMachine | None = None,
    now: datetime | None = None,
) -> LifecycleEvaluation:
    machine = machine or LifecycleStateMachine()
    now = now or utcnow()
    warnings: list[str] = []
    flag_id = lifecycle.flag_id if lifecycle else (config.flag_id if config else flag_name)
    if lifecycle is None:
        warnings.append("no lifecycle record exists; evaluating from the initial 'created' state")
    current = lifecycle.state if lifecycle else S.CREATED
    evidence = collect_evidence(config, analysis, stale, stable_config_days=stable_config_days, now=now)
    kinds = {e.kind for e in evidence}

    rationale: list[str] = []
    suggested, conf = current, 0.5
    if config is None:
        rationale.append("no Flagsmith configuration available; cannot infer rollout state")
        conf = 0.1
    else:
        prod_enabled = any(e.enabled for e in config.production)
        if E.STALE_ASSESSMENT in kinds and E.OWNER_CONFIRMED_NOT_NEEDED in kinds and current in (S.FULLY_RELEASED, S.READY_FOR_CLEANUP):
            suggested, conf = S.READY_FOR_CLEANUP, 0.7
            rationale.append("stale assessment and owner confirmation both support cleanup")
        elif E.FULL_PRODUCTION_EXPOSURE in kinds:
            suggested = S.FULLY_RELEASED
            conf = 0.65 if E.ROLLOUT_STABLE in kinds else 0.45
            rationale.append("uniform 100% production exposure" + (" with stable configuration" if E.ROLLOUT_STABLE in kinds else ""))
            warnings.append("100% exposure does not prove the rollout is complete; the flag owner must confirm")
        elif E.PRODUCTION_ROLLOUT_STARTED in kinds:
            suggested, conf = S.ROLLING_OUT, 0.7
            rationale.append("flag is exposed to part of production")
        elif E.ENABLED_IN_NON_PRODUCTION in kinds:
            suggested, conf = S.TESTING, 0.6
            rationale.append("enabled only outside production")
        elif E.CODE_REFERENCES_PRESENT in kinds:
            suggested, conf = S.IN_DEVELOPMENT, 0.5
            rationale.append("code references exist but the flag is disabled everywhere")
            if not prod_enabled and config.created_at and (now - config.created_at).days > 90:
                warnings.append("disabled everywhere for a long time: may be abandoned rather than in development")
                conf = 0.3
        else:
            suggested, conf = S.CREATED, 0.4
            rationale.append("no code references and not enabled anywhere")
        if config.is_archived:
            warnings.append("flag is archived in Flagsmith; the engine never infers 'retired' — record it via an approved transition")

    # Never suggest moving into states that only human-approved workflows may reach,
    # and never suggest moving "backwards" from a later recorded state.
    order = list(S)
    if suggested in (S.CLEANUP_IN_PROGRESS, S.RETIRED):
        suggested = current
    if order.index(suggested) < order.index(current):
        if current in (S.READY_FOR_CLEANUP, S.CLEANUP_IN_PROGRESS, S.RETIRED):
            rationale.append(f"recorded state '{current.value}' is kept; evidence is consistent with an earlier state")
            suggested = current
        else:
            warnings.append(f"evidence suggests '{suggested.value}' but the recorded state is '{current.value}': possible rollback, review required")

    transitions = [machine.check(current, r.to_state, evidence, []) for r in machine.rules_from(current)]
    return LifecycleEvaluation(
        flag_id=flag_id, flag_name=flag_name, current_state=current, suggested_state=suggested,
        suggestion_confidence=round(conf, 2), rationale=rationale, available_evidence=evidence,
        transitions=transitions, warnings=warnings, evaluated_at=now,
    )
