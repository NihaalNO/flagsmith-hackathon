from __future__ import annotations

from tree_sitter import Node

from ..discovery import ast_utils as A
from ..discovery.ecmascript import ModuleInfo
from ..schemas.common import SourceSpan


def span_for(node: Node, m: ModuleInfo) -> SourceSpan:
    sr, sc = node.start_point
    er, ec = node.end_point
    return SourceSpan(
        repository=m.repository,
        file_path=m.path,
        line=sr + 1,
        column=A.char_column(m.src, sr, sc),
        end_line=er + 1,
        end_column=A.char_column(m.src, er, ec),
        start_byte=node.start_byte,
        end_byte=node.end_byte,
    )


def span_between(first: Node, last: Node, m: ModuleInfo) -> SourceSpan:
    s = span_for(first, m)
    e = span_for(last, m)
    return s.model_copy(update={"end_line": e.end_line, "end_column": e.end_column, "end_byte": e.end_byte})


def excerpt(m: ModuleInfo, start_byte: int, end_byte: int, max_lines: int = 8) -> str:
    raw = m.src[start_byte:end_byte].decode("utf-8", "replace").replace("\r\n", "\n")
    lines = raw.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def contains(outer: SourceSpan, inner: SourceSpan) -> bool:
    return (
        outer.repository == inner.repository
        and outer.file_path == inner.file_path
        and outer.start_byte is not None
        and inner.start_byte is not None
        and outer.start_byte <= inner.start_byte
        and inner.end_byte <= outer.end_byte
    )
