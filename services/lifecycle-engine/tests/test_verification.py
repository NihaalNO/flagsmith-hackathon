from __future__ import annotations

import shutil
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from lifecycle_engine.cleanup.patcher import PatchGenerator
from lifecycle_engine.cleanup.verifier import CleanupVerifier, run_command
from lifecycle_engine.discovery.scanner import scan_repositories
from lifecycle_engine.schemas.discovery import RepositoryTarget
from lifecycle_engine.schemas.flags import DynamicReferenceReview
from lifecycle_engine.schemas.verification import (
    CheckStatus,
    CommandResult,
    CommandSpec,
    VerificationResult,
    VerificationStatus,
)
from lifecycle_engine.schemas.stale import CleanupDirection as D

SRC = {"src/a.ts": """
    import flagsmith from "flagsmith";
    export function f() {
      if (flagsmith.hasFeature("x")) {
        return 1;
      }
      return 2;
    }
"""}


def fake_runner(status=CheckStatus.PASSED, seen=None):
    def run(spec: CommandSpec, cwd):
        if seen is not None:
            seen.append((spec, cwd, (cwd / "src/a.ts").read_text() if (cwd / "src/a.ts").exists() else None))
        return CommandResult(name=spec.name, kind=spec.kind, repository=spec.repository, argv=spec.argv, status=status,
                             exit_code=0 if status == CheckStatus.PASSED else 1)
    return run


TESTS = [CommandSpec(name="unit", kind="tests", repository="t/r", argv=["fake"])]


@pytest.fixture
def setup(make_repo):
    def _s(files=SRC):
        root = make_repo(files)
        targets = [RepositoryTarget(repository="t/r", root_path=str(root))]
        return root, targets, PatchGenerator(targets).generate("x", D.KEEP_ENABLED_PATH)
    return _s


def test_patch_applied_only_in_isolated_workspace(setup):
    root, targets, patch = setup()
    original = (root / "src/a.ts").read_bytes()
    seen = []
    v = CleanupVerifier(targets, commands=TESTS, auto_detect_tests=False, runner=fake_runner(seen=seen)).verify(patch)
    assert v.status == VerificationStatus.READY_FOR_REVIEW and v.ready_for_human_review
    spec, cwd, content_in_ws = seen[0]
    assert "hasFeature" not in content_in_ws            # tests ran against the patched copy
    assert not str(cwd).startswith(str(root))
    assert (root / "src/a.ts").read_bytes() == original  # source repo untouched
    assert not cwd.exists()                              # workspace cleaned up
    assert v.remaining_references == []
    assert "the Flagsmith flag was not modified or archived" in v.actions_not_taken


def test_failed_tests_fail_verification(setup):
    _, targets, patch = setup()
    v = CleanupVerifier(targets, commands=TESTS, auto_detect_tests=False, runner=fake_runner(CheckStatus.FAILED)).verify(patch)
    assert v.status == VerificationStatus.FAILED and not v.ready_for_human_review
    assert v.test_results[0].status == CheckStatus.FAILED


def test_command_that_cannot_run_is_an_error_not_a_pass(setup):
    _, targets, patch = setup()
    cmd = [CommandSpec(name="missing", kind="tests", repository="t/r", argv=["definitely-not-a-real-binary-xyz"])]
    v = CleanupVerifier(targets, commands=cmd, auto_detect_tests=False).verify(patch)
    assert v.test_results[0].status == CheckStatus.ERROR and "not found" in v.test_results[0].error
    assert v.status == VerificationStatus.FAILED


def test_failed_verification_cannot_be_marked_successful():
    base = dict(id="v", patch_id="p", flag_name="x", files_changed=[], patch_applied=True,
                test_results=[CommandResult(name="t", kind="tests", repository="r", argv=["t"], status=CheckStatus.FAILED)])
    with pytest.raises(ValidationError):
        VerificationResult(**base, status=VerificationStatus.READY_FOR_REVIEW, ready_for_human_review=True)
    with pytest.raises(ValidationError):
        VerificationResult(**base, status=VerificationStatus.INCOMPLETE, ready_for_human_review=False)
    with pytest.raises(ValidationError):
        VerificationResult(**{**base, "test_results": [], "patch_applied": False}, status=VerificationStatus.READY_FOR_REVIEW,
                           ready_for_human_review=True)
    ok = VerificationResult(**base, status=VerificationStatus.FAILED, ready_for_human_review=False)
    assert not ok.ready_for_human_review


