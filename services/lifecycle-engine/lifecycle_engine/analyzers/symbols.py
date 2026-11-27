"""Repository-wide symbol definitions and identifier usages (for related-code analysis)."""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

from ..discovery import ast_utils as A
from ..discovery.ecmascript import ModuleInfo, is_test_file
from ..discovery.scanner import CodebaseSnapshot
from ..schemas.common import SourceSpan
from .spans import span_for

_NON_USAGE_PARENTS = {"import_specifier", "import_clause", "namespace_import", "export_specifier"}


@dataclass
class SymbolDef:
    name: str
    kind: str
    module: ModuleInfo
    node: Node

    @property
    def span(self) -> SourceSpan:
        return span_for(self.node, self.module)


@dataclass
class Usage:
    repository: str
    file_path: str
    start_byte: int
    end_byte: int
    is_test: bool


@dataclass
class SymbolIndex:
    definitions: dict[str, list[SymbolDef]] = field(default_factory=dict)
    usages: dict[str, list[Usage]] = field(default_factory=dict)

    @classmethod
    def build(cls, snapshot: CodebaseSnapshot) -> "SymbolIndex":
        idx = cls()
        for m in snapshot.modules():
            idx._collect(m)
        return idx

    def _collect(self, m: ModuleInfo) -> None:
        src = m.src
        is_jsx = m.path.endswith((".tsx", ".jsx"))
        def_names: set[int] = set()
        for top in m.root.named_children:
            decl = top.child_by_field_name("declaration") if top.type == "export_statement" else top
            if decl is None:
                continue
            if decl.type in ("function_declaration", "class_declaration"):
                n = decl.child_by_field_name("name")
                if n is None:
                    continue
                name = A.text(n, src)
                kind = "class" if decl.type == "class_declaration" else ("component" if is_jsx and name[:1].isupper() else "function")
                self.definitions.setdefault(name, []).append(SymbolDef(name, kind, m, decl))
                def_names.add(n.start_byte)
            elif decl.type in ("lexical_declaration", "variable_declaration"):
                for d in decl.named_children:
                    if d.type != "variable_declarator":
                        continue
                    n, v = d.child_by_field_name("name"), A.unwrap(d.child_by_field_name("value"))
                    if n is None or n.type != "identifier" or v is None:
                        continue
                    if v.type in ("arrow_function", "function_expression", "function") or (
                        v.type == "call_expression" and any(x.type in ("arrow_function", "function_expression") for x in A.walk(v))
                    ):
                        name = A.text(n, src)
                        kind = "component" if is_jsx and name[:1].isupper() else "function"
                        self.definitions.setdefault(name, []).append(SymbolDef(name, kind, m, d))
                        def_names.add(n.start_byte)
        test = is_test_file(m.path)
        for n in A.walk(m.root):
            if n.type not in ("identifier", "property_identifier") or n.start_byte in def_names:
                continue
            if n.type == "property_identifier":
                p = n.parent
                # only count `this.method(...)` style property usages
                if p is None or p.type != "member_expression" or A.text(p.child_by_field_name("object"), src) != "this":
                    continue
            if n.parent is not None and n.parent.type in _NON_USAGE_PARENTS:
                continue
            if any(a.type == "import_statement" for a in A.ancestors(n)):
                continue
            self.usages.setdefault(A.text(n, src), []).append(Usage(m.repository, m.path, n.start_byte, n.end_byte, test))

    def definition(self, name: str, prefer_repo: str | None = None) -> SymbolDef | None:
        defs = self.definitions.get(name, [])
        for d in defs:
            if d.module.repository == prefer_repo:
                return d
        return defs[0] if defs else None
