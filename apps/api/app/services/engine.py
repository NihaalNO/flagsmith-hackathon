"""Bridge to the lifecycle intelligence engine (services/lifecycle-engine).

The engine is imported lazily so the API still starts if the package is missing
(e.g. a deployment that did not install it); only the analysis endpoints then
return 503. The engine is stateless: this module feeds it what the backend knows
and returns plain JSON for storage.

The Flagsmith sync supplies every environment's state (overrides, splits, last
modified), creation date, tags, owners and archived status. Still missing: Flagsmith
change history, evaluation analytics and owner confirmations, so stale detection
reports those as missing evidence; it never treats missing evidence as safety.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.models.flag import Flag
from app.models.flag_environment_state import FlagEnvironmentState


class EngineUnavailable(RuntimeError):
    pass


def _engine():
    try:
        import lifecycle_engine  # noqa: F401
        from lifecycle_engine.agents import llm as engine_llm
        from lifecycle_engine.agents.planner import CleanupPlanner
        from lifecycle_engine.agents.verdict import assess_flag
        from lifecycle_engine.experiments.stats import analyze_experiment
        from lifecycle_engine.schemas import experiments as X
        from lifecycle_engine.schemas.verdict import ExperimentStatusInput
        from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
        from lifecycle_engine.cleanup.patcher import PatchGenerator
        from lifecycle_engine.discovery.scanner import CodebaseSnapshot
        from lifecycle_engine.discovery.sources import tarball_checkout
        from lifecycle_engine.policies.stale_detector import StaleFlagDetector
        from lifecycle_engine.schemas import flags as F
        from lifecycle_engine.schemas.lifecycle import FlagLifecycle, LifecycleState
        from lifecycle_engine.schemas.stale import CleanupDirection, StaleStatus
        from lifecycle_engine.state_machine.evaluator import evaluate_lifecycle
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
        raise EngineUnavailable(
            "lifecycle engine is not installed (pip install -r requirements-engine.txt from apps/api)") from exc
    return {
        "version": lifecycle_engine.ENGINE_VERSION, "llm": engine_llm,
        "CleanupPlanner": CleanupPlanner, "CodeIntelligence": CodeIntelligence, "PatchGenerator": PatchGenerator,
        "CodebaseSnapshot": CodebaseSnapshot, "tarball_checkout": tarball_checkout,
        "StaleFlagDetector": StaleFlagDetector, "F": F, "FlagLifecycle": FlagLifecycle,
        "LifecycleState": LifecycleState, "CleanupDirection": CleanupDirection, "StaleStatus": StaleStatus,
        "evaluate_lifecycle": evaluate_lifecycle,
        "assess_flag": assess_flag, "analyze_experiment": analyze_experiment, "X": X,
        "ExperimentStatusInput": ExperimentStatusInput,
    }


def engine_version() -> str:
    return _engine()["version"]


def llm_provider():
    """The configured LLM provider for plan narratives (settings.flagpilot_llm_provider), or None."""
    llm = _engine()["llm"]
    kind = settings.flagpilot_llm_provider.strip().lower()
    model = settings.flagpilot_llm_model.strip() or None
    if not kind:
        return None
    if kind == "anthropic":
        return llm.AnthropicProvider(model=model or "claude-opus-5-5")
    if kind == "claude-code":
        return llm.ClaudeCodeProvider(model=model)
    if kind == "mock":
        return llm.MockLLMProvider()
    if kind == "groq":
        try:
            return llm.GroqProvider(
                model=model,
                api_key=settings.flagpilot_llm_api_key.strip() or settings.groq_api_key.strip() or None,
                base_url=settings.flagpilot_llm_base_url.strip() or None,
                json_mode=settings.flagpilot_llm_json_mode.strip().lower(),
            )
        except ValueError as exc:
            raise EngineUnavailable(f"LLM provider misconfigured: {exc}") from exc
    if kind == "openai-compatible":
        try:
            return llm.OpenAICompatibleProvider(
                model=model or "",
                base_url=settings.flagpilot_llm_base_url.strip(),
                api_key=settings.flagpilot_llm_api_key.strip() or None,
                json_mode=settings.flagpilot_llm_json_mode.strip().lower(),
            )
        except ValueError as exc:
            raise EngineUnavailable(f"LLM provider misconfigured: {exc}") from exc
    raise EngineUnavailable(f"LLM provider misconfigured: unknown FLAGPILOT_LLM_PROVIDER "
                            f"'{settings.flagpilot_llm_provider}' (groq, openai-compatible, anthropic, claude-code, mock)")


@dataclass
class RepoSnapshot:
    full_name: str
    commit_sha: str
    tarball: bytes


def _engine_value(value):
    """Engine values are scalars; structured remote-config values are passed as JSON text."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True)


