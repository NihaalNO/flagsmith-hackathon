"""Deterministic detection of flag-controlled branches and fallback behavior."""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from ..discovery import ast_utils as A
from ..discovery.ecmascript import ModuleInfo
from ..schemas.analysis import (
    BranchKind,
    CodePath,
    FallbackBehavior,
    FallbackKind,
    FlagBranch,
    Polarity,
)
from ..schemas.common import stable_id
from ..schemas.discovery import FlagReference, UsageType
from .spans import excerpt, span_between, span_for

EXIT_STATEMENTS = {"return_statement", "throw_statement", "continue_statement", "break_statement"}


@dataclass
class Climb:
    expr: Node            # outermost node whose truthiness still tracks the flag (modulo ops)
    positive: bool        # polarity of `expr` relative to the flag
    ops: list[tuple[str, str, Node, bool]] = field(default_factory=list)  # (op, side, binary node, polarity before op)


def climb(node: Node, src: bytes) -> Climb:
    expr, positive, ops = node, True, []
    while True:
        p = expr.parent
        if p is None:
            break
        t = p.type
        if t in ("parenthesized_expression", "await_expression", "non_null_expression", "as_expression"):
            expr = p
            continue
        if t == "unary_expression" and A.text(p.child_by_field_name("operator"), src) == "!":
            positive = not positive
            expr = p
            continue
        if t == "binary_expression":
            op = A.text(p.child_by_field_name("operator"), src)
            left, right = p.child_by_field_name("left"), p.child_by_field_name("right")
            side = "left" if left == expr else "right"
            other = right if side == "left" else left
            if op in ("===", "==", "!==", "!=") and A.text(other, src) in ("true", "false"):
                eq = op in ("===", "==")
                if eq != (A.text(other, src) == "true"):
                    positive = not positive
                expr = p
                continue
            if op in ("&&", "||"):
                ops.append((op, side, p, positive))
                expr = p
                continue
        break
    return Climb(expr, positive, ops)


def _block_interior(node: Node) -> list[Node]:
    if node.type == "statement_block":
        return [c for c in node.named_children if c.type != "comment"]
    return [node]


def code_path(node: Node, m: ModuleInfo, description: str) -> CodePath:
    src = m.src
    stmts = _block_interior(node)
    if stmts and node.type == "statement_block":
        span = span_between(stmts[0], stmts[-1], m)
    else:
        span = span_for(node, m)
    callees: list[str] = []
    components: list[str] = []
    for n in A.walk(node):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                callees.append(A.text(fn, src))
            elif fn is not None and fn.type == "member_expression" and A.text(fn.child_by_field_name("object"), src) == "this":
                callees.append(A.text(fn.child_by_field_name("property"), src))
        elif n.type in ("jsx_opening_element", "jsx_self_closing_element"):
            name = n.child_by_field_name("name")
            t = A.text(name, src)
            if t[:1].isupper():
                components.append(t)
    return CodePath(
        span=span,
        excerpt=excerpt(m, span.start_byte, span.end_byte),
        callees=sorted(set(callees)),
        rendered_components=sorted(set(components)),
        is_empty=node.type == "statement_block" and not stmts,
        description=description,
    )


def _rest_of_block(if_node: Node, m: ModuleInfo) -> CodePath | None:
    parent = if_node.parent
    if parent is None or parent.type not in ("statement_block", "program"):
        return None
    siblings = [c for c in parent.named_children if c.type != "comment"]
    idx = next(i for i, c in enumerate(siblings) if c == if_node)
    rest = siblings[idx + 1 :]
    if not rest:
        return None
    span = span_between(rest[0], rest[-1], m)
    callees: set[str] = set()
    components: set[str] = set()
    for s in rest:
        p = code_path(s, m, "")
        callees.update(p.callees)
        components.update(p.rendered_components)
    return CodePath(span=span, excerpt=excerpt(m, span.start_byte, span.end_byte), callees=sorted(callees),
                    rendered_components=sorted(components), description="statements after the guard clause")


def _is_guard(consequence: Node) -> bool:
    stmts = _block_interior(consequence)
    return len(stmts) == 1 and stmts[0].type in EXIT_STATEMENTS


def _make_branch(ref: FlagReference, kind: BranchKind, c: Climb, construct: Node, m: ModuleInfo,
                 enabled: CodePath | None, disabled: CodePath | None, determinable: bool,
                 notes: list[str], alias: str | None) -> FlagBranch:
    return FlagBranch(
        id=stable_id("branch", ref.id, construct.start_byte, alias or ""),
        reference_id=ref.id,
        kind=kind,
        polarity=Polarity.POSITIVE if c.positive else Polarity.NEGATIVE,
        condition=A.text(c.expr, m.src),
        condition_is_exact=not c.ops,
        construct_span=span_for(construct, m),
        enabled_path=enabled,
        disabled_path=disabled,
        determinable=determinable,
        via_alias=alias,
        notes=notes,
    )


