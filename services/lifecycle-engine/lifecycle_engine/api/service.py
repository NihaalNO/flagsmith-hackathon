"""`EngineService`: the callable interface the shared backend uses.

Framework-free; `router.py` exposes it over FastAPI. The service is stateless:
lifecycle records and findings are returned for the backend to persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..agents.llm import LLMProvider
from ..agents.planner import CleanupPlanner
from ..analyzers.code_intelligence import CodeIntelligence
from ..cleanup.patcher import PatchGenerator
from ..cleanup.verifier import CleanupVerifier
from ..discovery.scanner import CodebaseSnapshot
from ..policies.stale_detector import StaleFlagDetector
from ..schemas.analysis import FlagAnalysisReport
from ..schemas.api import (
    AnalyzeFlagRequest,
    CleanupPlanRequest,
    CleanupPlanResponse,
    DetectStaleRequest,
    DiscoverRequest,
    EvaluateLifecycleRequest,
    GeneratePatchRequest,
    StaleDetectionResponse,
    TransitionRequest,
    VerifyCleanupRequest,
)
from ..schemas.discovery import RepositoryTarget, ScanResult
from ..schemas.flags import ConsumerCoverage
from ..schemas.lifecycle import FlagLifecycle, LifecycleEvaluation
from ..schemas.patch import CleanupPatch
from ..schemas.stale import STATUS_ORDER, CleanupDirection, StaleStatus
from ..schemas.verification import CommandSpec, VerificationResult
from ..state_machine.evaluator import evaluate_lifecycle
from ..state_machine.machine import LifecycleStateMachine
from .errors import CommandsNotAllowed, MissingInput, PathNotAllowed, RepositoryNotFound


@dataclass
class EngineSettings:
    # Repository checkouts must live under one of these. Empty = any path (development only).
    allowed_roots: list[Path] = field(default_factory=list)
    # HTTP callers may not supply commands to execute unless an operator enables this.
    allow_request_commands: bool = False
    # Operator-configured test/static commands, run during verification.
    server_commands: list[CommandSpec] = field(default_factory=list)


class EngineService:
    def __init__(self, settings: EngineSettings | None = None, llm: LLMProvider | None = None):
        self.settings = settings or EngineSettings()
        self.llm = llm
        self.machine = LifecycleStateMachine()

    # -- helpers --------------------------------------------------------------
    def _targets(self, targets: list[RepositoryTarget]) -> list[RepositoryTarget]:
        for t in targets:
            root = Path(t.root_path).resolve()
            if not root.is_dir():
                raise RepositoryNotFound(f"repository checkout not found for '{t.repository}'", repository=t.repository)
            if self.settings.allowed_roots and not any(root.is_relative_to(a.resolve()) for a in self.settings.allowed_roots):
                raise PathNotAllowed(f"repository path for '{t.repository}' is outside the allowed roots", repository=t.repository)
        return targets

    def _analysis(self, targets, flag_name, config) -> tuple[CodebaseSnapshot, FlagAnalysisReport]:
        snap = CodebaseSnapshot(self._targets(targets))
        return snap, CodeIntelligence(snap).analyze(flag_name, config)

    @staticmethod
    def _coverage(req_cov: ConsumerCoverage | None, targets) -> ConsumerCoverage:
        return req_cov or ConsumerCoverage(scanned_repositories=[t.repository for t in targets])

    # -- operations -------------------------------------------------------------
    def discover(self, req: DiscoverRequest) -> ScanResult:
        return CodebaseSnapshot(self._targets(req.repositories)).scan

    def analyze_flag(self, req: AnalyzeFlagRequest) -> FlagAnalysisReport:
        return self._analysis(req.repositories, req.flag_name, req.configuration)[1]

    def evaluate_lifecycle(self, req: EvaluateLifecycleRequest) -> LifecycleEvaluation:
        _, analysis = self._analysis(req.repositories, req.flag_name, req.configuration)
        detector = StaleFlagDetector(req.policy)
        stale = detector.evaluate(req.flag_name, config=req.configuration, analysis=analysis, lifecycle=req.lifecycle,
                                  coverage=self._coverage(req.coverage, req.repositories), now=req.as_of)
        return evaluate_lifecycle(req.flag_name, lifecycle=req.lifecycle, config=req.configuration, analysis=analysis, stale=stale,
                                  stable_config_days=detector.policy.stable_config_days, machine=self.machine, now=req.as_of)

    def transition(self, req: TransitionRequest) -> FlagLifecycle:
        if req.override:
            return self.machine.override(req.lifecycle, req.to_state, actor=req.actor, justification=req.reason,
                                         approvals=req.approvals, evidence=req.evidence)
        return self.machine.transition(req.lifecycle, req.to_state, actor=req.actor, reason=req.reason,
                                       evidence=req.evidence, approvals=req.approvals)

    def detect_stale(self, req: DetectStaleRequest) -> StaleDetectionResponse:
        snap = CodebaseSnapshot(self._targets(req.repositories))
        ci = CodeIntelligence(snap)
        configs = {c.flag_name: c for c in req.configurations}
        names = set(configs)
        if req.include_unconfigured_code_flags:
            names |= {r.flag_name for r in snap.scan.references if r.flag_name}
        detector = StaleFlagDetector(req.policy)
        cov = self._coverage(req.coverage, req.repositories)
        findings = [detector.evaluate(n, config=configs.get(n), analysis=ci.analyze(n, configs.get(n)),
                                      lifecycle=req.lifecycles.get(n), coverage=cov, now=req.as_of) for n in sorted(names)]
        findings.sort(key=lambda f: (-STATUS_ORDER.index(f.status), -f.confidence, f.flag_name))
        counts = {s: sum(f.status == s for f in findings) for s in StaleStatus}
        return StaleDetectionResponse(scan_id=snap.scan.scan_id, findings=findings, counts=counts)

    def cleanup_plan(self, req: CleanupPlanRequest) -> CleanupPlanResponse:
        _, analysis = self._analysis(req.repositories, req.flag_name, req.configuration)
        stale = StaleFlagDetector(req.policy).evaluate(
            req.flag_name, config=req.configuration, analysis=analysis, lifecycle=req.lifecycle,
            coverage=self._coverage(req.coverage, req.repositories), now=req.as_of)
        patch = None
        if req.include_patch and stale.status in (StaleStatus.READY_FOR_CLEANUP, StaleStatus.SAFE_TO_RETIRE) \
                and stale.cleanup_direction not in (CleanupDirection.UNDETERMINED, CleanupDirection.ARCHIVE_ONLY):
            patch = PatchGenerator(req.repositories).generate(
                req.flag_name, stale.cleanup_direction, inline_value=stale.inline_value,
                enabled_state=req.configuration.uniform_production_state() if req.configuration else None)
        planner = CleanupPlanner(self.llm if req.use_llm else None)
        plan = planner.plan(analysis=analysis, stale=stale, patch=patch,
                            lifecycle_state=req.lifecycle.state if req.lifecycle else None,
                            archived=bool(req.configuration and req.configuration.is_archived))
        return CleanupPlanResponse(plan=plan, stale_finding=stale, analysis=analysis, patch=patch)

    def generate_patch(self, req: GeneratePatchRequest) -> CleanupPatch:
        targets = self._targets(req.repositories)
        direction, inline, enabled = req.direction, req.inline_value, None
        if req.configuration is not None:
            enabled = req.configuration.uniform_production_state()
            if direction is None:
                direction = {True: CleanupDirection.KEEP_ENABLED_PATH, False: CleanupDirection.KEEP_DISABLED_PATH}.get(enabled)
        if direction is None:
            raise MissingInput("cleanup direction is required (or a configuration with a uniform production state)")
        if direction == CleanupDirection.INLINE_VALUE and inline is None and req.configuration is not None:
            same, inline = req.configuration.uniform_production_value()
            if not same:
                raise MissingInput("production values differ; provide inline_value explicitly")
        return PatchGenerator(targets).generate(req.flag_name, direction, inline_value=inline, enabled_state=enabled)

    def verify_cleanup(self, req: VerifyCleanupRequest) -> VerificationResult:
        targets = self._targets(req.repositories)
        if req.commands and not self.settings.allow_request_commands:
            raise CommandsNotAllowed("request-supplied commands are disabled; configure server_commands instead")
        commands = [*self.settings.server_commands, *(req.commands if self.settings.allow_request_commands else [])]
        verifier = CleanupVerifier(targets, commands=commands, auto_detect_tests=req.auto_detect_tests,
                                   require_tests=req.require_tests, dynamic_reference_reviews=req.dynamic_reference_reviews)
        return verifier.verify(req.patch)
