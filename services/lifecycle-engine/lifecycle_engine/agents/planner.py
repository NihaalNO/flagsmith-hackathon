"""Module 5 — cleanup planner.

Deterministic analysis decides *what* (action, affected files, proposed changes,
approvals, verification). An optional LLM may only add narrative (summary,
statements, risks, patch rationale) that cites the evidence packet; invalid or
unavailable LLM output falls back to a deterministic narrative.
"""

from __future__ import annotations

import json

from ..schemas.analysis import FlagAnalysisReport, FindingKind, UncertaintyKind
from ..schemas.common import stable_id
from ..schemas.lifecycle import ApprovalRole, LifecycleState
from ..schemas.patch import CleanupPatch, PatchStatus
from ..schemas.planning import (
    CleanupPlan,
    EvidenceRef,
    LLMPlanNarrative,
    LLMStatus,
    LLMUsage,
    ProposedChange,
    ReasonedStatement,
    RecommendedAction,
    RiskAssessment,
    RiskItem,
    RiskLevel,
    StatementType,
    VerificationRequirement,
)
from ..schemas.stale import CleanupDirection, StaleFlagFinding, StaleStatus
from .evidence import allowed_locations, build_evidence
from .llm import LLMProvider, LLMUnavailableError
from .validation import validate_narrative

SYSTEM_PROMPT = """You explain feature-flag cleanup plans for FlagPilot.
You receive an evidence packet produced by deterministic code analysis and policy evaluation.
Rules:
- Use ONLY the evidence in the packet. Never invent flag references, file paths, line numbers, Flagsmith configuration,
  test results, repository activity or lifecycle transitions.
- Every statement of type "fact" must cite one or more evidence ids from the packet.
- Label inferences as "hypothesis" and suggestions as "recommendation".
- Do not claim the flag is safe to delete/retire, that tests pass, or that anything was merged or archived.
- Do not change the recommended action; explain it.
Return JSON matching the provided schema."""

_ACTION = {
    StaleStatus.NOT_STALE: RecommendedAction.NO_ACTION,
    StaleStatus.INSUFFICIENT_EVIDENCE: RecommendedAction.GATHER_EVIDENCE,
    StaleStatus.POTENTIALLY_STALE: RecommendedAction.REQUEST_OWNER_REVIEW,
    StaleStatus.READY_FOR_CLEANUP: RecommendedAction.PREPARE_CLEANUP_PATCH,
    StaleStatus.SAFE_TO_RETIRE: RecommendedAction.REQUEST_ARCHIVE_APPROVAL,
}
_LEVEL_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]


