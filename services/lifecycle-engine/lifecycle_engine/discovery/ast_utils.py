"""Small helpers over Tree-sitter nodes (shared by discovery, analysis and cleanup)."""

from __future__ import annotations

from typing import Iterator

from tree_sitter import Node

# Expression wrappers that do not change the runtime value of the inner expression.
TRANSPARENT_WRAPPERS = {
    "parenthesized_expression",
    "as_expression",
    "satisfies_expression",
    "non_null_expression",
    "type_assertion",
}

FUNCTION_NODES = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "function",
    "arrow_function",
    "method_definition",
}


def text(node: Node | None, src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def unwrap(node: Node | None) -> Node | None:
    """Strip parentheses / `as` / `satisfies` / `!` / `await` wrappers."""
    while node is not None and (node.type in TRANSPARENT_WRAPPERS or node.type == "await_expression"):
        inner = [c for c in node.named_children if c.type not in ("type_annotation",) and not c.type.endswith("_type")]
        if not inner:
            break
        node = inner[0]
    return node


def string_value(node: Node | None, src: bytes) -> str | None:
    """Return the value of a string literal or substitution-free template string."""
    node = unwrap(node)
    if node is None:
        return None
    if node.type == "string":
        return "".join(text(c, src) for c in node.named_children if c.type in ("string_fragment", "escape_sequence"))
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.named_children):
            return None
        return "".join(text(c, src) for c in node.named_children if c.type in ("string_fragment", "escape_sequence"))
    return None


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def ancestors(node: Node) -> Iterator[Node]:
    p = node.parent
    while p is not None:
        yield p
        p = p.parent


def function_name(fn: Node, src: bytes) -> str | None:
    """Best-effort name for a function-like node."""
    name = fn.child_by_field_name("name")
    if name is not None:
        base = text(name, src)
        if fn.type == "method_definition":
            for a in ancestors(fn):
                if a.type in ("class_declaration", "class"):
                    cname = a.child_by_field_name("name")
                    if cname is not None:
                        return f"{text(cname, src)}.{base}"
                    break
        return base
    parent = fn.parent
    # const Foo = () => ..., possibly wrapped e.g. React.memo(() => ...)
    while parent is not None and parent.type in ("arguments", "call_expression", "parenthesized_expression"):
        parent = parent.parent
    if parent is not None and parent.type == "variable_declarator":
        return text(parent.child_by_field_name("name"), src) or None
    if parent is not None and parent.type in ("pair", "public_field_definition", "assignment_expression"):
        key = parent.child_by_field_name("key") or parent.child_by_field_name("name") or parent.child_by_field_name("left")
        return text(key, src) or None
    return None


def enclosing_function(node: Node) -> Node | None:
    for a in ancestors(node):
        if a.type in FUNCTION_NODES:
            return a
    return None


def enclosing_function_name(node: Node, src: bytes) -> str | None:
    for a in ancestors(node):
        if a.type in FUNCTION_NODES:
            n = function_name(a, src)
            if n:
                return n
    return None


def line_text(src: bytes, row: int) -> str:
    lines = src.split(b"\n")
    if 0 <= row < len(lines):
        return lines[row].decode("utf-8", "replace").rstrip("\r")
    return ""


def char_column(src: bytes, row: int, byte_col: int) -> int:
    """Convert a 0-based byte column to a 1-based character column."""
    lines = src.split(b"\n")
    if 0 <= row < len(lines):
        return len(lines[row][:byte_col].decode("utf-8", "replace")) + 1
    return byte_col + 1


def line_indent(src: bytes, byte_offset: int) -> str:
    start = src.rfind(b"\n", 0, byte_offset) + 1
    i = start
    while i < len(src) and src[i : i + 1] in (b" ", b"\t"):
        i += 1
    return src[start:i].decode("utf-8")
