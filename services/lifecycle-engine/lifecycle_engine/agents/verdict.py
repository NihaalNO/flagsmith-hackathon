"""Is a flag active, and is it still useful?

The labels are decided by deterministic rules over verified evidence (code references,
Flagsmith state per environment, recent changes, tags, owner statements, experiments).
An optional LLM writes the explanation; its output is validated like cleanup-plan
narratives (cited evidence must exist, no invented files, no safety/test/merge claims)
and replaced by a deterministic explanation if it fails.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..schemas.analysis import FlagAnalysisReport
from ..schemas.common import utcnow
from ..schemas.discovery import ReferenceClassification
from ..schemas.flags import LONG_LIVED_PURPOSES, FlagConfiguration
from ..schemas.planning import LLMPlanNarrative, LLMStatus, LLMUsage
from ..schemas.stale import StaleFlagFinding, StalePolicy
from ..schemas.verdict import Activity, ExperimentStatusInput, FlagVerdict, Usefulness, VerdictReason
from .evidence import allowed_locations, build_evidence
from .llm import LLMProvider, LLMUnavailableError
from .validation import validate_narrative

SYSTEM_PROMPT = """You explain, for engineers, whether a feature flag is active and whether it is still useful.
You receive verified evidence and a verdict computed by deterministic rules.
Rules:
- Explain the verdict in 2-4 short sentences in `reasoning_summary`; do not change the verdict.
- Use ONLY the evidence in the packet. Never invent flag references, files, line numbers, configuration,
  usage numbers, experiments or owners.