class CleanupPlanner:
    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def plan(self, *, analysis: FlagAnalysisReport | None, stale: StaleFlagFinding, patch: CleanupPatch | None = None,
             lifecycle_state: LifecycleState | None = None, archived: bool = False) -> CleanupPlan:
        action = _ACTION[stale.status]
        if action == RecommendedAction.PREPARE_CLEANUP_PATCH and stale.cleanup_direction == CleanupDirection.ARCHIVE_ONLY:
            action = RecommendedAction.REQUEST_ARCHIVE_APPROVAL
        evidence, directions = build_evidence(analysis, stale, patch)
        ev_ids = {e.id for e in evidence}
        locations = allowed_locations(evidence, patch, analysis)

        affected = sorted({f"{r.repository}:{r.location.file_path}" for r in (analysis.references if analysis else [])}
                          | ({f"{f.repository}:{f.file_path}" for f in patch.files} if patch else set()))
        obsolete = self._obsolete_paths(analysis, stale)
        changes = self._proposed_changes(patch)
        risk = self._risk(analysis, stale, patch, evidence)
        verification = self._verification(analysis, patch, stale)
        approvals = [] if action == RecommendedAction.NO_ACTION else [ApprovalRole.FLAG_OWNER]
        if patch is not None and patch.files:
            approvals.append(ApprovalRole.CODE_REVIEWER)

        det_recommendation = self._recommendation_text(action, stale, patch)
        narrative = self._deterministic_narrative(stale, analysis, evidence, directions, det_recommendation, patch)
        usage = LLMUsage(status=LLMStatus.NOT_CONFIGURED)
        if self.llm is not None:
            usage = LLMUsage(status=LLMStatus.UNAVAILABLE, provider=self.llm.name, model=getattr(self.llm, "model", None))
            context = {
                "flag_name": stale.flag_name,
                "stale_status": stale.status.value,
                "cleanup_direction": stale.cleanup_direction.value,
                "recommended_action": action.value,
                "deterministic_recommendation": det_recommendation,
                "evidence": [{**e.model_dump(mode="json", exclude_none=True), **({"direction": directions[e.id]} if e.id in directions else {})}
                             for e in evidence],
                "affected_files": affected,
                "missing_evidence": [m.description for m in stale.missing_evidence],
            }
            try:
                raw = self.llm.complete_json(system=SYSTEM_PROMPT, prompt="Evidence packet:\n" + json.dumps(context, indent=1),
                                             schema=LLMPlanNarrative.model_json_schema(), context=context)
                parsed, errors = validate_narrative(raw, evidence_ids=ev_ids, locations=locations,
                                                    stale_status=stale.status, archived=archived)
                if parsed is None:
                    usage = usage.model_copy(update={"status": LLMStatus.REJECTED_INVALID_OUTPUT, "validation_errors": errors})
                else:
                    narrative = parsed
                    usage = usage.model_copy(update={"status": LLMStatus.USED})
                    risk = self._merge_llm_risks(risk, parsed)
            except LLMUnavailableError as exc:
                usage = usage.model_copy(update={"error": str(exc)})

        confidence = stale.confidence
        if patch is not None and patch.status == PatchStatus.PARTIAL:
            confidence *= 0.8
        return CleanupPlan(
            id=stable_id("plan", stale.id, patch.id if patch else "", action.value),
            flag_id=stale.flag_id, flag_name=stale.flag_name, lifecycle_state=lifecycle_state or stale.lifecycle_state,
            stale_status=stale.status, cleanup_direction=stale.cleanup_direction, recommended_action=action,
            reasoning_summary=narrative.reasoning_summary, statements=narrative.statements, evidence=evidence,
            affected_files=affected, obsolete_code_paths=obsolete, proposed_changes=changes,
            patch_id=patch.id if patch and patch.files else None, patch_rationale=narrative.patch_rationale,
            risk=risk, confidence=round(confidence, 2), missing_evidence=stale.missing_evidence,
            required_approvals=approvals, verification_requirements=verification, llm=usage,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _recommendation_text(action: RecommendedAction, stale: StaleFlagFinding, patch: CleanupPatch | None) -> str:
        base = stale.recommended_next_action
        if action == RecommendedAction.PREPARE_CLEANUP_PATCH and patch is not None and patch.files:
            base = (f"Review the suggested patch ({len(patch.files)} file(s), {patch.status.value}) and run verification "
                    "before opening a pull request. " + base)
        return base

    @staticmethod
    def _deterministic_narrative(stale, analysis, evidence: list[EvidenceRef], directions, recommendation, patch) -> LLMPlanNarrative:
        supports = [e for e in evidence if directions.get(e.id) == "supports"]
        opposes = [e for e in evidence if directions.get(e.id) == "opposes"]
        refs = [e for e in evidence if e.kind == "code_reference"]
        blockers = [e for e in evidence if e.kind == "blocker"]
        statements: list[ReasonedStatement] = []
        for e in supports:
            statements.append(ReasonedStatement(type=StatementType.FACT, text=e.summary.split("] ", 1)[-1], evidence_ids=[e.id]))
        for e in opposes:
            statements.append(ReasonedStatement(type=StatementType.FACT, text="Against staleness: " + e.summary.split("] ", 1)[-1], evidence_ids=[e.id]))
        if refs:
            statements.append(ReasonedStatement(type=StatementType.FACT, text=f"{len(refs)} code reference(s) to the flag were found.",
                                                evidence_ids=[e.id for e in refs]))
        for e in evidence:
            if e.kind == "analysis_finding" and e.summary.startswith("[hypothesis]"):
                statements.append(ReasonedStatement(type=StatementType.HYPOTHESIS, text=e.summary.removeprefix("[hypothesis] "), evidence_ids=[e.id]))
        for b in blockers:
            statements.append(ReasonedStatement(type=StatementType.FACT, text="Blocker: " + b.summary, evidence_ids=[b.id]))
        statements.append(ReasonedStatement(type=StatementType.RECOMMENDATION, text=recommendation))
        summary = (f"'{stale.flag_name}' is assessed as {stale.status.value.replace('_', ' ')} "
                   f"(confidence {stale.confidence}, {len(supports)} supporting / {len(opposes)} opposing signals, "
                   f"{len(blockers)} blocker(s), {len(stale.missing_evidence)} missing evidence item(s)).")
        rationale = None
        if patch is not None and patch.files:
            keep = {CleanupDirection.KEEP_ENABLED_PATH: "enabled", CleanupDirection.KEEP_DISABLED_PATH: "disabled"}.get(stale.cleanup_direction)
            rationale = (f"Production uniformly runs the {keep} path, so the patch keeps that path and removes the flag checks."
                         if keep else f"The patch inlines the uniform production value {stale.inline_value!r}.")
        return LLMPlanNarrative(reasoning_summary=summary, statements=statements, risks=[], patch_rationale=rationale)

    @staticmethod
    def _obsolete_paths(analysis: FlagAnalysisReport | None, stale: StaleFlagFinding) -> list[str]:
        if analysis is None or stale.cleanup_direction not in (CleanupDirection.KEEP_ENABLED_PATH, CleanupDirection.KEEP_DISABLED_PATH):
            return []
        enabled_kept = stale.cleanup_direction == CleanupDirection.KEEP_ENABLED_PATH
        out = []
        for b in analysis.branches:
            dead = b.disabled_path if enabled_kept else b.enabled_path
            if dead is not None:
                tag = "" if b.determinable else " (indeterminate; verify manually)"
                out.append(f"{dead.span.repository}:{dead.span.file_path}:{dead.span.line}-{dead.span.end_line} "
                           f"{dead.description}{tag}: {dead.excerpt.splitlines()[0][:80]}")
        for s in analysis.related_symbols:
            if s.exclusively_flag_gated and s.defined_at and (
                    (s.relation.value == "used_in_disabled_path") == enabled_kept):
                out.append(f"{s.defined_at.repository}:{s.defined_at.file_path}:{s.defined_at.line} {s.kind} `{s.name}` "
                           "is only reachable through the removed path")
        return out

    @staticmethod
    def _proposed_changes(patch: CleanupPatch | None) -> list[ProposedChange]:
        if patch is None:
            return []
        out = []
        for fc in patch.files:
            for e in fc.edits:
                out.append(ProposedChange(id=stable_id("chg", patch.id, fc.file_path, e.line, e.description, length=12),
                                          repository=fc.repository, file_path=fc.file_path, line=e.line,
                                          reference_id=e.reference_id, change_kind=e.kind.value, description=e.description))
        for ms in patch.manual_steps:
            out.append(ProposedChange(id=stable_id("chg", patch.id, ms.file_path, ms.line, ms.reason, length=12),
                                      repository=ms.repository, file_path=ms.file_path, line=ms.line,
                                      reference_id=ms.reference_id, change_kind="manual", description=ms.reason))
        return out

    @staticmethod
    def _risk(analysis, stale: StaleFlagFinding, patch: CleanupPatch | None, evidence: list[EvidenceRef]) -> RiskAssessment:
        items: list[RiskItem] = []
        by_kind = {}
        for e in evidence:
            by_kind.setdefault(e.kind, []).append(e)
        for b in by_kind.get("blocker", []):
            items.append(RiskItem(level=RiskLevel.HIGH, description=b.summary, evidence_ids=[b.id],
                                  mitigation="resolve or explicitly review before cleanup"))
        for m in by_kind.get("missing_evidence", []):
            items.append(RiskItem(level=RiskLevel.MEDIUM, description="missing evidence: " + m.summary, evidence_ids=[m.id]))
        if analysis is not None:
            repos = {r.repository for r in analysis.references}
            if len(repos) > 1:
                items.append(RiskItem(level=RiskLevel.MEDIUM, description=f"cleanup spans {len(repos)} repositories; changes must ship together or in a safe order",
                                      evidence_ids=[e.id for e in by_kind.get("code_reference", [])], mitigation="coordinate merges and deploys"))
            if analysis.references and not analysis.related_tests:
                items.append(RiskItem(level=RiskLevel.MEDIUM, description="no tests were associated with flag-controlled code",
                                      mitigation="add or identify tests before merging"))
            escapes = [f for f in analysis.findings if f.kind == FindingKind.FLAG_VALUE_ESCAPES_SCOPE]
            if escapes:
                items.append(RiskItem(level=RiskLevel.MEDIUM, description="flag value flows into other code; downstream behaviour not analyzed",
                                      evidence_ids=[f.id for f in escapes]))
            if any(u.kind == UncertaintyKind.INDETERMINATE_BRANCH for u in analysis.uncertainties):
                items.append(RiskItem(level=RiskLevel.MEDIUM, description="compound conditions need manual review",
                                      evidence_ids=[b.id for b in by_kind.get("branch", [])]))
        if patch is not None:
            for ms in by_kind.get("manual_step", []):
                items.append(RiskItem(level=RiskLevel.MEDIUM, description=ms.summary, evidence_ids=[ms.id]))
            if patch.follow_ups:
                items.append(RiskItem(level=RiskLevel.LOW, description=f"{len(patch.follow_ups)} follow-up item(s) (newly unused code) need review"))
        overall = max((i.level for i in items), key=_LEVEL_ORDER.index, default=RiskLevel.LOW)
        return RiskAssessment(overall=overall, items=items)

    @staticmethod
    def _merge_llm_risks(risk: RiskAssessment, n: LLMPlanNarrative) -> RiskAssessment:
        extra = [RiskItem(level=r.level, description=f"[llm] {r.description}", evidence_ids=r.evidence_ids, mitigation=r.mitigation)
                 for r in n.risks]
        items = risk.items + extra
        return RiskAssessment(overall=max((i.level for i in items), key=_LEVEL_ORDER.index, default=RiskLevel.LOW), items=items)

    @staticmethod
    def _verification(analysis, patch: CleanupPatch | None, stale) -> list[VerificationRequirement]:
        out: list[VerificationRequirement] = []
        if analysis is not None:
            for t in analysis.related_tests:
                out.append(VerificationRequirement(kind="tests", repository=t.repository, target=t.file_path,
                                                   description=f"run {t.file_path} ({t.strength}: {'; '.join(t.reasons)})"))
        if patch is not None and patch.files:
            out.append(VerificationRequirement(kind="static_check", description="re-parse every changed file; no syntax errors"))
            out.append(VerificationRequirement(kind="static_check", description="run the project's type checker / linter if configured (e.g. tsc --noEmit)"))
            out.append(VerificationRequirement(kind="rescan", description=f"re-scan all repositories; expect zero references to '{stale.flag_name}'"))
        out.append(VerificationRequirement(kind="manual_review", description="code review by the flag owner and a reviewer"))
        return out
