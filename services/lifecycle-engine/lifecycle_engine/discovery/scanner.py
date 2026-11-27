"""Repository scanner: walks local checkouts and produces a validated `ScanResult`."""

from __future__ import annotations

import os
from pathlib import Path

from .. import ENGINE_VERSION
from ..schemas.common import stable_id
from ..schemas.discovery import (
    FileScanError,
    FlagReference,
    FlagSummary,
    ReferenceClassification,
    RepositoryTarget,
    ScanResult,
    ScanSummary,
)
from .ecmascript import ModuleInfo, RepoIndex, extract_references
from .languages import language_for_path, parse

DEFAULT_IGNORED_DIRS = frozenset(
    {"node_modules", ".git", "dist", "build", "coverage", ".next", "out", ".turbo", ".cache", "vendor", ".venv", "__pycache__"}
)
MAX_FILE_BYTES = 1_000_000


def iter_source_files(root: Path, ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ignored_dirs)
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            if language_for_path(rel) is not None:
                yield full, rel


def build_index(target: RepositoryTarget, errors: list[FileScanError] | None = None,
                overrides: dict[str, bytes] | None = None) -> RepoIndex:
    """Parse a repository. `overrides` maps relative paths to in-memory content
    (used to analyze proposed patches without touching the working tree)."""
    root = Path(target.root_path)
    overrides = overrides or {}
    if not root.is_dir():
        raise FileNotFoundError(f"repository root does not exist: {root}")
    modules: list[ModuleInfo] = []
    for full, rel in iter_source_files(root):
        lang = language_for_path(rel)
        try:
            if full.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"file larger than {MAX_FILE_BYTES} bytes; skipped")
            src = overrides[rel] if rel in overrides else full.read_bytes()
            tree = parse(src, lang)
        except Exception as exc:  # unreadable file must not abort the scan
            if errors is not None:
                errors.append(FileScanError(repository=target.repository, file_path=rel, error=str(exc)))
            continue
        if tree.root_node.has_error and errors is not None:
            errors.append(FileScanError(repository=target.repository, file_path=rel,
                                        error="syntax errors present; references in malformed regions may be missed"))
        modules.append(ModuleInfo(target.repository, rel, lang, src, tree))
    return RepoIndex(target.repository, modules)


def summarize(refs: list[FlagReference], traits: list[FlagReference]) -> ScanSummary:
    flags: dict[str, FlagSummary] = {}
    for r in refs:
        if r.flag_name is None:
            continue
        s = flags.setdefault(r.flag_name, FlagSummary(flag_name=r.flag_name))
        if r.classification == ReferenceClassification.CONFIRMED:
            s.confirmed += 1
        else:
            s.potential += 1
        if r.location.file_path not in s.files:
            s.files.append(r.location.file_path)
        if r.repository not in s.repositories:
            s.repositories.append(r.repository)
    return ScanSummary(
        confirmed=sum(r.classification == ReferenceClassification.CONFIRMED for r in refs),
        potential=sum(r.classification == ReferenceClassification.POTENTIAL for r in refs),
        dynamic=sum(r.classification == ReferenceClassification.DYNAMIC for r in refs),
        trait_reads=len(traits),
        flags=sorted(flags.values(), key=lambda s: s.flag_name),
    )


class CodebaseSnapshot:
    """Parsed repositories plus their scan result; shared by analysis and cleanup."""

    def __init__(self, targets: list[RepositoryTarget], overrides: dict[tuple[str, str], bytes] | None = None):
        self.targets = list(targets)
        overrides = overrides or {}
        self.indexes: dict[str, RepoIndex] = {}
        errors: list[FileScanError] = []
        refs: list[FlagReference] = []
        traits: list[FlagReference] = []
        files_with_refs: set[tuple[str, str]] = set()
        for t in self.targets:
            index = build_index(t, errors, {p: b for (r, p), b in overrides.items() if r == t.repository})
            self.indexes[t.repository] = index
            for m in index.modules.values():
                r, tr = extract_references(m, index)
                refs.extend(r)
                traits.extend(tr)
                if r:
                    files_with_refs.add((t.repository, m.path))
        refs.sort(key=lambda r: (r.repository, r.location.file_path, r.location.line, r.location.column))
        content_key = "|".join(r.id for r in refs)
        self.scan = ScanResult(
            scan_id=stable_id("scan", *(t.repository for t in self.targets), content_key),
            engine_version=ENGINE_VERSION,
            repositories=[t.repository for t in self.targets],
            files_scanned=sum(len(i.modules) for i in self.indexes.values()),
            files_with_references=len(files_with_refs),
            errors=errors,
            references=refs,
            trait_reads=traits,
            summary=summarize(refs, traits),
        )

    def module(self, repository: str, path: str) -> ModuleInfo | None:
        index = self.indexes.get(repository)
        return index.modules.get(path) if index else None

    def modules(self):
        for index in self.indexes.values():
            yield from index.modules.values()

    def node_for(self, ref: FlagReference):
        m = self.module(ref.repository, ref.location.file_path)
        if m is None or ref.location.start_byte is None:
            return None, None
        node = m.root.descendant_for_byte_range(ref.location.start_byte, ref.location.end_byte)
        while node is not None and not (node.start_byte == ref.location.start_byte and node.end_byte == ref.location.end_byte):
            node = node.parent
        return m, node


def scan_repositories(targets: list[RepositoryTarget]) -> ScanResult:
    return CodebaseSnapshot(targets).scan


def scan_repository(repository: str, root_path: str | os.PathLike) -> ScanResult:
    return scan_repositories([RepositoryTarget(repository=repository, root_path=str(root_path))])
