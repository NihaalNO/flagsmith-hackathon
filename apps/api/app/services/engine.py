"""Bridge to the lifecycle intelligence engine (services/lifecycle-engine).

The engine is imported lazily so the API still starts if the package is missing
(e.g. a deployment that did not install it); only the analysis endpoints then
return 503. The engine is stateless: this module feeds it what the backend knows
and returns plain JSON for storage.

What the backend can supply today is limited (one synced environment's on/off
state, no Flagsmith change history, analytics, tags or owner confirmations), so
stale detection will usually report missing evidence rather than recommend
cleanup. That is intended: the engine never treats missing evidence as safety.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.models.flag import Flag


class EngineUnavailable(RuntimeError):
    pass


def _engine():
    try:
        import lifecycle_engine  # noqa: F401
        from lifecycle_engine.agents import llm as engine_llm
        from lifecycle_engine.agents.planner import CleanupPlanner
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
                            f"'{settings.flagpilot_llm_provider}' (openai-compatible, anthropic, claude-code, mock)")


@dataclass
class RepoSnapshot:
    full_name: str
    commit_sha: str
    tarball: bytes


def flag_configuration(flag: Flag) -> Any:
    """Map a backend Flag row to the engine's FlagConfiguration, using only data we have."""
    F = _engine()["F"]
    env_name = flag.environment or "unknown"
    environments = []
    if flag.environment:
        environments.append(F.EnvironmentFlagState(
            environment=env_name,
            is_production=env_name.lower() in settings.production_environment_list,
            enabled=bool(flag.enabled),
            updated_at=flag.updated_at,
        ))
    return F.FlagConfiguration(
        flag_name=flag.name,
        project=flag.project_name or (str(flag.flagsmith_project_id) if flag.flagsmith_project_id is not None else "default"),
        flagsmith_feature_id=int(flag.flagsmith_id) if flag.flagsmith_id and flag.flagsmith_id.isdigit() else None,
        description=flag.description,
        # Backend row creation time: Flagsmith's own creation date is not synced yet.
        created_at=flag.created_at,
        owners=[flag.owner] if flag.owner else [],
        environments=environments,
        data_source="flagpilot-backend",
        fetched_at=flag.updated_at,
    )


def analyze_flag(flag: Flag, repos: list[RepoSnapshot], *, use_llm: bool = True) -> dict:
    """Run discovery, code intelligence, stale detection, lifecycle evaluation and planning
    for one flag over the given repository snapshots. Returns JSON-ready data."""
    E = _engine()
    config = flag_configuration(flag)
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
        planner = E["CleanupPlanner"](llm_provider() if use_llm else None)
        plan = planner.plan(analysis=analysis, stale=stale, patch=patch, lifecycle_state=lifecycle.state)
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
        "scan_errors": scan_errors,
    }
