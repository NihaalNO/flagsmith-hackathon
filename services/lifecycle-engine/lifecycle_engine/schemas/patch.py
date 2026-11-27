"""Suggested cleanup patches (generated deterministically, applied only in isolation)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from .common import EngineModel, utcnow
from .stale import CleanupDirection


class EditKind(str, Enum):
    KEEP_BRANCH = "keep_branch"                    # if/ternary replaced by the kept branch
    REMOVE_CONDITIONAL = "remove_conditional"      # construct removed: kept side is empty
    SIMPLIFY_CONDITION = "simplify_condition"      # flag removed from a compound condition
    REPLACE_WITH_LITERAL = "replace_with_literal"  # flag check replaced by true/false where it escapes
    INLINE_VALUE = "inline_value"
    REMOVE_SUBSCRIPTION = "remove_subscription"
    REMOVE_STATEMENT = "remove_statement"
    REMOVE_UNUSED_IMPORT = "remove_unused_import"


class AppliedEdit(EngineModel):
    kind: EditKind
    file_path: str
    line: int = Field(description="Line in the file content the edit was computed against.")
    reference_id: str | None = None
    description: str
    before: str
    after: str


class FileChange(EngineModel):
    repository: str
    file_path: str
    original_sha256: str
    patched_sha256: str
    diff: str = Field(description="Unified diff for review.")
    patched_content: str
    edits: list[AppliedEdit] = Field(default_factory=list)


class ManualStep(EngineModel):
    repository: str
    file_path: str
    line: int
    reference_id: str | None = None
    reason: str


class PatchStatus(str, Enum):
    COMPLETE = "complete"          # every confirmed reference was rewritten
    PARTIAL = "partial"            # some references need manual edits
    NOT_GENERATED = "not_generated"


class CleanupPatch(EngineModel):
    id: str
    flag_name: str
    direction: CleanupDirection
    inline_value: str | int | float | bool | None = None
    status: PatchStatus
    files: list[FileChange] = Field(default_factory=list)
    manual_steps: list[ManualStep] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list, description="Suggested review items, e.g. symbols that became unused.")
    base_scan_id: str
    generated_by: str = "deterministic-transformer"
    created_at: datetime = Field(default_factory=utcnow)
    notes: list[str] = Field(default_factory=list)

    @property
    def files_changed(self) -> list[str]:
        return [f"{f.repository}:{f.file_path}" for f in self.files]