- Every statement of type "fact" must cite evidence ids from the packet; label inferences "hypothesis".
- Do not claim the flag is safe to delete, that tests pass, or that anything was merged or archived.
Return JSON matching the provided schema."""

LONG_LIVED_TAGS = {"permanent", "kill-switch", "ops", "permission", "remote-config"}


def _ev(stale: StaleFlagFinding | None, kind: str) -> list[str]:
    return [e.id for e in (stale.evidence if stale else []) if e.kind == kind]


def assess_flag(
    *,
    config: FlagConfiguration | None,
    analysis: FlagAnalysisReport | None,
    stale: StaleFlagFinding | None,
    experiments: list[ExperimentStatusInput] | None = None,
    llm: LLMProvider | None = None,
    policy: StalePolicy | None = None,
    now: datetime | None = None,
) -> FlagVerdict:
    now = now or utcnow()
    policy = policy or StalePolicy()
    flag_name = (config.flag_name if config else None) or (analysis.flag_name if analysis else "") or (stale.flag_name if stale else "")
    experiments = experiments or []
    caveats: list[str] = []
    act: list[VerdictReason] = []
    use: list[VerdictReason] = []

    refs = analysis.references if analysis else []
    confirmed = [r for r in refs if r.classification == ReferenceClassification.CONFIRMED]
    ref_ids = [r.id for r in refs]
    dynamic = analysis.unresolved_dynamic_references if analysis else []
    running = [e for e in experiments if e.status == "running"]

    def A(points, text, ids=()):
        act.append(VerdictReason(text=text, points_to=points.value, evidence_ids=list(ids)))

    def U(points, text, ids=()):
        use.append(VerdictReason(text=text, points_to=points.value, evidence_ids=list(ids)))

    # ---------------- facts ----------------
    prod = config.production if config else []
    non_prod = [e for e in (config.environments if config else []) if not e.is_production]
    targeted = [e for e in prod if not e.is_uniform]
    uniform = config.uniform_production_state() if config else None
    changes = ([c.at for c in config.recent_changes] if config else []) + [e.updated_at for e in prod if e.updated_at]
    days_since_change = (now - max(changes)).days if changes else None
    evaluations = config.activity.evaluation_count if config and config.activity else None
    tags = {t.lower() for t in (config.tags if config else [])} & LONG_LIVED_TAGS
    oc = config.owner_confirmation if config else None
    archived = bool(config and config.is_archived)
    all_off = bool(config and config.environments) and not any(e.enabled for e in config.environments)
    prod_on = any(e.enabled for e in prod)
    differs = bool(prod and non_prod) and {e.enabled for e in prod} != {e.enabled for e in non_prod}

    if config is None:
        caveats.append("no Flagsmith configuration was available; only code evidence was used")
    elif not prod:
        caveats.append("no production environment state; activity and usefulness are less certain")
    if dynamic:
        caveats.append(f"{len(dynamic)} dynamic flag reference(s) might evaluate this flag at runtime")
    if evaluations is None and config is not None:
        caveats.append("evaluation analytics are not available, so real traffic is unknown")

    # ---------------- activity ----------------
    if running:
        A(Activity.ACTIVE, f"an A/B experiment is running on it ({', '.join(e.name for e in running)})")
    if targeted:
        A(Activity.ACTIVE, "production serves different values to different users (segment/identity overrides or a split)",
          _ev(stale, "production_targeting"))
    if days_since_change is not None and days_since_change < policy.stable_config_days:
        A(Activity.ACTIVE, f"its production configuration changed {days_since_change} day(s) ago", _ev(stale, "config_stability"))
    if evaluations:
        A(Activity.ACTIVE, f"it was evaluated {evaluations} time(s) recently", _ev(stale, "evaluation_activity"))
    if confirmed and prod_on:
        A(Activity.ACTIVE, f"{len(confirmed)} code reference(s) check it and it is on in production", ref_ids[:5])
    elif confirmed and any(e.enabled for e in non_prod):
        A(Activity.ACTIVE, "code checks it and it is on outside production (being tested)", ref_ids[:5])
    if archived:
        A(Activity.INACTIVE, "it is archived in Flagsmith", _ev(stale, "archived"))
    if not refs and not evaluations:
        A(Activity.INACTIVE, "no scanned code evaluates it" + ("" if evaluations == 0 else " (traffic unknown)"),
          _ev(stale, "code_reference_count"))
    if all_off and (days_since_change is None or days_since_change >= policy.stable_config_days):
        A(Activity.INACTIVE, "it is off in every environment", _ev(stale, "production_state"))

    active_r = [r for r in act if r.points_to == Activity.ACTIVE.value]
    inactive_r = [r for r in act if r.points_to == Activity.INACTIVE.value]
    if archived and not running:
        activity = Activity.INACTIVE
    elif active_r:
        activity = Activity.ACTIVE
    elif inactive_r:
        activity = Activity.INACTIVE
    else:
        activity = Activity.UNKNOWN
        if not act:
            A(Activity.UNKNOWN, "not enough evidence: no Flagsmith state and no conclusive code evidence")

    # ---------------- usefulness ----------------
    if oc and oc.purpose in LONG_LIVED_PURPOSES:
        U(Usefulness.USEFUL, f"its owner says it is a long-lived {oc.purpose.value.replace('_', ' ')}", _ev(stale, "owner_confirmation"))
    if tags:
        U(Usefulness.USEFUL, f"it is tagged {', '.join(sorted(tags))} (meant to stay)", _ev(stale, "long_lived_tag"))
    if oc and oc.still_needed is True:
        U(Usefulness.USEFUL, f"{oc.confirmed_by} confirmed it is still needed", _ev(stale, "owner_confirmation"))
    if running:
        U(Usefulness.USEFUL, "it splits traffic for a running experiment")
    if targeted:
        U(Usefulness.USEFUL, "it gives different users different behaviour in production", _ev(stale, "production_targeting"))
    if differs and confirmed:
        U(Usefulness.USEFUL, "it is on in some environments and off in others (rollout or testing in progress)",
          _ev(stale, "environments_agree"))
    if days_since_change is not None and days_since_change < policy.stable_config_days and not targeted:
        U(Usefulness.USEFUL, "it was changed recently, so it is still being used as a switch", _ev(stale, "config_stability"))

    if oc and oc.still_needed is False:
        U(Usefulness.NOT_USEFUL, f"{oc.confirmed_by} confirmed it is no longer needed", _ev(stale, "owner_confirmation"))
    if archived and confirmed:
        U(Usefulness.NOT_USEFUL, "code still checks a flag that is archived in Flagsmith", ref_ids[:5])
    if not refs and not evaluations and not running:
        U(Usefulness.NOT_USEFUL, "nothing in the scanned code uses it", _ev(stale, "code_reference_count"))
    stable = days_since_change is not None and days_since_change >= policy.stable_config_days
    if uniform is not None and not targeted and not running and stable and confirmed and not differs:
        U(Usefulness.NOT_USEFUL,
          f"every production user gets the same behaviour (uniformly {'on' if uniform else 'off'} for {days_since_change} days), "
          "so the check no longer changes anything", _ev(stale, "production_state") + _ev(stale, "config_stability"))

    useful_r = [r for r in use if r.points_to == Usefulness.USEFUL.value]
    not_useful_r = [r for r in use if r.points_to == Usefulness.NOT_USEFUL.value]
    if oc and oc.still_needed is False and not running:
        usefulness = Usefulness.NOT_USEFUL
    elif useful_r:
        usefulness = Usefulness.USEFUL
    elif not_useful_r:
        usefulness = Usefulness.NOT_USEFUL
    else:
        usefulness = Usefulness.UNCERTAIN
        U(Usefulness.UNCERTAIN, "the evidence doesn't show whether it still changes behaviour")

    # ---------------- confidence ----------------
    support = len(useful_r if usefulness == Usefulness.USEFUL else not_useful_r if usefulness == Usefulness.NOT_USEFUL else [])
    against = len(not_useful_r if usefulness == Usefulness.USEFUL else useful_r if usefulness == Usefulness.NOT_USEFUL else [])
    confidence = 0.35 + 0.15 * support - 0.15 * against
    if config is None or not prod:
        confidence -= 0.15
    if dynamic and usefulness == Usefulness.NOT_USEFUL:
        confidence -= 0.15
    if evaluations is None:
        confidence -= 0.05
    if activity == Activity.UNKNOWN or usefulness == Usefulness.UNCERTAIN:
        confidence = min(confidence, 0.35)
    confidence = round(max(0.1, min(0.95, confidence)), 2)

    recommendation = _recommend(activity, usefulness, uniform, archived, stale, caveats)
    evidence, directions = build_evidence(analysis, stale, None) if stale is not None else ([], {})
    explanation = _deterministic_explanation(flag_name, activity, usefulness, act, use, recommendation)
    verdict = FlagVerdict(
        flag_name=flag_name, activity=activity, usefulness=usefulness, confidence=confidence,
        activity_reasons=act, usefulness_reasons=use, recommendation=recommendation, explanation=explanation,
        evidence=evidence, caveats=caveats, llm=LLMUsage(status=LLMStatus.NOT_CONFIGURED), evaluated_at=now)
    if llm is not None and stale is not None:
        verdict = _with_llm(verdict, llm, analysis, stale, archived)
    return verdict


def _recommend(activity, usefulness, uniform, archived, stale, caveats) -> str:
    keep = {True: "enabled", False: "disabled"}.get(uniform)
    if usefulness == Usefulness.USEFUL:
        return "Keep it. It still changes behaviour for some users or is meant to stay."
    if usefulness == Usefulness.NOT_USEFUL:
        if archived:
            return "Remove the remaining code checks for this archived flag (review the generated cleanup plan)."
        if activity == Activity.INACTIVE:
            return "Confirm with the owner, then remove the flag from code and archive it in Flagsmith after the cleanup is merged."
        path = f" keeping the {keep} path" if keep else ""
        return (f"Plan a cleanup{path}: ask the owner to confirm it is no longer needed, then use the cleanup plan. "
                "Don't archive it in Flagsmith until the code change is merged and verified.")
    missing = "; ".join(m.description for m in (stale.missing_evidence if stale else [])[:3])
    return "Gather more evidence before deciding" + (f": {missing}." if missing else ".")


def _deterministic_explanation(flag, activity, usefulness, act, use, recommendation) -> str:
    def first(reasons, label):
        r = next((x for x in reasons if x.points_to == label), None)
        return f": {r.text}" if r else ""
    a = {"active": "is active", "inactive": "looks inactive", "unknown": "has unknown activity"}[activity.value]
    u = {"useful": "still useful", "not_useful": "no longer useful", "uncertain": "of uncertain use"}[usefulness.value]
    return f"'{flag}' {a}{first(act, activity.value)}. It seems {u}{first(use, usefulness.value)}. {recommendation}"


def _with_llm(verdict: FlagVerdict, llm: LLMProvider, analysis, stale: StaleFlagFinding, archived: bool) -> FlagVerdict:
    ids = {e.id for e in verdict.evidence}
    usage = LLMUsage(status=LLMStatus.UNAVAILABLE, provider=llm.name, model=getattr(llm, "model", None))
    context = {
        "flag_name": verdict.flag_name,
        "verdict": {"activity": verdict.activity.value, "usefulness": verdict.usefulness.value,
                    "confidence": verdict.confidence, "recommendation": verdict.recommendation},
        "reasons": [r.model_dump(mode="json") for r in verdict.activity_reasons + verdict.usefulness_reasons],
        "caveats": verdict.caveats,
        "evidence": [e.model_dump(mode="json", exclude_none=True) for e in verdict.evidence],
        "deterministic_recommendation": verdict.recommendation,
        "stale_status": stale.status.value,
        "recommended_action": verdict.recommendation,
    }
    try:
        raw = llm.complete_json(system=SYSTEM_PROMPT, prompt="Evidence packet:\n" + json.dumps(context, indent=1),
                                schema=LLMPlanNarrative.model_json_schema(), context=context)
        parsed, errors = validate_narrative(raw, evidence_ids=ids, locations=allowed_locations(verdict.evidence, None, analysis),
                                            stale_status=stale.status, archived=archived)
    except LLMUnavailableError as exc:
        return verdict.model_copy(update={"llm": usage.model_copy(update={"error": str(exc)})})
    if parsed is None:
        return verdict.model_copy(update={"llm": usage.model_copy(update={"status": LLMStatus.REJECTED_INVALID_OUTPUT,
                                                                           "validation_errors": errors})})
    return verdict.model_copy(update={"explanation": parsed.reasoning_summary, "statements": parsed.statements,
                                      "llm": usage.model_copy(update={"status": LLMStatus.USED})})
