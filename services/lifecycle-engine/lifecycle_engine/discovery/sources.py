"""Repository sources other than a local checkout.

The backend (apps/api) downloads GitHub tarballs (`<owner>-<repo>-<sha>/...` inside a
.tar.gz). `materialize_tarball` extracts one safely into a directory so every engine
operation can run on it; `scan_github_tarball` is the one-call discovery helper.
"""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

from ..schemas.discovery import RepositoryTarget, ScanResult
from .languages import language_for_path
from .scanner import DEFAULT_IGNORED_DIRS, MAX_FILE_BYTES, CodebaseSnapshot

MAX_TOTAL_BYTES = 500_000_000


class UnsafeArchiveError(ValueError):
    pass


def _strip_root(name: str) -> str | None:
    """GitHub tarballs wrap everything in one top-level directory; drop it."""
    parts = PurePosixPath(name).parts
    return "/".join(parts[1:]) if len(parts) > 1 else None


def materialize_tarball(data: bytes, dest: str | Path, *, source_files_only: bool = True) -> Path:
    """Extract a GitHub .tar.gz into `dest`. Only regular files are written; links,
    devices, absolute paths and `..` components are rejected. With
    `source_files_only`, only files the engine can parse (plus package.json, for test
    detection) are extracted, which keeps large repositories cheap."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            rel = _strip_root(member.name)
            if rel is None or member.isdir():
                continue
            if not member.isfile():
                continue  # symlinks, hardlinks, devices: never materialized
            p = PurePosixPath(rel)
            if p.is_absolute() or ".." in p.parts:
                raise UnsafeArchiveError(f"unsafe path in archive: {member.name}")
            if any(part in DEFAULT_IGNORED_DIRS for part in p.parts[:-1]) or member.size > MAX_FILE_BYTES:
                continue
            if source_files_only and language_for_path(rel) is None and p.name != "package.json":
                continue
            total += member.size
            if total > MAX_TOTAL_BYTES:
                raise UnsafeArchiveError("archive exceeds the extraction size limit")
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest):
                raise UnsafeArchiveError(f"path escapes destination: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is not None:
                target.write_bytes(src.read())
    return dest


@contextmanager
def tarball_checkout(repository: str, data: bytes, *, source_files_only: bool = True) -> Iterator[RepositoryTarget]:
    """Temporary `RepositoryTarget` for a tarball; removed when the block exits."""
    tmp = Path(tempfile.mkdtemp(prefix="flagpilot-repo-"))
    try:
        yield RepositoryTarget(repository=repository, root_path=str(materialize_tarball(data, tmp, source_files_only=source_files_only)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scan_github_tarball(repository: str, data: bytes) -> ScanResult:
    """Discovery over a GitHub tarball, e.g. the one apps/api downloads in
    `POST /api/repositories/{id}/scan`."""
    with tarball_checkout(repository, data) as target:
        return CodebaseSnapshot([target]).scan