@dataclass
class BranchAnalysis:
    branches: list[FlagBranch] = field(default_factory=list)
    escapes: list[str] = field(default_factory=list)       # descriptions of where the flag value escapes
    unanalyzed: list[str] = field(default_factory=list)


def analyze_boolean_use(ref: FlagReference, node: Node, m: ModuleInfo, alias: str | None = None,
                        out: BranchAnalysis | None = None) -> BranchAnalysis:
    out = out or BranchAnalysis()
    src = m.src
    c = climb(node, src)
    container = c.expr.parent

    if container is not None and container.type == "if_statement" and container.child_by_field_name("condition") == c.expr:
        cons = container.child_by_field_name("consequence")
        alt_clause = container.child_by_field_name("alternative")
        alt = None
        if alt_clause is not None:
            alt = alt_clause.named_children[0] if alt_clause.type == "else_clause" and alt_clause.named_children else alt_clause
        if not c.ops:
            if alt is None and _is_guard(cons):
                true_path = code_path(cons, m, "early exit taken when the condition is true")
                rest = _rest_of_block(container, m)
                enabled, disabled = (true_path, rest) if c.positive else (rest, true_path)
                out.branches.append(_make_branch(ref, BranchKind.GUARD_CLAUSE, c, container, m, enabled, disabled, True,
                                                 ["guard clause: the remaining statements run only when the condition is false"], alias))
            else:
                true_path = code_path(cons, m, "if-consequence")
                false_path = code_path(alt, m, "else-branch") if alt is not None else None
                enabled, disabled = (true_path, false_path) if c.positive else (false_path, true_path)
                kind = BranchKind.IF_ELSE if alt is not None else BranchKind.IF
                notes = [] if alt is not None else ["no else-branch: nothing runs for the opposite flag state"]
                if alt is not None and alt.type == "if_statement":
                    notes.append("else-branch is an else-if chain; its own conditions also apply")
                out.branches.append(_make_branch(ref, kind, c, container, m, enabled, disabled, True, notes, alias))
        else:
            ops = ", ".join(sorted({o[0] for o in c.ops}))
            out.branches.append(_make_branch(
                ref, BranchKind.IF_ELSE if alt is not None else BranchKind.IF, c, container, m,
                code_path(cons, m, "if-consequence (also depends on other conditions)") if _requires_flag(c) else None,
                None, False,
                [f"compound condition ({ops}): paths depend on other operands; manual review required"], alias))
        return out

    if container is not None and container.type == "ternary_expression" and container.child_by_field_name("condition") == c.expr:
        cons = container.child_by_field_name("consequence")
        alt = container.child_by_field_name("alternative")
        if not c.ops:
            t, f = code_path(cons, m, "ternary consequence"), code_path(alt, m, "ternary alternative")
            enabled, disabled = (t, f) if c.positive else (f, t)
            out.branches.append(_make_branch(ref, BranchKind.TERNARY, c, container, m, enabled, disabled, True, [], alias))
        else:
            out.branches.append(_make_branch(ref, BranchKind.TERNARY, c, container, m, None, None, False,
                                             ["compound ternary condition; manual review required"], alias))
        return out

    if c.ops:
        op, side, binary, pol = c.ops[0]
        if side == "left" and len(c.ops) == 1:
            right = binary.child_by_field_name("right")
            path = code_path(right, m, f"right operand of {op}")
            # `flag && X`: X runs when flag true. `flag || X`: X runs when flag false.
            runs_when_enabled = (op == "&&") == pol
            enabled, disabled = (path, None) if runs_when_enabled else (None, path)
            kind = BranchKind.LOGICAL_AND if op == "&&" else BranchKind.LOGICAL_OR
            sub = Climb(binary.child_by_field_name("left"), pol, [])
            out.branches.append(_make_branch(ref, kind, sub, binary, m, enabled, disabled, True,
                                             [f"short-circuit {op}: the right operand is evaluated only when the flag is {'enabled' if runs_when_enabled else 'disabled'}"], alias))
        else:
            out.branches.append(_make_branch(ref, BranchKind.LOGICAL_AND if c.ops[0][0] == "&&" else BranchKind.LOGICAL_OR,
                                             c, c.ops[-1][2], m, None, None, False,
                                             ["flag participates in a compound boolean expression; manual review required"], alias))
        return out

    # Value is stored in a variable: follow the alias one level within its scope.
    if container is not None and container.type == "variable_declarator" and container.child_by_field_name("value") == c.expr and alias is None:
        name_node = container.child_by_field_name("name")
        if name_node is not None and name_node.type == "identifier":
            name = A.text(name_node, src)
            scope = A.enclosing_function(container) or m.root
            uses = [n for n in A.walk(scope) if n.type == "identifier" and A.text(n, src) == name and n != name_node]
            for u in uses:
                analyze_boolean_use(ref, u, m, alias=name if c.positive else f"!{name}", out=out)
            if not uses:
                out.unanalyzed.append(f"flag value stored in `{name}` but `{name}` is never used")
            return out

    if container is not None and container.type in ("return_statement", "arguments", "pair", "jsx_expression",
                                                     "assignment_expression", "arrow_function", "variable_declarator",
                                                     "spread_element", "array"):
        where = {
            "return_statement": "returned from", "arguments": "passed as an argument in",
            "arrow_function": "returned from arrow function", "jsx_expression": "rendered/passed in JSX in",
        }.get(container.type, f"used in a {container.type.replace('_', ' ')} in")
        out.escapes.append(f"flag value is {where} `{A.enclosing_function_name(node, src) or '<module>'}` at line {node.start_point[0] + 1}")
        return out

    out.unanalyzed.append(f"flag check at line {node.start_point[0] + 1} is used in an unsupported construct ({container.type if container else 'root'})")
    return out