def test_no_tests_means_incomplete(setup):
    _, targets, patch = setup()
    v = CleanupVerifier(targets, auto_detect_tests=False).verify(patch)
    assert v.status == VerificationStatus.INCOMPLETE
    assert "no tests were executed" in " ".join(v.unresolved_risks)


def test_stale_patch_is_rejected(setup):
    root, targets, patch = setup()
    (root / "src/a.ts").write_text("// edited after patch generation\n", encoding="utf-8")
    v = CleanupVerifier(targets, commands=TESTS, auto_detect_tests=False, runner=fake_runner()).verify(patch)
    assert not v.patch_applied and "sha256 mismatch" in v.apply_errors[0]
    assert v.status == VerificationStatus.FAILED and v.test_results == []


def test_tampered_patch_with_syntax_error_fails(setup):
    _, targets, patch = setup()
    broken = patch.model_copy(update={"files": [patch.files[0].model_copy(update={"patched_content": "export function f( {\n"})]})
    v = CleanupVerifier(targets, commands=TESTS, auto_detect_tests=False, runner=fake_runner()).verify(broken)
    assert v.status == VerificationStatus.FAILED
    assert v.static_findings and v.static_findings[0].check == "syntax"


def test_path_traversal_rejected(setup):
    _, targets, patch = setup()
    evil = patch.model_copy(update={"files": [patch.files[0].model_copy(update={"file_path": "../../outside.ts"})]})
    v = CleanupVerifier(targets, commands=TESTS, auto_detect_tests=False, runner=fake_runner()).verify(evil)
    assert not v.patch_applied and any("escapes" in e or "does not exist" in e for e in v.apply_errors)


def test_remaining_and_dynamic_references_detected(setup):
    files = {**SRC, "src/b.js": 'export function g() { if (window.flagsmith.hasFeature("x")) { h(); } }\n',
             "src/dyn.ts": 'import f from "flagsmith";\nexport const on = (n: string) => f.hasFeature(n);\n'}
    _, targets, patch = setup(files)
    v = CleanupVerifier(targets, commands=TESTS, auto_detect_tests=False, runner=fake_runner()).verify(patch)
    assert v.status == VerificationStatus.INCOMPLETE
    assert [r.location.file_path for r in v.remaining_references] == ["src/b.js"]
    assert [r.location.file_path for r in v.unresolved_dynamic_references] == ["src/dyn.ts"]


def test_empty_patch_cannot_verify(setup):
    _, targets, _ = setup()
    empty = PatchGenerator(targets).generate("x", D.UNDETERMINED)
    v = CleanupVerifier(targets, auto_detect_tests=False).verify(empty)
    assert v.status == VerificationStatus.FAILED and not v.patch_applied


# --- integration: real sample repositories and Node test runner -------------------

needs_node = pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None, reason="node/npm not on PATH")


def _reviews(targets):
    return [DynamicReferenceReview(reference_id=d.id, reviewed_by="lead", reviewed_at=datetime.now(timezone.utc),
                                   excluded_flags=["new_checkout"]) for d in scan_repositories(targets).dynamic()]


@pytest.mark.node
@needs_node
def test_sample_cleanup_verifies_with_real_tests(sample_targets):
    patch = PatchGenerator(sample_targets).generate("new_checkout", D.KEEP_ENABLED_PATH)
    v = CleanupVerifier(sample_targets, dynamic_reference_reviews=_reviews(sample_targets)).verify(patch)
    assert [r.status for r in v.test_results] == [CheckStatus.PASSED, CheckStatus.PASSED], [r.output_tail for r in v.test_results]
    assert v.status == VerificationStatus.READY_FOR_REVIEW


@pytest.mark.node
@needs_node
def test_sample_cleanup_fails_when_a_test_exercises_the_removed_path(sample_copy, sample_targets):
    web = sample_copy("checkout-web")
    (web / "src/lib/legacy.test.ts").write_text(
        'import { test } from "node:test";\nimport assert from "node:assert/strict";\nimport { cartTotal } from "./cart.ts";\n'
        'const off = { hasFeature: () => false } as any;\n'
        'test("legacy rounding", () => { assert.equal(cartTotal(off, [{ price: 1.25, quantity: 2 }]), 3); });\n',
        encoding="utf-8")
    targets = [RepositoryTarget(repository="acme/checkout-web", root_path=str(web))]
    patch = PatchGenerator(targets).generate("new_checkout", D.KEEP_ENABLED_PATH)
    v = CleanupVerifier(targets, dynamic_reference_reviews=_reviews(targets)).verify(patch)
    assert v.status == VerificationStatus.FAILED
    assert v.test_results[0].status == CheckStatus.FAILED and "legacy rounding" in v.test_results[0].output_tail
