from __future__ import annotations

import io
import tarfile

import pytest

from lifecycle_engine.discovery.sources import UnsafeArchiveError, materialize_tarball, scan_github_tarball, tarball_checkout
from lifecycle_engine.discovery.scanner import scan_repository

from tests.conftest import SAMPLES


def _tarball(files: dict[str, bytes], root: str = "acme-checkout-web-abc123", extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            info = tarfile.TarInfo(f"{root}/{path}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        for info in extra or []:
            tar.addfile(info)
    return buf.getvalue()


def _sample_files(repo: str) -> dict[str, bytes]:
    base = SAMPLES / repo
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}


def test_tarball_scan_matches_local_scan():
    files = _sample_files("checkout-web")
    from_tar = scan_github_tarball("acme/checkout-web", _tarball(files))
    local = scan_repository("acme/checkout-web", SAMPLES / "checkout-web")
    assert [r.id for r in from_tar.references] == [r.id for r in local.references]
    assert from_tar.summary == local.summary


def test_only_source_files_and_package_json_are_extracted(tmp_path):
    data = _tarball({"src/a.ts": b"export const a = 1;\n", "package.json": b"{}", "logo.png": b"\x89PNG",
                     "node_modules/x/index.js": b"x", "README.md": b"# hi"})
    out = materialize_tarball(data, tmp_path / "r")
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == ["package.json", "src/a.ts"]


def test_links_are_never_materialized(tmp_path):
    link = tarfile.TarInfo("root/src/evil.ts")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    out = materialize_tarball(_tarball({"src/ok.ts": b"1;"}, root="root", extra=[link]), tmp_path / "r")
    assert not (out / "src/evil.ts").exists() and (out / "src/ok.ts").exists()


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(UnsafeArchiveError):
        materialize_tarball(_tarball({"../../escape.ts": b"1;"}), tmp_path / "r")


def test_temporary_checkout_is_removed():
    with tarball_checkout("t/r", _tarball({"src/a.ts": b"1;"})) as target:
        from pathlib import Path

        root = Path(target.root_path)
        assert (root / "src/a.ts").exists()
    assert not root.exists()
