"""Module 4 — deterministic, evidence-based stale flag detection.

The detector accumulates weighted evidence for and against staleness, records what
evidence is missing, and applies *blockers* that cap the strongest status that may
be recommended. It never produces a deletion decision.

Safety properties (tested):
  * zero code references alone never yields READY_FOR_CLEANUP or SAFE_TO_RETIRE;
  * 100% rollout alone never yields READY_FOR_CLEANUP;
  * unresolved dynamic references cap the status at POTENTIALLY_STALE;
  * SAFE_TO_RETIRE requires a lifecycle history with verified + merged cleanup.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..schemas.analysis import FlagAnalysisReport
from ..schemas.common import EvidenceDirection as D, EvidenceItem, MissingEvidence, stable_id, utcnow
from ..schemas.discovery import ReferenceClassification, UsageType
from ..schemas.flags import LONG_LIVED_PURPOSES, ConsumerCoverage, FlagConfiguration
from ..schemas.lifecycle import EvidenceKind, FlagLifecycle, LifecycleState
from ..schemas.stale import (
    STATUS_ORDER,
    Blocker,
    CleanupDirection,
    StaleFlagFinding,
    StalePolicy,
    StaleStatus,
)

ACTIVE_LIFECYCLE = {LifecycleState.CREATED, LifecycleState.IN_DEVELOPMENT, LifecycleState.TESTING, LifecycleState.ROLLING_OUT}


def _rank(s: StaleStatus) -> int:
    return STATUS_ORDER.index(s)


class _Acc:
    def __init__(self, flag_name: str, policy: StalePolicy):
        self.flag_name = flag_name
        self.policy = policy
        self.items: list[EvidenceItem] = []
        self.missing: list[MissingEvidence] = []
        self.blockers: list[Blocker] = []
        self.uncertainty: list[str] = []

    def add(self, kind, source, description, direction=D.NEUTRAL, weight_key=None, value=None, observed_at=None):
        w = self.policy.weights.get(weight_key, 0.0) if weight_key else 0.0
        signed = w if direction == D.SUPPORTS else (-w if direction == D.OPPOSES else 0.0)
        self.items.append(EvidenceItem(
            id=stable_id("ev", self.flag_name, kind, description, length=12), kind=kind, source=source,
            description=description, direction=direction, weight=round(signed, 3), value=value, observed_at=observed_at))

    def miss(self, kind, description, blocks):
        self.missing.append(MissingEvidence(kind=kind, description=description, blocks=[b.value for b in blocks]))

    def block(self, kind, description, cap: StaleStatus):
        self.blockers.append(Blocker(kind=kind, description=description, caps_status_at=cap))

    @property
    def score(self) -> float:
        return round(sum(i.weight for i in self.items), 3)


class StaleFlagDetector:
    def __init__(self, policy: StalePolicy | None = None):
        self.policy = policy or StalePolicy()

    def evaluate(
        self,
        flag_name: str,
        *,
        config: FlagConfiguration | None,
        analysis: FlagAnalysisReport | None,
        lifecycle: FlagLifecycle | None = None,
        coverage: ConsumerCoverage | None = None,
        now: datetime | None = None,
    ) -> StaleFlagFinding:
        now = now or utcnow()
        p = self.policy
        acc = _Acc(flag_name, p)
        R, P = StaleStatus.READY_FOR_CLEANUP, StaleStatus.POTENTIALLY_STALE
        hard_not_stale = False
        direction = CleanupDirection.UNDETERMINED
        inline_value = None

        # ---------------- Flagsmith configuration ----------------
        if config is None:
            acc.miss("flag_configuration", "flag configuration from Flagsmith is unavailable", [P, R])
        else:
            if config.created_at is None:
                acc.miss("flag_age", "flag creation date unknown", [R])
            else:
                age = (now - config.created_at).days
                if age < p.min_age_days:
                    acc.add("flag_age", "flagsmith", f"flag is {age} days old (policy minimum {p.min_age_days})", D.OPPOSES, "young", age)
                    hard_not_stale = True
                else:
                    acc.add("flag_age", "flagsmith", f"flag is {age} days old; age alone does not indicate staleness", D.SUPPORTS, "age", age)

            tags = {t.lower() for t in config.tags} & {t.lower() for t in p.long_lived_tags}
            if tags:
                acc.add("long_lived_tag", "flagsmith", f"flag is tagged {sorted(tags)} (long-lived by design)", D.OPPOSES, "targeting_active")
                hard_not_stale = True

            if not config.production:
                acc.miss("production_state", "no production environment state available", [P, R])
            else:
                uniform = config.uniform_production_state()
                split = [e for e in config.production if not e.is_uniform]
                if split:
                    detail = ", ".join(f"{e.environment}: {e.segment_overrides} segment / {e.identity_overrides} identity overrides"
                                       + (f", {e.percentage_allocation}% split" if e.percentage_allocation not in (None, 0, 100) else "")
                                       for e in split)
                    acc.add("production_targeting", "flagsmith", f"production targeting is active ({detail})", D.OPPOSES, "targeting_active")
                    hard_not_stale = True
                elif uniform is None:
                    acc.add("production_targeting", "flagsmith", "production environments disagree on the flag state", D.OPPOSES, "targeting_active")
                    hard_not_stale = True
                else:
                    acc.add("production_state", "flagsmith",
                            f"flag is uniformly {'enabled (100%)' if uniform else 'disabled'} in all production environments; "
                            "this alone does not prove the rollout is finished", D.SUPPORTS, "uniform_production", uniform)
                    direction = CleanupDirection.KEEP_ENABLED_PATH if uniform else CleanupDirection.KEEP_DISABLED_PATH
                    non_prod = [e for e in config.environments if not e.is_production]
                    if non_prod:
                        if all(e.is_uniform and e.enabled == uniform for e in non_prod):
                            acc.add("environments_agree", "flagsmith", "all non-production environments match production", D.SUPPORTS, "environments_agree")
                        else:
                            acc.add("environments_agree", "flagsmith",
                                    "non-production environments differ from production (may still be toggled for testing)", D.OPPOSES, "environments_agree")

                changes = [c.at for c in config.recent_changes if c.environment is None or
                           any(e.environment == c.environment for e in config.production)]
                changes += [e.updated_at for e in config.production if e.updated_at is not None]
                if not changes:
                    acc.miss("config_stability", "no production configuration change history available", [R])
                else:
                    last = max(changes)
                    days = (now - last).days
                    if days < p.stable_config_days:
                        acc.add("config_stability", "flagsmith", f"production configuration changed {days} days ago", D.OPPOSES,
                                "config_recently_changed", days, last)
                        acc.block("recent_config_change",
                                  f"production configuration changed {days} days ago (policy requires {p.stable_config_days} stable days)", P)
                    else:
                        acc.add("config_stability", "flagsmith", f"production configuration unchanged for {days} days", D.SUPPORTS,
                                "config_stable", days, last)

            if not config.owners:
                acc.miss("ownership", "flag has no recorded owner who can confirm its purpose", [R])
            else:
                acc.add("ownership", "flagsmith", f"owners: {', '.join(config.owners)}", D.NEUTRAL, value=len(config.owners))

            oc = config.owner_confirmation
            if oc is None:
                acc.miss("owner_confirmation", "owner has not confirmed whether the flag is still needed", [R] if p.require_owner_confirmation else [])
            elif oc.purpose in LONG_LIVED_PURPOSES:
                acc.add("owner_confirmation", "owner", f"{oc.confirmed_by} confirmed purpose '{oc.purpose.value}' (long-lived)", D.OPPOSES, "targeting_active")
                hard_not_stale = True
            elif oc.still_needed is True:
                acc.add("owner_confirmation", "owner", f"{oc.confirmed_by} confirmed the flag is still needed", D.OPPOSES, "targeting_active")
                hard_not_stale = True
            elif oc.still_needed is False:
                acc.add("owner_confirmation", "owner", f"{oc.confirmed_by} confirmed the flag is no longer needed ({oc.purpose.value})",
                        D.SUPPORTS, "owner_not_needed", observed_at=oc.confirmed_at)
            else:
                acc.miss("owner_confirmation", "owner confirmed the purpose but not whether it is still needed", [R])

            if config.is_archived:
                acc.add("archived", "flagsmith", "flag is already archived in Flagsmith", D.NEUTRAL, value=True)

        # ---------------- code evidence ----------------
        cov = coverage or ConsumerCoverage(scanned_repositories=analysis.repositories if analysis else [])
        refs = analysis.references if analysis else []
        confirmed = [r for r in refs if r.classification == ReferenceClassification.CONFIRMED]
        potential = [r for r in refs if r.classification == ReferenceClassification.POTENTIAL]
        dynamic = []
        for r in (analysis.unresolved_dynamic_references if analysis else []):
            review = next((rv for rv in cov.dynamic_reference_reviews if rv.reference_id == r.id and rv.excludes(flag_name)), None)
            if review is None:
                dynamic.append(r)
            else:
                acc.add("dynamic_reference_review", "owner",
                        f"{review.reviewed_by} reviewed dynamic reference {r.location.file_path}:{r.location.line} "
                        f"(`{r.argument_expression}`) and confirmed it does not evaluate this flag", D.NEUTRAL,
                        value=r.id, observed_at=review.reviewed_at)
        if analysis is None:
            acc.miss("code_scan", "no code analysis available", [P, R])
        else:
            locs = sorted({f"{r.repository}:{r.location.file_path}" for r in refs})
            if confirmed:
                acc.add("code_reference_count", "code_scan",
                        f"{len(confirmed)} confirmed reference(s) in {len(locs)} file(s): {', '.join(locs)}", D.NEUTRAL, value=len(confirmed))
            else:
                acc.add("code_reference_count", "code_scan",
                        f"no confirmed references in {', '.join(analysis.repositories)}; not proof of non-use", D.SUPPORTS, "zero_references", 0)
            if potential:
                acc.block("potential_references", f"{len(potential)} unconfirmed reference(s) need review: "
                          + ", ".join(f"{r.location.file_path}:{r.location.line}" for r in potential), P)
            if dynamic:
                acc.block("dynamic_references", f"{len(dynamic)} dynamic reference(s) may evaluate this flag at runtime: "
                          + ", ".join(f"{r.repository}:{r.location.file_path}:{r.location.line}" for r in dynamic), P)
            if any(not b.determinable for b in analysis.branches):
                acc.uncertainty.append("some flag-controlled branches are compound/indeterminate; cleanup will need manual edits")
            value_only = refs and all(r.usage_type in (UsageType.VALUE_RETRIEVAL, UsageType.HOOK_SUBSCRIPTION) for r in refs)
            if value_only and config is not None and direction != CleanupDirection.UNDETERMINED:
                same, val = config.uniform_production_value()
                if same:
                    direction, inline_value = CleanupDirection.INLINE_VALUE, val
                else:
                    direction = CleanupDirection.UNDETERMINED
                    acc.uncertainty.append("flag is used for its value, but production values differ")
            if not refs:
                direction = CleanupDirection.ARCHIVE_ONLY if direction != CleanupDirection.UNDETERMINED else direction

        # Coverage: code we did not scan may still use the flag.
        if cov.known_external_consumers:
            acc.block("external_consumers", "known unscanned consumers: " + ", ".join(cov.known_external_consumers), P)
        if cov.all_consumers_scanned is not True:
            acc.miss("consumer_coverage", "not confirmed that every codebase consuming this flag was scanned",
                     [R] if not confirmed else [])

        # Activity / evaluation data
        act = config.activity if config else None
        if act is None or act.evaluation_count is None:
            acc.miss("evaluation_activity", "no flag evaluation analytics available", [R] if not confirmed else [])
        else:
            recent = act.last_evaluated_at is not None and (now - act.last_evaluated_at) <= timedelta(days=p.activity_window_days)
            if not confirmed and (act.evaluation_count > 0 or recent):
                acc.add("evaluation_activity", act.source,
                        f"{act.evaluation_count} evaluation(s) in the last {act.window_days} days but no references in scanned code: "
                        "an unscanned consumer is likely evaluating it", D.OPPOSES, "evaluated_by_unscanned_consumer", act.evaluation_count)
                acc.block("unscanned_consumer_activity", "flag is evaluated by code that was not scanned", P)
            else:
                acc.add("evaluation_activity", act.source,
                        f"{act.evaluation_count} evaluation(s) in the last {act.window_days} days", D.NEUTRAL, value=act.evaluation_count)

        # Lifecycle
        state = lifecycle.state if lifecycle else None
        if state in ACTIVE_LIFECYCLE:
            acc.add("lifecycle_state", "lifecycle", f"lifecycle state is '{state.value}' (not released)", D.OPPOSES, "lifecycle_active", state.value)
        elif state is not None:
            acc.add("lifecycle_state", "lifecycle", f"lifecycle state is '{state.value}'", D.SUPPORTS, "lifecycle_released", state.value)
        if direction == CleanupDirection.UNDETERMINED and config is not None and not hard_not_stale:
            acc.block("undetermined_direction", "cannot determine which code path to keep", P)

        # ---------------- status ----------------
        score = acc.score
        if hard_not_stale:
            status = StaleStatus.NOT_STALE
        elif config is None or analysis is None or not config.production:
            status = StaleStatus.INSUFFICIENT_EVIDENCE
        elif score < p.potential_threshold:
            status = StaleStatus.NOT_STALE
        else:
            status = StaleStatus.POTENTIALLY_STALE
            ready_blocked = [m for m in acc.missing if R.value in m.blocks]
            if not ready_blocked:
                status = StaleStatus.READY_FOR_CLEANUP
                if self._cleanup_completed(lifecycle) and not refs and not dynamic:
                    status = StaleStatus.SAFE_TO_RETIRE
        caps = [b.caps_status_at for b in acc.blockers]
        if caps and _rank(status) > min(_rank(c) for c in caps):
            status = STATUS_ORDER[min(_rank(c) for c in caps)]

        n_missing = len(acc.missing)
        if status == StaleStatus.INSUFFICIENT_EVIDENCE:
            confidence = 0.2
        else:
            confidence = max(0.05, min(0.95, (0.5 + min(abs(score), 1.0) / 2) * (1 - 0.08 * n_missing) * (1 - 0.1 * len(acc.blockers))))

        return StaleFlagFinding(
            id=stable_id("stale", flag_name, p.id, p.version, status.value, score, now.date().isoformat()),
            flag_id=config.flag_id if config else flag_name,
            flag_name=flag_name,
            status=status,
            confidence=round(confidence, 2),
            score=score,
            cleanup_direction=direction,
            inline_value=inline_value,
            lifecycle_state=state,
            evidence=acc.items,
            missing_evidence=acc.missing,
            blockers=acc.blockers,
            uncertainty=acc.uncertainty,
            recommended_next_action=_next_action(status, acc),
            policy_id=p.id,
            policy_version=p.version,
            evaluated_at=now,
        )

    @staticmethod
    def _cleanup_completed(lifecycle: FlagLifecycle | None) -> bool:
        if lifecycle is None or lifecycle.state != LifecycleState.CLEANUP_IN_PROGRESS:
            return False
        kinds = {e.kind for rec in lifecycle.history for e in rec.evidence if e.reference}
        return EvidenceKind.CLEANUP_VERIFIED in kinds and EvidenceKind.CLEANUP_MERGED in kinds


def _next_action(status: StaleStatus, acc: _Acc) -> str:
    if status == StaleStatus.NOT_STALE:
        return "No cleanup action. Re-evaluate later or when configuration changes."
    if status == StaleStatus.INSUFFICIENT_EVIDENCE:
        return "Collect missing evidence: " + "; ".join(m.description for m in acc.missing)
    if status == StaleStatus.POTENTIALLY_STALE:
        todo = [b.description for b in acc.blockers] + [m.description for m in acc.missing if StaleStatus.READY_FOR_CLEANUP.value in m.blocks]
        return "Ask the flag owner to review. Before cleanup: " + ("; ".join(todo) if todo else "confirm the flag is no longer needed")
    if status == StaleStatus.READY_FOR_CLEANUP:
        return "Generate a cleanup plan and patch for human review; do not archive the flag until cleanup is merged and verified."
    return "Cleanup is merged and verified. Archiving in Flagsmith still requires explicit owner and reviewer approval."
