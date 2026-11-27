"""Module 6 — verify a suggested patch in an isolated temporary workspace.

The source repositories are copied, the patch is applied to the copy only, then
syntax checks, configured static checks and tests run, and the copy is re-scanned.
Nothing is merged, pushed, archived or written back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from ..analyzers.dynamic import matching_dynamic_references
from ..discovery import ast_utils as A
from ..discovery.languages import language_for_path, parse
from ..discovery.scanner import CodebaseSnapshot
from ..schemas.common import stable_id, utcnow
from ..schemas.discovery import RepositoryTarget
from ..schemas.flags import DynamicReferenceReview
from ..schemas.lifecycle import ApprovalRole
from ..schemas.patch import CleanupPatch, PatchStatus
from ..schemas.verification import (
    CheckStatus,
    CommandResult,
    CommandSpec,
    StaticFinding,
    VerificationResult,
    VerificationStatus,
)
from .patcher import sha256

ACTIONS_NOT_TAKEN = [
    "no changes were written to the source repositories",
    "no branch was pushed and no pull request was opened or merged",
    "the Flagsmith flag was not modified or archived",
]
DEFAULT_COPY_IGNORE = (".git",)

Runner = Callable[[CommandSpec, Path], CommandResult]


def run_command(spec: CommandSpec, cwd: Path) -> CommandResult:
    exe = shutil.which(spec.argv[0])
    base = dict(name=spec.name, kind=spec.kind, repository=spec.repository, argv=spec.argv)
    if exe is None:
        return CommandResult(**base, status=CheckStatus.ERROR, error=f"command not found: {spec.argv[0]}")
    start = time.monotonic()
    try:
        proc = subprocess.run([exe, *spec.argv[1:]], cwd=cwd, capture_output=True, text=True,
                              timeout=spec.timeout_s, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return CommandResult(**base, status=CheckStatus.ERROR, duration_s=round(time.monotonic() - start, 2),
                             error=f"timed out after {spec.timeout_s}s")
    except OSError as exc:
        return CommandResult(**base, status=CheckStatus.ERROR, error=str(exc))
    out = (proc.stdout or "") + (proc.stderr or "")
    return CommandResult(**base, status=CheckStatus.PASSED if proc.returncode == 0 else CheckStatus.FAILED,
                         exit_code=proc.returncode, duration_s=round(time.monotonic() - start, 2),
                         output_tail="\n".join(out.strip().splitlines()[-40:]))


def detect_test_commands(target: RepositoryTarget) -> list[CommandSpec]:
    """package.json with a `test` script -> `npm test`."""
    pkg = Path(target.root_path) / "package.json"
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError):
        return []
    if "test" in scripts:
        return [CommandSpec(name="npm test", kind="tests", repository=target.repository, argv=["npm", "test", "--silent"])]
    return []


def _safe_dir_name(repo: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "__" for c in repo)


class CleanupVerifier:
    def __init__(self, targets: list[RepositoryTarget], *, commands: list[CommandSpec] | None = None,
                 auto_detect_tests: bool = True, require_tests: bool = True, keep_workspace: bool = False,
                 copy_ignore: tuple[str, ...] = DEFAULT_COPY_IGNORE, runner: Runner = run_command,
                 dynamic_reference_reviews: list[DynamicReferenceReview] | None = None):
        self.targets = targets
        self.commands = list(commands or [])
        if auto_detect_tests:
            configured = {(c.repository, c.kind) for c in self.commands}
            for t in targets:
                if (t.repository, "tests") not in configured:
                    self.commands.extend(detect_test_commands(t))
        self.require_tests = require_tests
        self.keep_workspace = keep_workspace
        self.copy_ignore = copy_ignore
        self.runner = runner
        self.reviews = dynamic_reference_reviews or []

    def verify(self, patch: CleanupPatch) -> VerificationResult:
        started = utcnow()
        ws = Path(tempfile.mkdtemp(prefix="flagpilot-verify-"))
        try:
            return self._verify_in(ws, patch, started)
        finally:
            if not self.keep_workspace:
                shutil.rmtree(ws, ignore_errors=True)

    # ------------------------------------------------------------------
    def _verify_in(self, ws: Path, patch: CleanupPatch, started) -> VerificationResult:
        roots: dict[str, Path] = {}
        for t in self.targets:
            dst = ws / _safe_dir_name(t.repository)
            shutil.copytree(t.root_path, dst, ignore=shutil.ignore_patterns(*self.copy_ignore))
            roots[t.repository] = dst

        apply_errors: list[str] = []
        if not patch.files:
            apply_errors.append(f"patch has no file changes (status {patch.status.value})")
        for fc in patch.files:
            root = roots.get(fc.repository)
            if root is None:
                apply_errors.append(f"{fc.repository}: repository not part of this verification")
                continue
            dest = (root / fc.file_path).resolve()
            if not dest.is_relative_to(root.resolve()):
                apply_errors.append(f"{fc.repository}:{fc.file_path}: path escapes the repository")
                continue
            if not dest.is_file():
                apply_errors.append(f"{fc.repository}:{fc.file_path}: file does not exist")
                continue
            if sha256(dest.read_bytes()) != fc.original_sha256:
                apply_errors.append(f"{fc.repository}:{fc.file_path}: file changed since the patch was generated (sha256 mismatch)")
                continue
            dest.write_bytes(fc.patched_content.encode("utf-8"))
        applied = not apply_errors

        static_findings: list[StaticFinding] = []
        test_results: list[CommandResult] = []
        static_results: list[CommandResult] = []
        remaining, dynamic = [], []
        if applied:
            for fc in patch.files:
                path = roots[fc.repository] / fc.file_path
                lang = language_for_path(fc.file_path)
                tree = parse(path.read_bytes(), lang)
                if tree.root_node.has_error:
                    lines = sorted({n.start_point[0] + 1 for n in A.walk(tree.root_node) if n.type == "ERROR" or n.is_missing})
                    static_findings.append(StaticFinding(check="syntax", repository=fc.repository, file_path=fc.file_path,
                                                         line=lines[0] if lines else None,
                                                         message=f"syntax error(s) after patch at line(s) {lines[:10]}"))
            for spec in self.commands:
                if spec.repository not in roots:
                    continue
                res = self.runner(spec, roots[spec.repository] / spec.cwd)
                (test_results if spec.kind == "tests" else static_results).append(res)

            snap = CodebaseSnapshot([RepositoryTarget(repository=r, root_path=str(p)) for r, p in roots.items()])
            remaining = [r for r in snap.scan.references if r.flag_name == patch.flag_name]
            reviewed = {rv.reference_id for rv in self.reviews if rv.excludes(patch.flag_name)}
            # reference ids depend on location; match reviews by (repo, file, argument) for robustness
            reviewed_keys = set()
            if reviewed:
                base = CodebaseSnapshot(self.targets).scan.references
                reviewed_keys = {(r.repository, r.location.file_path, r.argument_expression) for r in base if r.id in reviewed}
            dynamic = [r for r in matching_dynamic_references(snap.scan.references, patch.flag_name)
                       if (r.repository, r.location.file_path, r.argument_expression) not in reviewed_keys]

        risks: list[str] = []
        failed_checks = [r for r in [*test_results, *static_results] if r.status in (CheckStatus.FAILED, CheckStatus.ERROR)]
        if remaining:
            risks.append(f"{len(remaining)} reference(s) to '{patch.flag_name}' remain after the patch")
        if dynamic:
            risks.append(f"{len(dynamic)} dynamic reference(s) may still evaluate '{patch.flag_name}'")
        if applied and not test_results:
            risks.append("no tests were executed for the affected repositories")
        if patch.status == PatchStatus.PARTIAL:
            risks.append(f"patch is partial: {len(patch.manual_steps)} manual step(s) outstanding")
        for f in patch.follow_ups:
            risks.append("follow-up: " + f)
        untested = sorted({fc.repository for fc in patch.files} - {r.repository for r in test_results if r.status == CheckStatus.PASSED})
        if applied and test_results and untested:
            risks.append("changed repositories without passing tests: " + ", ".join(untested))

        if not applied or static_findings or failed_checks:
            status = VerificationStatus.FAILED
        elif remaining or dynamic or patch.status == PatchStatus.PARTIAL or (self.require_tests and (not test_results or untested)):
            status = VerificationStatus.INCOMPLETE
        else:
            status = VerificationStatus.READY_FOR_REVIEW

        return VerificationResult(
            id=stable_id("verify", patch.id, status.value, started.isoformat()),
            patch_id=patch.id, flag_name=patch.flag_name, files_changed=patch.files_changed,
            patch_applied=applied, apply_errors=apply_errors, remaining_references=remaining,
            unresolved_dynamic_references=dynamic, static_findings=static_findings,
            test_results=test_results, static_results=static_results, status=status,
            ready_for_human_review=status == VerificationStatus.READY_FOR_REVIEW, unresolved_risks=risks,
            required_approvals=[ApprovalRole.CODE_REVIEWER, ApprovalRole.FLAG_OWNER],
            actions_not_taken=ACTIONS_NOT_TAKEN, workspace_path=str(ws) if self.keep_workspace else None,
            started_at=started, finished_at=utcnow(),
        )