def flag_configuration(flag: Flag, states: list[FlagEnvironmentState] | None = None) -> tuple[Any, list[str]]:
    """Map a backend Flag (and its synced environment states) to the engine's FlagConfiguration.

    Returns the configuration and notes on data that is approximated or missing, so the
    analysis result says what the evidence is based on."""
    F = _engine()["F"]
    notes: list[str] = []
    prod_names = settings.production_environment_list
    environments = []
    if states:
        for st in sorted(states, key=lambda x: x.environment_name.lower()):
            if st.identity_overrides is None:
                notes.append(f"identity overrides in '{st.environment_name}' are not reported by Flagsmith "
                             "(Edge project); treated as none")
            environments.append(F.EnvironmentFlagState(
                environment=st.environment_name,
                is_production=st.environment_name.lower() in prod_names,
                enabled=bool(st.enabled),
                value=_engine_value(st.value),
                segment_overrides=st.segment_overrides or 0,
                identity_overrides=st.identity_overrides or 0,
                percentage_allocation=st.percentage_allocation,
                updated_at=st.last_modified_at,
            ))
        if not any(e.is_production for e in environments):
            notes.append("no synced environment matches PRODUCTION_ENVIRONMENT_NAMES "
                         f"({', '.join(sorted(prod_names))}); production state is unknown")
    elif flag.environment:
        notes.append("per-environment state not synced yet; using the single mirrored environment "
                     "(run the Flagsmith sync)")
        environments.append(F.EnvironmentFlagState(
            environment=flag.environment,
            is_production=flag.environment.lower() in prod_names,
            enabled=bool(flag.enabled),
            updated_at=flag.updated_at,
        ))
    else:
        notes.append("no Flagsmith environment state available for this flag")
    if flag.flagsmith_created_at is None:
        notes.append("Flagsmith creation date unknown; using the FlagPilot record's creation date")
    owners = ([flag.owner] if flag.owner else []) + [f"flagsmith-user:{i}" for i in (flag.flagsmith_owner_ids or [])]
    feature_type = F.FeatureType.MULTIVARIATE if (flag.flagsmith_type or "").upper() == "MULTIVARIATE" else F.FeatureType.STANDARD
    notes.append("Flagsmith change history and evaluation analytics are not synced yet")
    config = F.FlagConfiguration(
        flag_name=flag.name,
        project=flag.project_name or (str(flag.flagsmith_project_id) if flag.flagsmith_project_id is not None else "default"),
        flagsmith_feature_id=int(flag.flagsmith_id) if flag.flagsmith_id and flag.flagsmith_id.isdigit() else None,
        description=flag.description,
        created_at=flag.flagsmith_created_at or flag.created_at,
        owners=owners,
        tags=list(flag.flagsmith_tags or []),
        is_archived=bool(flag.is_archived_in_flagsmith),
        feature_type=feature_type,
        environments=environments,
        data_source="flagsmith-sync" if states else "flagpilot-backend",
        fetched_at=max((st.synced_at for st in states), default=None) if states else flag.updated_at,
    )
    return config, notes


