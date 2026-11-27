"""Source transformations that remove a flag check by substituting its known value.

Each confirmed reference is replaced by the value the flag has after cleanup and
the surrounding boolean logic is partially evaluated:

    !flag            -> !v
    flag && x        -> x            (v true)   | false (v false)
    flag || x        -> true         (v true)   | x     (v false)
    if (v) A else B  -> A / B        (block unwrapped and re-indented)
    v ? a : b        -> a / b
    {v && <X/>}      -> <X/> / (removed)

Anything outside these rules becomes a manual step rather than a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from tree_sitter import Node

from ..discovery import ast_utils as A
from ..discovery.ecmascript import ModuleInfo
from ..schemas.discovery import FlagReference, UsageType
from ..schemas.patch import EditKind

NEEDS_PARENS = {"binary_expression", "ternary_expression", "assignment_expression", "sequence_expression",
                "arrow_function", "augmented_assignment_expression", "await_expression", "yield_expression"}
SAFE_PARENTS = {"parenthesized_expression", "jsx_expression", "variable_declarator", "return_statement", "arguments",
                "expression_statement", "pair", "array", "template_substitution", "spread_element"}
JSX_NODES = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
EMPTY_RENDER = {"null", "undefined", "false", "true"}


@dataclass
class Edit:
    start: int
    end: int
    replacement: str
    kind: EditKind
    description: str
    reference_id: str | None
    line: int


@dataclass
class EditResult:
    edits: list[Edit]
    manual: list[str]


def _literal(value) -> str:
    return json.dumps(value)


def _expr_text(node: Node, src: bytes, target: Node) -> str:
    """Text of `node` to substitute in place of `target`, parenthesized if needed."""
    t = A.text(node, src)
    inner = A.unwrap(node) if node.type == "parenthesized_expression" else node
    parent = target.parent
    if inner is not None and inner.type in NEEDS_PARENS and (parent is None or parent.type not in SAFE_PARENTS):
        return t if node.type == "parenthesized_expression" else f"({t})"
    if node.type == "parenthesized_expression" and parent is not None and parent.type in SAFE_PARENTS and inner is not None:
        return A.text(inner, src)
    return t


def _statements_text(stmts: list[Node], src: bytes, target_indent: str) -> str:
    if not stmts:
        return ""
    raw = src[stmts[0].start_byte : stmts[-1].end_byte].decode("utf-8", "replace")
    inner_indent = A.line_indent(src, stmts[0].start_byte)
    eol = "\r\n" if b"\r\n" in src else "\n"  # preserve the file's line-ending style
    lines = raw.replace("\r\n", "\n").split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith(inner_indent):
            out.append(target_indent + ln[len(inner_indent):])
        else:
            out.append(target_indent + ln.lstrip())
    return eol.join(out)


def _block_stmts(node: Node | None) -> list[Node]:
    if node is None:
        return []
    if node.type == "statement_block":
        return [c for c in node.named_children if c.type != "comment"]
    return [node]


def _whole_line_removal(src: bytes, start: int, end: int) -> tuple[int, int]:
    """Extend a removal to the full line(s) when nothing else remains on them."""
    ls = src.rfind(b"\n", 0, start) + 1
    le = src.find(b"\n", end)
    le = len(src) if le == -1 else le
    if src[ls:start].strip() == b"" and src[end:le].strip() in (b"", b";"):
        return ls, min(le + 1, len(src))
    return start, end


def _bool_node_for(ref: FlagReference, node: Node, src: bytes) -> Node:
    """The node whose truthiness equals the flag's enabled state."""
    if ref.usage_type == UsageType.FLAG_OBJECT_RETRIEVAL:
        p = node.parent
        if p is not None and p.type == "member_expression" and A.text(p.child_by_field_name("property"), src) == "enabled":
            return p
    return node


