"""Find where flag names appear in a repository snapshot (GitHub tarball)."""

import io
import re
import tarfile
from dataclasses import dataclass

MAX_FILE_BYTES = 1_000_000
MAX_REFS_PER_FLAG = 200
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".next", "out", "coverage",
             "__pycache__", ".venv", "venv", "target", ".turbo"}
SKIP_SUFFIXES = (".lock", ".min.js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
                 ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".mp4", ".webp")
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock"}


@dataclass
class Reference:
    flag_name: str
    path: str
    line: int
    snippet: str


@dataclass
class ScanResult:
    files_scanned: int
    references: list[Reference]


def _wanted(path: str) -> bool:
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    name = parts[-1]
    return name not in SKIP_FILES and not name.lower().endswith(SKIP_SUFFIXES)


def scan_tarball(data: bytes, flag_names: list[str]) -> ScanResult:
    if not flag_names:
        return ScanResult(0, [])
    # Whole-identifier match, so "pricing" doesn't match "pricing_enabled".
    names = sorted(set(flag_names), key=len, reverse=True)
    pattern = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9_])")
    refs: list[Reference] = []
    counts: dict[str, int] = {}
    files = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.size > MAX_FILE_BYTES:
                continue
            # GitHub tarballs wrap everything in "<owner>-<repo>-<sha>/"
            path = member.name.split("/", 1)[1] if "/" in member.name else member.name
            if not _wanted(path):
                continue
            raw = tar.extractfile(member).read()
            if b"\x00" in raw[:8000]:
                continue  # binary
            files += 1
            text = raw.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in pattern.finditer(line):
                    name = m.group(1)
                    if counts.get(name, 0) >= MAX_REFS_PER_FLAG:
                        continue
                    counts[name] = counts.get(name, 0) + 1
                    refs.append(Reference(name, path, lineno, line.strip()[:500]))
    return ScanResult(files, refs)