def analyze_flag(flag: Flag, repos: list[RepoSnapshot], *, states: list[FlagEnvironmentState] | None = None,
                 experiments: list[dict] | None = None, use_llm: bool = True) -> dict:
    """Run discovery, code intelligence, stale detection, lifecycle evaluation and planning
    for one flag over the given repository snapshots. Returns JSON-ready data."""
    E = _engine()
    config, data_notes = flag_configuration(flag, states)
    lifecycle = E["FlagLifecycle"](
        flag_id=flag.id, flag_name=flag.name, state=E["LifecycleState"](flag.lifecycle_status),
        created_at=flag.created_at, updated_at=flag.lifecycle_changed_at)
    with ExitStack() as stack:
        targets = [stack.enter_context(E["tarball_checkout"](r.full_name, r.tarball)) for r in repos]
        snapshot = E["CodebaseSnapshot"](targets)
        analysis = E["CodeIntelligence"](snapshot).analyze(flag.name, config)
        detector = E["StaleFlagDetector"]()
        coverage = E["F"].ConsumerCoverage(scanned_repositories=[r.full_name for r in repos])
        stale = detector.evaluate(flag.name, config=config, analysis=analysis, lifecycle=lifecycle, coverage=coverage)
        evaluation = E["evaluate_lifecycle"](flag.name, lifecycle=lifecycle, config=config, analysis=analysis, stale=stale,
                                             stable_config_days=detector.policy.stable_config_days)
        patch = None
        S, D = E["StaleStatus"], E["CleanupDirection"]
        if stale.status in (S.READY_FOR_CLEANUP, S.SAFE_TO_RETIRE) and stale.cleanup_direction not in (D.UNDETERMINED, D.ARCHIVE_ONLY):
            patch = E["PatchGenerator"](targets).generate(
                flag.name, stale.cleanup_direction, inline_value=stale.inline_value,
                enabled_state=config.uniform_production_state())
        provider = llm_provider() if use_llm else None
        planner = E["CleanupPlanner"](provider)
        plan = planner.plan(analysis=analysis, stale=stale, patch=patch, lifecycle_state=lifecycle.state)
        verdict = E["assess_flag"](
            config=config, analysis=analysis, stale=stale, llm=provider, policy=detector.policy,
            experiments=[E["ExperimentStatusInput"](name=x["name"], status=x["status"], decision=x.get("decision"))
                         for x in (experiments or [])])
        scan_errors = [e.model_dump(mode="json") for e in snapshot.scan.errors]

    return {
        "engine_version": E["version"],
        "repositories": [{"full_name": r.full_name, "commit_sha": r.commit_sha} for r in repos],
        "flag_configuration": config.model_dump(mode="json"),
        "analysis": analysis.model_dump(mode="json"),
        "stale_finding": stale.model_dump(mode="json"),
        "lifecycle_evaluation": evaluation.model_dump(mode="json"),
        "cleanup_plan": plan.model_dump(mode="json"),
        "patch": patch.model_dump(mode="json") if patch is not None else None,
        "verdict": verdict.model_dump(mode="json"),
        "scan_errors": scan_errors,
        "data_notes": data_notes,
    }


def experiment_results(variants: list[dict], counts: dict[str, tuple[int, int]], *, minimum_detectable_effect: float,
                       alpha: float) -> dict:
    """Run the engine's A/B statistics. `counts` maps variant key -> (exposures, conversions)."""
    E = _engine()
    X = E["X"]
    inputs = [X.VariantInput(key=v["key"], is_control=bool(v.get("is_control")), expected_share=v.get("expected_share"),
                             exposures=counts.get(v["key"], (0, 0))[0], conversions=counts.get(v["key"], (0, 0))[1])
              for v in variants]
    design = X.ExperimentDesign(minimum_detectable_effect=minimum_detectable_effect, alpha=alpha)
    return E["analyze_experiment"](inputs, design).model_dump(mode="json")
