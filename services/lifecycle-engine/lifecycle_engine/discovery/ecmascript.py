"""TypeScript / JavaScript Flagsmith reference extraction.

Two phases:
  1. `ModuleInfo` collects imports, exports, string constants and Flagsmith
     bindings for one file. Bindings are resolved to a fixed point across the
     repository so that e.g. `export const client = createFlagsmithInstance()`
     in `lib/flags.ts` makes `client.hasFeature(...)` in another file CONFIRMED.
  2. `extract_references` walks the syntax tree and emits `FlagReference`s.

Known limitations (reported, not hidden): bindings are tracked per file by name,
not by lexical scope; computed property chains deeper than one level are not
resolved; values flowing through function returns are not followed.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from tree_sitter import Node, Tree

from ..schemas.common import SourceSpan, stable_id
from ..schemas.discovery import (
    FlagApi,
    FlagReference,
    FlagsmithSdk,
    NameResolution,
    ReferenceClassification,
    ReferenceContext,
    UsageType,
)
from . import ast_utils as A
from .languages import LanguageSupport
from .sdk_patterns import (
    AMBIGUOUS_METHODS,
    EXPORTS,
    FLAG_PROPERTY_USAGE,
    KIND_SDK,
    RECEIVER_APIS,
    SERVER_FLAGS_FACTORIES,
    TYPE_TO_INSTANCE,
    ApiSpec,
    BindingKind,
    module_sdk,
)

TEST_FILE_RE = re.compile(r"(^|/)(__tests__|tests?)/|\.(test|spec)\.[cm]?[jt]sx?$")
RESOLVE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")

ConstValue = str | dict[str, str]


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE_RE.search(path))


@dataclass
class Binding:
    kind: BindingKind
    sdk: FlagsmithSdk
    evidence: str
    flag_name: str | None = None  # for REACT_FLAG bindings


@dataclass
class ImportRecord:
    local: str
    specifier: str
    imported: str  # "default", "*", or exported name
    line: int


@dataclass
class ModuleInfo:
    repository: str
    path: str  # posix, relative to repo root
    lang: LanguageSupport
    src: bytes
    tree: Tree
    imports: list[ImportRecord] = field(default_factory=list)
    constants: dict[str, ConstValue] = field(default_factory=dict)
    exports: dict[str, str] = field(default_factory=dict)  # exported name -> local name
    reexports: dict[str, tuple[str, str]] = field(default_factory=dict)  # exported -> (specifier, imported)
    bindings: dict[str, Binding] = field(default_factory=dict)
    field_bindings: dict[str, Binding] = field(default_factory=dict)  # this.<name>

    @property
    def root(self) -> Node:
        return self.tree.root_node

    @property
    def has_flagsmith_import(self) -> bool:
        return any(module_sdk(i.specifier) for i in self.imports) or bool(self.bindings)


class RepoIndex:
    """All parsed modules of one repository, with relative-import resolution."""

    def __init__(self, repository: str, modules: list[ModuleInfo]):
        self.repository = repository
        self.modules = {m.path: m for m in modules}
        for m in modules:
            _collect_static(m)
        self._resolve_bindings()

    def resolve(self, from_path: str, specifier: str) -> ModuleInfo | None:
        if not specifier.startswith("."):
            return None
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), specifier))
        stem, ext = posixpath.splitext(base)
        candidates = [base]
        if ext in (".js", ".jsx", ".mjs", ".cjs"):  # TS projects import './x.js' for './x.ts'
            candidates += [stem + e for e in RESOLVE_EXTENSIONS]
        candidates += [base + e for e in RESOLVE_EXTENSIONS]
        candidates += [posixpath.join(base, "index" + e) for e in RESOLVE_EXTENSIONS]
        for c in candidates:
            if c in self.modules:
                return self.modules[c]
        return None

    # -- cross-module lookups -------------------------------------------------
    def exported_binding(self, module: ModuleInfo, name: str, depth: int = 0) -> Binding | None:
        if depth > 5:
            return None
        if name in module.exports:
            return module.bindings.get(module.exports[name])
        if name in module.reexports:
            spec, imported = module.reexports[name]
            target = self.resolve(module.path, spec)
            if target is not None:
                return self.exported_binding(target, imported, depth + 1)
            sdk = module_sdk(spec)
            if sdk and (sdk, imported) in EXPORTS:
                return Binding(EXPORTS[(sdk, imported)], sdk, f"re-exported from '{spec}'")
        return None

    def exported_constant(self, module: ModuleInfo, name: str, depth: int = 0) -> ConstValue | None:
        if depth > 5:
            return None
        if name in module.exports and module.exports[name] in module.constants:
            return module.constants[module.exports[name]]
        if name in module.reexports:
            spec, imported = module.reexports[name]
            target = self.resolve(module.path, spec)
            if target is not None:
                return self.exported_constant(target, imported, depth + 1)
        return None

    def _resolve_bindings(self) -> None:
        for _ in range(6):  # fixed point across files (export chains)
            changed = False
            for m in self.modules.values():
                changed |= _resolve_module_bindings(m, self)
            if not changed:
                break


# ---------------------------------------------------------------------------
# Phase 1a: imports, exports, constants (no cross-file knowledge needed)
# ---------------------------------------------------------------------------

def _collect_static(m: ModuleInfo) -> None:
    src = m.src
    for node in m.root.children:
        decl = node
        exported = False
        if node.type == "export_statement":
            _collect_export_statement(m, node)
            decl = node.child_by_field_name("declaration")
            exported = True
            if decl is None:
                continue
        if decl.type == "import_statement":
            _collect_import(m, decl)
        elif decl.type in ("lexical_declaration", "variable_declaration"):
            is_const = decl.children and A.text(decl.children[0], src) == "const"
            for d in decl.named_children:
                if d.type != "variable_declarator":
                    continue
                name_node = d.child_by_field_name("name")
                if name_node is None or name_node.type != "identifier":
                    continue
                name = A.text(name_node, src)
                if exported:
                    m.exports[name] = name
                if is_const:
                    value = _const_value(d.child_by_field_name("value"), src)
                    if value is not None:
                        m.constants[name] = value
        elif decl.type == "enum_declaration":
            name = A.text(decl.child_by_field_name("name"), src)
            body = decl.child_by_field_name("body")
            members: dict[str, str] = {}
            for member in body.named_children if body is not None else []:
                if member.type == "enum_assignment":
                    v = A.string_value(member.child_by_field_name("value"), src)
                    if v is not None:
                        members[A.text(member.child_by_field_name("name"), src)] = v
            if members:
                m.constants[name] = members
            if exported:
                m.exports[name] = name
        elif exported and decl.type in ("function_declaration", "class_declaration"):
            n = decl.child_by_field_name("name")
            if n is not None:
                m.exports[A.text(n, src)] = A.text(n, src)
    for d in A.walk(m.root):  # require() anywhere at top level or nested
        if d.type == "variable_declarator":
            _collect_require(m, d)


def _const_value(node: Node | None, src: bytes) -> ConstValue | None:
    node = A.unwrap(node)
    if node is None:
        return None
    s = A.string_value(node, src)
    if s is not None:
        return s
    if node.type == "object":
        out: dict[str, str] = {}
        for pair in node.named_children:
            if pair.type != "pair":
                continue
            key = pair.child_by_field_name("key")
            key_text = A.string_value(key, src) if key is not None and key.type == "string" else A.text(key, src)
            v = A.string_value(pair.child_by_field_name("value"), src)
            if v is not None and key_text:
                out[key_text] = v
        return out or None
    return None


def _collect_import(m: ModuleInfo, node: Node) -> None:
    src = m.src
    spec = A.string_value(node.child_by_field_name("source"), src)
    if spec is None:
        return
    line = node.start_point[0] + 1
    for clause in node.named_children:
        if clause.type != "import_clause":
            continue
        for c in clause.named_children:
            if c.type == "identifier":
                m.imports.append(ImportRecord(A.text(c, src), spec, "default", line))
            elif c.type == "namespace_import":
                ident = next((x for x in c.named_children if x.type == "identifier"), None)
                if ident is not None:
                    m.imports.append(ImportRecord(A.text(ident, src), spec, "*", line))
            elif c.type == "named_imports":
                for s in c.named_children:
                    if s.type != "import_specifier":
                        continue
                    name = A.text(s.child_by_field_name("name"), src)
                    alias = s.child_by_field_name("alias")
                    m.imports.append(ImportRecord(A.text(alias, src) if alias else name, spec, name, line))


def _collect_export_statement(m: ModuleInfo, node: Node) -> None:
    src = m.src
    source = node.child_by_field_name("source")
    spec = A.string_value(source, src) if source is not None else None
    value = node.child_by_field_name("value")
    if value is not None and node.child_by_field_name("declaration") is None:
        v = A.unwrap(value)
        if v is not None and v.type == "identifier":
            m.exports["default"] = A.text(v, src)
    for c in node.named_children:
        if c.type == "export_clause":
            for s in c.named_children:
                if s.type != "export_specifier":
                    continue
                name = A.text(s.child_by_field_name("name"), src)
                alias = s.child_by_field_name("alias")
                exported = A.text(alias, src) if alias else name
                if spec is not None:
                    m.reexports[exported] = (spec, name)
                else:
                    m.exports[exported] = name


def _collect_require(m: ModuleInfo, d: Node) -> None:
    src = m.src
    value = A.unwrap(d.child_by_field_name("value"))
    if value is None or value.type != "call_expression":
        return
    fn = value.child_by_field_name("function")
    if fn is None or A.text(fn, src) != "require":
        return
    args = value.child_by_field_name("arguments")
    spec = A.string_value(args.named_children[0], src) if args is not None and args.named_children else None
    if spec is None:
        return
    name_node = d.child_by_field_name("name")
    line = d.start_point[0] + 1
    if name_node is None:
        return
    if name_node.type == "identifier":
        m.imports.append(ImportRecord(A.text(name_node, src), spec, "default", line))
    elif name_node.type == "object_pattern":
        for p in name_node.named_children:
            if p.type == "shorthand_property_identifier_pattern":
                n = A.text(p, src)
                m.imports.append(ImportRecord(n, spec, n, line))
            elif p.type == "pair_pattern":
                key = A.text(p.child_by_field_name("key"), src)
                val = p.child_by_field_name("value")
                if val is not None and val.type == "identifier":
                    m.imports.append(ImportRecord(A.text(val, src), spec, key, line))


# ---------------------------------------------------------------------------
# Phase 1b: binding resolution (iterated to a fixed point across the repo)
# ---------------------------------------------------------------------------

INSTANCE_KINDS = {BindingKind.CLIENT, BindingKind.SERVER_CLIENT, BindingKind.SERVER_FLAGS, BindingKind.REACT_FLAGS}


def _set(m: ModuleInfo, table: dict[str, Binding], name: str, b: Binding) -> bool:
    cur = table.get(name)
    if cur is not None and cur.kind == b.kind and cur.flag_name == b.flag_name:
        return False
    if cur is not None:
        return False  # first resolution wins; avoids oscillation on name reuse
    table[name] = b
    return True


def expr_kind(node: Node | None, m: ModuleInfo) -> Binding | None:
    """Return the Flagsmith binding an expression evaluates to, if known."""
    src = m.src
    node = A.unwrap(node)
    if node is None:
        return None
    if node.type == "identifier":
        return m.bindings.get(A.text(node, src))
    if node.type == "member_expression":
        obj = A.unwrap(node.child_by_field_name("object"))
        if obj is not None and obj.type == "this":
            return m.field_bindings.get(A.text(node.child_by_field_name("property"), src))
        return None
    if node.type == "call_expression":
        fn = A.unwrap(node.child_by_field_name("function"))
        if fn is None:
            return None
        if fn.type == "identifier":
            b = m.bindings.get(A.text(fn, src))
            if b is None:
                return None
            if b.kind == BindingKind.CLIENT_FACTORY:
                return Binding(BindingKind.CLIENT, b.sdk, f"instance from {A.text(fn, src)}() ({b.evidence})")
            if b.kind == BindingKind.USE_FLAGSMITH_HOOK:
                return Binding(BindingKind.CLIENT, FlagsmithSdk.JS_CLIENT, f"client from {A.text(fn, src)}() ({b.evidence})")
            if b.kind == BindingKind.USE_FLAGS_HOOK:
                return Binding(BindingKind.REACT_FLAGS, FlagsmithSdk.REACT, f"result of {A.text(fn, src)}() ({b.evidence})")
            return None
        if fn.type == "member_expression":
            prop = A.text(fn.child_by_field_name("property"), src)
            recv = expr_kind(fn.child_by_field_name("object"), m)
            if recv is not None and recv.kind == BindingKind.SERVER_CLIENT and prop in SERVER_FLAGS_FACTORIES:
                return Binding(BindingKind.SERVER_FLAGS, FlagsmithSdk.NODE_SERVER, f"flags from .{prop}() on server client ({recv.evidence})")
        return None
    if node.type == "new_expression":
        ctor = expr_kind(node.child_by_field_name("constructor"), m)
        if ctor is not None and ctor.kind == BindingKind.SERVER_CLASS:
            return Binding(BindingKind.SERVER_CLIENT, FlagsmithSdk.NODE_SERVER, f"new server client ({ctor.evidence})")
    return None


def _type_binding(type_node: Node | None, m: ModuleInfo) -> Binding | None:
    if type_node is None:
        return None
    for t in A.walk(type_node):
        if t.type == "type_identifier":
            b = m.bindings.get(A.text(t, m.src))
            if b is not None and b.kind in TYPE_TO_INSTANCE:
                return Binding(TYPE_TO_INSTANCE[b.kind], b.sdk, f"annotated with type {A.text(t, m.src)} ({b.evidence})")
    return None


def _resolve_module_bindings(m: ModuleInfo, index: RepoIndex) -> bool:
    src = m.src
    changed = False
    for imp in m.imports:
        sdk = module_sdk(imp.specifier)
        if sdk is not None and (sdk, imp.imported) in EXPORTS:
            how = {"default": "default import", "*": "namespace import"}.get(imp.imported, f"named import '{imp.imported}'")
            changed |= _set(m, m.bindings, imp.local, Binding(EXPORTS[(sdk, imp.imported)], sdk, f"`{imp.local}` is a {how} from '{imp.specifier}' (line {imp.line})"))
        elif imp.specifier.startswith("."):
            target = index.resolve(m.path, imp.specifier)
            if target is not None:
                b = index.exported_binding(target, imp.imported)
                if b is not None:
                    changed |= _set(m, m.bindings, imp.local, Binding(b.kind, b.sdk, f"`{imp.local}` imported from {target.path} where {b.evidence}", b.flag_name))
    for node in A.walk(m.root):
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            b = expr_kind(value, m) or _type_binding(node.child_by_field_name("type"), m)
            if b is None or name_node is None:
                continue
            if name_node.type == "identifier":
                if b.kind in INSTANCE_KINDS or b.kind in (BindingKind.CLIENT_FACTORY, BindingKind.USE_FLAGS_HOOK, BindingKind.USE_FLAGSMITH_HOOK, BindingKind.SERVER_CLASS):
                    changed |= _set(m, m.bindings, A.text(name_node, src), b)
            elif name_node.type == "object_pattern" and b.kind == BindingKind.REACT_FLAGS:
                for p in name_node.named_children:
                    if p.type == "shorthand_property_identifier_pattern":
                        n = A.text(p, src)
                        changed |= _set(m, m.bindings, n, Binding(BindingKind.REACT_FLAG, FlagsmithSdk.REACT, f"`{n}` destructured from {b.evidence}", flag_name=n))
                    elif p.type == "pair_pattern":
                        key = p.child_by_field_name("key")
                        key_name = A.string_value(key, src) if key is not None and key.type == "string" else A.text(key, src)
                        val = p.child_by_field_name("value")
                        if val is not None and val.type == "identifier":
                            n = A.text(val, src)
                            changed |= _set(m, m.bindings, n, Binding(BindingKind.REACT_FLAG, FlagsmithSdk.REACT, f"`{n}` destructured as '{key_name}' from {b.evidence}", flag_name=key_name))
        elif node.type in ("required_parameter", "optional_parameter"):
            b = _type_binding(node.child_by_field_name("type"), m)
            pattern = node.child_by_field_name("pattern")
            if b is not None and pattern is not None and pattern.type == "identifier":
                name = A.text(pattern, src)
                changed |= _set(m, m.bindings, name, b)
                # TS parameter properties: constructor(private flagsmith: IFlagsmith)
                if any(c.type == "accessibility_modifier" or A.text(c, src) == "readonly" for c in node.children):
                    changed |= _set(m, m.field_bindings, name, b)
        elif node.type == "public_field_definition":
            name = A.text(node.child_by_field_name("name"), src)
            b = _type_binding(node.child_by_field_name("type"), m) or expr_kind(node.child_by_field_name("value"), m)
            if b is not None and name:
                changed |= _set(m, m.field_bindings, name, b)
        elif node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "member_expression" and A.text(left.child_by_field_name("object"), src) == "this":
                b = expr_kind(node.child_by_field_name("right"), m)
                if b is not None:
                    changed |= _set(m, m.field_bindings, A.text(left.child_by_field_name("property"), src), b)
    return changed


# ---------------------------------------------------------------------------
# Phase 2: reference extraction
# ---------------------------------------------------------------------------

@dataclass
class NameInfo:
    name: str | None
    resolution: NameResolution
    note: str | None = None


def resolve_flag_name(node: Node | None, m: ModuleInfo, index: RepoIndex) -> NameInfo:
    src = m.src
    node = A.unwrap(node)
    if node is None:
        return NameInfo(None, NameResolution.UNRESOLVED, "no argument")
    s = A.string_value(node, src)
    if s is not None:
        return NameInfo(s, NameResolution.LITERAL)
    if node.type == "identifier":
        name = A.text(node, src)
        v = m.constants.get(name)
        if isinstance(v, str):
            return NameInfo(v, NameResolution.LOCAL_CONSTANT, f"const {name} = '{v}'")
        imported = _imported_constant(m, index, name)
        if isinstance(imported, tuple) and isinstance(imported[0], str):
            return NameInfo(imported[0], NameResolution.IMPORTED_CONSTANT, f"{name} = '{imported[0]}' from {imported[1]}")
    if node.type in ("member_expression", "subscript_expression"):
        obj = A.unwrap(node.child_by_field_name("object"))
        if node.type == "member_expression":
            key = A.text(node.child_by_field_name("property"), src)
        else:
            key = A.string_value(node.child_by_field_name("index"), src)
        if obj is not None and obj.type == "identifier" and key:
            oname = A.text(obj, src)
            v = m.constants.get(oname)
            if isinstance(v, dict) and key in v:
                return NameInfo(v[key], NameResolution.LOCAL_CONSTANT, f"{oname}.{key} = '{v[key]}'")
            imported = _imported_constant(m, index, oname)
            if isinstance(imported, tuple) and isinstance(imported[0], dict) and key in imported[0]:
                return NameInfo(imported[0][key], NameResolution.IMPORTED_CONSTANT, f"{oname}.{key} = '{imported[0][key]}' from {imported[1]}")
    return NameInfo(None, NameResolution.UNRESOLVED, f"flag name expression `{A.text(node, src)}` is not statically resolvable")


def _imported_constant(m: ModuleInfo, index: RepoIndex, local: str) -> tuple[ConstValue, str] | None:
    for imp in m.imports:
        if imp.local == local:
            target = index.resolve(m.path, imp.specifier)
            if target is not None:
                v = index.exported_constant(target, imp.imported)
                if v is not None:
                    return v, target.path
    return None


CONDITIONAL_PARENTS = {"if_statement", "ternary_expression", "switch_statement", "while_statement", "conditional_type"}


def _in_conditional(node: Node) -> bool:
    child = node
    for a in A.ancestors(node):
        if a.type == "if_statement" and a.child_by_field_name("condition") == child:
            return True
        if a.type == "ternary_expression" and a.child_by_field_name("condition") == child:
            return True
        if a.type == "binary_expression":
            op = a.child_by_field_name("operator")
            if op is not None and op.type in ("&&", "||", "??"):
                return True
        if a.type in ("expression_statement", "statement_block", "lexical_declaration", "return_statement") or a.type in A.FUNCTION_NODES:
            return False
        child = a
    return False


class _Emitter:
    def __init__(self, m: ModuleInfo, index: RepoIndex):
        self.m = m
        self.index = index
        self.refs: list[FlagReference] = []
        self.traits: list[FlagReference] = []
        self._seen: set[tuple[int, int, str]] = set()

    def emit(
        self,
        node: Node,
        *,
        api: FlagApi,
        usage: UsageType,
        sdk: FlagsmithSdk,
        name: NameInfo,
        confirmed: bool,
        confidence: float,
        evidence: list[str],
        arg_node: Node | None,
    ) -> None:
        key = (node.start_byte, node.end_byte, api.value)
        if key in self._seen:
            return
        self._seen.add(key)
        m, src = self.m, self.m.src
        sr, sc = node.start_point
        er, ec = node.end_point
        span = SourceSpan(
            repository=m.repository,
            file_path=m.path,
            line=sr + 1,
            column=A.char_column(src, sr, sc),
            end_line=er + 1,
            end_column=A.char_column(src, er, ec),
            start_byte=node.start_byte,
            end_byte=node.end_byte,
        )
        is_dynamic = name.name is None
        if usage == UsageType.TRAIT_READ:
            classification = ReferenceClassification.CONFIRMED if confirmed else ReferenceClassification.POTENTIAL
        elif not confirmed:
            classification = ReferenceClassification.POTENTIAL
        elif is_dynamic:
            classification = ReferenceClassification.DYNAMIC
        else:
            classification = ReferenceClassification.CONFIRMED
        ev = list(evidence)
        if name.note:
            ev.append(name.note)
        snippet = "\n".join(A.line_text(src, r).strip() for r in range(sr, min(er, sr + 2) + 1))
        ref = FlagReference(
            id=stable_id("ref", m.repository, m.path, span.line, span.column, api.value, name.name or ""),
            repository=m.repository,
            location=span,
            flag_name=None if usage == UsageType.TRAIT_READ else name.name,
            trait_key=name.name if usage == UsageType.TRAIT_READ else None,
            sdk=sdk,
            api=api,
            usage_type=usage,
            classification=classification,
            is_dynamic=is_dynamic,
            name_resolution=name.resolution,
            argument_expression=A.text(arg_node, src) if arg_node is not None else None,
            confidence=round(confidence, 2),
            evidence=ev,
            context=ReferenceContext(
                snippet=snippet,
                enclosing_function=A.enclosing_function_name(node, src),
                in_conditional=_in_conditional(node),
                is_test_file=is_test_file(m.path),
            ),
        )
        (self.traits if usage == UsageType.TRAIT_READ else self.refs).append(ref)


def extract_references(m: ModuleInfo, index: RepoIndex) -> tuple[list[FlagReference], list[FlagReference]]:
    em = _Emitter(m, index)
    src = m.src
    for node in A.walk(m.root):
        if node.type == "call_expression":
            _handle_call(node, m, index, em)
        elif node.type in ("member_expression", "subscript_expression"):
            _handle_flag_object_access(node, m, index, em)
    em.refs.sort(key=lambda r: (r.location.line, r.location.column))
    em.traits.sort(key=lambda r: (r.location.line, r.location.column))
    return em.refs, em.traits


def _first_arg(call: Node, idx: int = 0) -> Node | None:
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    named = [a for a in args.named_children if a.type != "comment"]
    return named[idx] if len(named) > idx else None


def _handle_call(node: Node, m: ModuleInfo, index: RepoIndex, em: _Emitter) -> None:
    src = m.src
    fn = A.unwrap(node.child_by_field_name("function"))
    if fn is None:
        return
    if fn.type == "member_expression":
        method = A.text(fn.child_by_field_name("property"), src)
        recv_node = fn.child_by_field_name("object")
        recv = expr_kind(recv_node, m)
        if recv is not None and recv.kind in RECEIVER_APIS:
            spec = RECEIVER_APIS[recv.kind].get(method)
            if spec is None:
                return
            arg = _first_arg(node, spec.name_arg_index)
            name = resolve_flag_name(arg, m, index)
            conf = 0.95 if name.resolution == NameResolution.LITERAL else 0.9
            em.emit(node, api=spec.api, usage=spec.usage, sdk=KIND_SDK.get(recv.kind, recv.sdk), name=name,
                    confirmed=True, confidence=conf, evidence=[f"receiver: {recv.evidence}"], arg_node=arg)
            return
        if recv is None and method in AMBIGUOUS_METHODS:
            spec = AMBIGUOUS_METHODS[method]
            recv_text = A.text(recv_node, src)
            hints = []
            conf = 0.0
            if "flagsmith" in recv_text.lower():
                hints.append(f"receiver `{recv_text}` is named like a Flagsmith client")
                conf = 0.6
            elif "flag" in recv_text.lower():
                hints.append(f"receiver `{recv_text}` is named like a flag client")
                conf = 0.45
            if m.has_flagsmith_import:
                hints.append("file imports a Flagsmith SDK module")
                conf = max(conf, 0.4) + 0.05
            if not hints:
                return  # a method named like a Flagsmith API with no Flagsmith context: not reported
            arg = _first_arg(node, spec.name_arg_index)
            name = resolve_flag_name(arg, m, index)
            em.emit(node, api=spec.api, usage=spec.usage, sdk=FlagsmithSdk.UNKNOWN, name=name, confirmed=False,
                    confidence=min(conf, 0.65),
                    evidence=[f"`.{method}()` matches a Flagsmith API but receiver `{recv_text}` could not be traced to a Flagsmith SDK binding", *hints],
                    arg_node=arg)
        return
    if fn.type == "identifier":
        b = m.bindings.get(A.text(fn, src))
        if b is None or b.kind != BindingKind.USE_FLAGS_HOOK:
            return
        for arg_idx, usage in ((0, UsageType.HOOK_SUBSCRIPTION), (1, UsageType.TRAIT_READ)):
            arr = A.unwrap(_first_arg(node, arg_idx))
            if arr is None:
                continue
            elements = arr.named_children if arr.type == "array" else [arr]
            for el in elements:
                if el.type == "comment":
                    continue
                name = resolve_flag_name(el, m, index)
                em.emit(el if arr.type == "array" else arr, api=FlagApi.USE_FLAGS_SUBSCRIPTION, usage=usage,
                        sdk=FlagsmithSdk.REACT, name=name, confirmed=True,
                        confidence=0.95 if name.name else 0.9,
                        evidence=[f"argument {arg_idx + 1} of {A.text(fn, src)}(): {b.evidence}"], arg_node=el)


def _handle_flag_object_access(node: Node, m: ModuleInfo, index: RepoIndex, em: _Emitter) -> None:
    src = m.src
    parent = node.parent
    # Ignore the callee position (flags.x(...)) and assignment targets.
    if parent is not None and parent.type == "call_expression" and parent.child_by_field_name("function") == node:
        return
    obj = node.child_by_field_name("object")
    b = expr_kind(obj, m)
    if b is None:
        return
    if b.kind == BindingKind.REACT_FLAGS:
        if node.type == "member_expression":
            name = NameInfo(A.text(node.child_by_field_name("property"), src), NameResolution.LITERAL)
            arg = node.child_by_field_name("property")
        else:
            arg = node.child_by_field_name("index")
            name = resolve_flag_name(arg, m, index)
        target, usage = node, UsageType.FLAG_OBJECT_RETRIEVAL
        if parent is not None and parent.type == "member_expression" and parent.child_by_field_name("object") == node:
            prop = A.text(parent.child_by_field_name("property"), src)
            if prop in FLAG_PROPERTY_USAGE:
                target, usage = parent, FLAG_PROPERTY_USAGE[prop]
        em.emit(target, api=FlagApi.FLAGS_OBJECT_ACCESS, usage=usage, sdk=FlagsmithSdk.REACT, name=name,
                confirmed=True, confidence=0.9 if name.name else 0.85, evidence=[f"object: {b.evidence}"], arg_node=arg)
    elif b.kind == BindingKind.REACT_FLAG and node.type == "member_expression":
        prop = A.text(node.child_by_field_name("property"), src)
        if prop in FLAG_PROPERTY_USAGE:
            em.emit(node, api=FlagApi.FLAGS_OBJECT_ACCESS, usage=FLAG_PROPERTY_USAGE[prop], sdk=FlagsmithSdk.REACT,
                    name=NameInfo(b.flag_name, NameResolution.LITERAL), confirmed=True, confidence=0.9,
                    evidence=[f"object: {b.evidence}"], arg_node=obj)