def _requires_flag(c: Climb) -> bool:
    return all(op == "&&" for op, *_ in c.ops) and c.positive and all(pol for *_, pol in c.ops)


def analyze_fallback(ref: FlagReference, node: Node, m: ModuleInfo) -> list[FallbackBehavior]:
    src = m.src
    out: list[FallbackBehavior] = []
    if node.type == "call_expression":
        args = node.child_by_field_name("arguments")
        named = [a for a in (args.named_children if args else []) if a.type != "comment"]
        if len(named) >= 2 and named[1].type == "object":
            for pair in named[1].named_children:
                if pair.type == "pair" and A.text(pair.child_by_field_name("key"), src) == "fallback":
                    v = pair.child_by_field_name("value")
                    out.append(FallbackBehavior(reference_id=ref.id, kind=FallbackKind.OPTION_FALLBACK,
                                                default_expression=A.text(v, src), location=span_for(v, m)))
    c = climb(node, src)
    p = c.expr.parent
    if p is not None and p.type == "binary_expression" and p.child_by_field_name("left") == c.expr:
        op = A.text(p.child_by_field_name("operator"), src)
        if op in ("||", "??"):
            r = p.child_by_field_name("right")
            out.append(FallbackBehavior(reference_id=ref.id,
                                        kind=FallbackKind.NULLISH_DEFAULT if op == "??" else FallbackKind.LOGICAL_OR_DEFAULT,
                                        default_expression=A.text(r, src), location=span_for(r, m)))
    if p is not None and p.type == "variable_declarator" and p.child_by_field_name("value") == c.expr:
        name_node = p.child_by_field_name("name")
        if name_node is not None and name_node.type == "identifier":
            name = A.text(name_node, src)
            scope = A.enclosing_function(p) or m.root
            for t in A.walk(scope):
                if t.type == "ternary_expression":
                    cond = t.child_by_field_name("condition")
                    if any(n.type == "identifier" and A.text(n, src) == name for n in A.walk(cond)):
                        alt = t.child_by_field_name("alternative")
                        if alt is not None and not any(n.type == "identifier" and A.text(n, src) == name for n in A.walk(alt)):
                            out.append(FallbackBehavior(reference_id=ref.id, kind=FallbackKind.TYPE_GUARD_DEFAULT,
                                                        default_expression=A.text(alt, src), location=span_for(alt, m)))
                elif t.type == "binary_expression" and A.text(t.child_by_field_name("left"), src) == name:
                    op = A.text(t.child_by_field_name("operator"), src)
                    if op in ("||", "??"):
                        r = t.child_by_field_name("right")
                        out.append(FallbackBehavior(reference_id=ref.id,
                                                    kind=FallbackKind.NULLISH_DEFAULT if op == "??" else FallbackKind.LOGICAL_OR_DEFAULT,
                                                    default_expression=A.text(r, src), location=span_for(r, m)))
    return out


BOOLEAN_USAGES = {UsageType.BOOLEAN_EVALUATION}