def boolean_edit(ref: FlagReference, node: Node, m: ModuleInfo, value: bool) -> Edit | str:
    """Edit for one boolean flag check, or a reason string if it must be done manually."""
    src = m.src
    expr = _bool_node_for(ref, node, src)
    known: bool | None = value
    line = expr.start_point[0] + 1

    while True:
        p = expr.parent
        if p is None:
            break
        t = p.type
        if t in ("parenthesized_expression", "await_expression", "non_null_expression", "as_expression"):
            expr = p
            continue
        if t == "unary_expression" and A.text(p.child_by_field_name("operator"), src) == "!":
            known = not known
            expr = p
            continue
        if t == "binary_expression":
            op = A.text(p.child_by_field_name("operator"), src)
            left, right = p.child_by_field_name("left"), p.child_by_field_name("right")
            other = right if left == expr else left
            flag_on_left = left == expr
            if op in ("===", "==", "!==", "!=") and A.text(other, src) in ("true", "false"):
                eq = op in ("===", "==")
                known = (known == (A.text(other, src) == "true")) if eq else (known != (A.text(other, src) == "true"))
                expr = p
                continue
            if op in ("&&", "||"):
                short = (op == "&&" and not known) or (op == "||" and known)
                if short:
                    if not flag_on_left and any(n.type == "call_expression" for n in A.walk(other)):
                        return (f"line {line}: `{A.text(other, src)}` is evaluated before the flag check and has calls; "
                                "removing it could drop side effects")
                    expr = p
                    continue  # whole expression has a known value
                # Non-short-circuit: the expression reduces to the other operand.
                return _residual_edit(ref, p, other, m, line)
        break

    return _known_value_edit(ref, expr, bool(known), m, line)


def _residual_edit(ref, binary: Node, other: Node, m: ModuleInfo, line: int) -> Edit:
    src = m.src
    parent = binary.parent
    text = _expr_text(other, src, binary)
    if parent is not None and parent.type == "jsx_expression" and A.unwrap(other) is not None and A.unwrap(other).type in JSX_NODES:
        return Edit(parent.start_byte, parent.end_byte, A.text(A.unwrap(other), src), EditKind.KEEP_BRANCH,
                    f"render `{A.text(A.unwrap(other), src)[:60]}` unconditionally", ref.id, line)
    return Edit(binary.start_byte, binary.end_byte, text, EditKind.SIMPLIFY_CONDITION,
                f"condition `{A.text(binary, src)[:80]}` simplified to `{text[:60]}`", ref.id, line)


