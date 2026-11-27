"""Language registry: maps file extensions to Tree-sitter grammars.

To add a language, register a `LanguageSupport` with its grammar loader and the
scanner class that understands that language's SDK usage patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Callable

from tree_sitter import Language, Parser, Tree


@dataclass(frozen=True)
class LanguageSupport:
    name: str
    extensions: tuple[str, ...]
    loader: Callable[[], object]
    family: str  # scanners are selected by family, e.g. "ecmascript"


def _ts():
    import tree_sitter_typescript as m

    return m.language_typescript()


def _tsx():
    import tree_sitter_typescript as m

    return m.language_tsx()


def _js():
    import tree_sitter_javascript as m

    return m.language()


LANGUAGES: list[LanguageSupport] = [
    LanguageSupport("typescript", (".ts", ".mts", ".cts"), _ts, "ecmascript"),
    LanguageSupport("tsx", (".tsx",), _tsx, "ecmascript"),
    LanguageSupport("javascript", (".js", ".jsx", ".mjs", ".cjs"), _js, "ecmascript"),
]

EXCLUDED_SUFFIXES = (".d.ts", ".min.js")


def language_for_path(path: str) -> LanguageSupport | None:
    lower = path.lower()
    if lower.endswith(EXCLUDED_SUFFIXES):
        return None
    suffix = PurePosixPath(lower).suffix
    for lang in LANGUAGES:
        if suffix in lang.extensions:
            return lang
    return None


@lru_cache(maxsize=None)
def _parser(name: str) -> Parser:
    lang = next(l for l in LANGUAGES if l.name == name)
    return Parser(Language(lang.loader()))


def parse(source: bytes, lang: LanguageSupport) -> Tree:
    return _parser(lang.name).parse(source)


def supported_extensions() -> set[str]:
    return {ext for lang in LANGUAGES for ext in lang.extensions}
