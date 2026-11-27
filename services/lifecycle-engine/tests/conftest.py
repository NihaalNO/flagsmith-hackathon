from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from lifecycle_engine.discovery.scanner import scan_repositories, scan_repository
from lifecycle_engine.schemas.discovery import RepositoryTarget

ENGINE_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ENGINE_ROOT / "sample_repos"
SAMPLE_DATA = ENGINE_ROOT / "sample_data"

CHECKOUT = RepositoryTarget(repository="acme/checkout-web", root_path=str(SAMPLES / "checkout-web"))
PRICING = RepositoryTarget(repository="acme/pricing-service", root_path=str(SAMPLES / "pricing-service"))


@pytest.fixture(scope="session")
def sample_targets() -> list[RepositoryTarget]:
    return [CHECKOUT, PRICING]


@pytest.fixture(scope="session")
def sample_scan(sample_targets):
    return scan_repositories(sample_targets)


@pytest.fixture
def make_repo(tmp_path):
    """Create a throwaway repository from {relative_path: source} and return its path."""

    def _make(files: dict[str, str], name: str = "repo") -> Path:
        root = tmp_path / name
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
        return root

    return _make


@pytest.fixture
def scan_files(make_repo):
    def _scan(files: dict[str, str], repository: str = "test/repo"):
        return scan_repository(repository, make_repo(files))

    return _scan


@pytest.fixture
def sample_copy(tmp_path):
    """A writable copy of the sample repositories."""

    def _copy(name: str = "checkout-web") -> Path:
        dst = tmp_path / name
        shutil.copytree(SAMPLES / name, dst)
        return dst

    return _copy