def _known_value_edit(ref, expr: Node, known: bool, m: ModuleInfo, line: int) -> Edit | str:
    src = m.src
    c = expr.parent
    if c is None:
        return f"line {line}: flag check at the top level of the file"

    if c.type == "if_statement" and c.child_by_field_name("condition") == expr:
        cons = c.child_by_field_name("consequence")
        alt_clause = c.child_by_field_name("alternative")
        alt = alt_clause.named_children[0] if alt_clause is not None and alt_clause.named_children else None
        indent = A.line_indent(src, c.start_byte)
        is_guard = alt is None and len(_block_stmts(cons)) == 1 and _block_stmts(cons)[0].type in (
            "return_statement", "throw_statement", "continue_statement", "break_statement")
        if known:
            if is_guard and c.parent is not None and c.parent.type in ("statement_block", "program"):
                siblings = [s for s in c.parent.named_children if s.type != "comment"]
                last = siblings[-1]
                kept = _statements_text(_block_stmts(cons), src, indent)
                dropped = "statements after the guard" if last != c else ""
                return Edit(c.start_byte, last.end_byte, kept, EditKind.KEEP_BRANCH,
                            f"guard always taken: keep `{kept[:60]}`" + (f" and drop unreachable {dropped}" if dropped else ""),
                            ref.id, line)
            kept = _statements_text(_block_stmts(cons), src, indent)
            if not kept:
                s, e = _whole_line_removal(src, c.start_byte, c.end_byte)
                return Edit(s, e, "", EditKind.REMOVE_CONDITIONAL, "kept branch is empty; conditional removed", ref.id, line)
            return Edit(c.start_byte, c.end_byte, kept, EditKind.KEEP_BRANCH, "if-consequence kept, else-branch removed", ref.id, line)
        # condition always false
        if alt is None:
            s, e = _whole_line_removal(src, c.start_byte, c.end_byte)
            return Edit(s, e, "", EditKind.REMOVE_CONDITIONAL, "condition never true; if-statement removed", ref.id, line)
        if alt.type == "if_statement":
            return Edit(c.start_byte, c.end_byte, A.text(alt, src), EditKind.KEEP_BRANCH, "else-if chain promoted", ref.id, line)
        kept = _statements_text(_block_stmts(alt), src, indent)
        if not kept:
            s, e = _whole_line_removal(src, c.start_byte, c.end_byte)
            return Edit(s, e, "", EditKind.REMOVE_CONDITIONAL, "kept branch is empty; conditional removed", ref.id, line)
        return Edit(c.start_byte, c.end_byte, kept, EditKind.KEEP_BRANCH, "else-branch kept, if-consequence removed", ref.id, line)

    if c.type == "ternary_expression" and c.child_by_field_name("condition") == expr:
        chosen = c.child_by_field_name("consequence" if known else "alternative")
        inner = A.unwrap(chosen)
        jsx = c.parent if c.parent is not None and c.parent.type == "jsx_expression" else None
        if jsx is not None and inner is not None and inner.type in JSX_NODES:
            return Edit(jsx.start_byte, jsx.end_byte, A.text(inner, src), EditKind.KEEP_BRANCH,
                        f"render `{A.text(inner, src)[:60]}` unconditionally", ref.id, line)
        if jsx is not None and inner is not None and A.text(inner, src) in EMPTY_RENDER:
            s, e = _whole_line_removal(src, jsx.start_byte, jsx.end_byte)
            return Edit(s, e, "", EditKind.REMOVE_CONDITIONAL, "kept branch renders nothing; expression removed", ref.id, line)
        text = _expr_text(chosen, src, c)
        return Edit(c.start_byte, c.end_byte, text, EditKind.KEEP_BRANCH, f"ternary replaced by `{text[:60]}`", ref.id, line)

    if c.type == "jsx_expression":
        s, e = _whole_line_removal(src, c.start_byte, c.end_byte)
        return Edit(s, e, "", EditKind.REMOVE_CONDITIONAL, "boolean JSX expression renders nothing; removed", ref.id, line)

    if c.type == "expression_statement":
        s, e = _whole_line_removal(src, c.start_byte, c.end_byte)
        return Edit(s, e, "", EditKind.REMOVE_STATEMENT, "statement has no effect after cleanup; removed", ref.id, line)

    if c.type in ("return_statement", "arguments", "variable_declarator", "pair", "arrow_function", "assignment_expression", "array"):
        lit = "true" if known else "false"
        return Edit(expr.start_byte, expr.end_byte, lit, EditKind.REPLACE_WITH_LITERAL,
                    f"flag check replaced by `{lit}`; review downstream uses (value escapes this function)", ref.id, line)

    return f"line {line}: unsupported construct `{c.type}` around the flag check"


def value_edit(ref: FlagReference, node: Node, m: ModuleInfo, value) -> Edit | str:
    src = m.src
    line = node.start_point[0] + 1
    target = node
    if ref.usage_type == UsageType.FLAG_OBJECT_RETRIEVAL:
        p = node.parent
        if p is not None and p.type == "member_expression" and A.text(p.child_by_field_name("property"), src) == "value":
            target = p
        else:
            return f"line {line}: flag object `{A.text(node, src)}` is used directly; inline manually"
    lit = _literal(value)
    return Edit(target.start_byte, target.end_byte, lit, EditKind.INLINE_VALUE,
                f"`{A.text(target, src)[:60]}` inlined as {lit}", ref.id, line)


def subscription_edit(ref: FlagReference, node: Node, m: ModuleInfo) -> Edit | str:
    """Remove a flag name from a `useFlags([...])` array."""
    src = m.src
    arr = node.parent
    line = node.start_point[0] + 1
    if arr is None or arr.type != "array":
        return f"line {line}: useFlags subscription is not an array literal"
    elements = [c for c in arr.named_children if c.type != "comment"]
    if len(elements) == 1:
        return Edit(arr.start_byte, arr.end_byte, "[]", EditKind.REMOVE_SUBSCRIPTION, "flag removed from useFlags()", ref.id, line)
    idx = elements.index(node)
    if idx < len(elements) - 1:
        start, end = node.start_byte, elements[idx + 1].start_byte
    else:
        start, end = elements[idx - 1].end_byte, node.end_byte
    return Edit(start, end, "", EditKind.REMOVE_SUBSCRIPTION, "flag removed from useFlags()", ref.id, line)
