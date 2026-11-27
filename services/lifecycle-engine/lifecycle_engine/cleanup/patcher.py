"""Suggested patch generation. Produces reviewable diffs; never writes to the repository."""

from __future__ import annotations

import difflib
import hashlib
from collections import Counter

from ..discovery import ast_utils as A
from ..discovery.ecmascript import ModuleInfo
from ..discovery.scanner import CodebaseSnapshot
from ..schemas.common import stable_id
from ..schemas.discovery import ReferenceClassification, RepositoryTarget, UsageType
from ..schemas.patch import AppliedEdit, CleanupPatch, EditKind, FileChange, ManualStep, PatchStatus
from ..schemas.stale import CleanupDirection
from ..analyzers.symbols import SymbolIndex
from .transforms import Edit, _whole_line_removal, boolean_edit, subscription_edit, value_edit

MAX_ROUNDS = 8


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _apply(src: bytes, edits: list[Edit]) -> tuple[bytes, list[Edit]]:
    """Apply non-overlapping edits (outermost first); overlapping ones wait for the next round."""
    chosen: list[Edit] = []
    for e in sorted(edits, key=lambda e: (e.start, -e.end)):
        if any(not (e.end <= c.start or e.start >= c.end) for c in chosen):
            continue
        chosen.append(e)
    out = src
    for e in sorted(chosen, key=lambda e: e.start, reverse=True):
        out = out[: e.start] + e.replacement.encode("utf-8") + out[e.end :]
    return out, chosen


def _identifier_uses(m: ModuleInfo) -> Counter:
    c: Counter = Counter()
    for n in A.walk(m.root):
        if n.type in ("identifier", "type_identifier") and not any(a.type == "import_statement" for a in A.ancestors(n)):
            c[A.text(n, m.src)] += 1
    return c


class PatchGenerator:
    def __init__(self, targets: list[RepositoryTarget]):
        self.targets = targets

    def generate(self, flag_name: str, direction: CleanupDirection, *, inline_value=None,
                 enabled_state: bool | None = None) -> CleanupPatch:
        base = CodebaseSnapshot(self.targets)
        notes: list[str] = []
        if direction in (CleanupDirection.UNDETERMINED, CleanupDirection.ARCHIVE_ONLY):
            reason = ("cleanup direction is undetermined; no code change can be derived safely"
                      if direction == CleanupDirection.UNDETERMINED else
                      "no code references: cleanup consists of archiving the flag in Flagsmith after approval")
            return CleanupPatch(id=stable_id("patch", flag_name, direction.value, base.scan.scan_id), flag_name=flag_name,
                                direction=direction, status=PatchStatus.NOT_GENERATED, base_scan_id=base.scan.scan_id, notes=[reason])
        bool_value = {CleanupDirection.KEEP_ENABLED_PATH: True, CleanupDirection.KEEP_DISABLED_PATH: False}.get(direction, enabled_state)

        overrides: dict[tuple[str, str], bytes] = {}
        edits_log: dict[tuple[str, str], list[AppliedEdit]] = {}
        manual: dict[str, ManualStep] = {}
        snap = base
        for _ in range(MAX_ROUNDS):
            refs = [r for r in snap.scan.references if r.flag_name == flag_name and r.classification == ReferenceClassification.CONFIRMED]
            per_file: dict[tuple[str, str], list[Edit]] = {}
            manual = {}
            for ref in refs:
                m, node = snap.node_for(ref)
                if node is None:
                    continue
                if ref.usage_type == UsageType.HOOK_SUBSCRIPTION:
                    res = subscription_edit(ref, node, m)
                elif ref.usage_type == UsageType.VALUE_RETRIEVAL or (
                        direction == CleanupDirection.INLINE_VALUE and ref.usage_type == UsageType.FLAG_OBJECT_RETRIEVAL):
                    res = (value_edit(ref, node, m, inline_value) if direction == CleanupDirection.INLINE_VALUE
                           else f"line {ref.location.line}: flag value is read; choose a value to inline manually")
                elif bool_value is None:
                    res = f"line {ref.location.line}: flag enabled state after cleanup is unknown"
                else:
                    res = boolean_edit(ref, node, m, bool_value)
                if isinstance(res, str):
                    manual[ref.id] = ManualStep(repository=ref.repository, file_path=ref.location.file_path,
                                                line=ref.location.line, reference_id=ref.id, reason=res)
                else:
                    per_file.setdefault((ref.repository, ref.location.file_path), []).append(res)
            applied_any = False
            for key, edits in per_file.items():
                m = snap.module(*key)
                new_src, chosen = _apply(m.src, edits)
                if chosen:
                    applied_any = True
                    overrides[key] = new_src
                    edits_log.setdefault(key, []).extend(
                        AppliedEdit(kind=e.kind, file_path=key[1], line=e.line, reference_id=e.reference_id,
                                    description=e.description, before=m.src[e.start:e.end].decode("utf-8", "replace"),
                                    after=e.replacement) for e in chosen)
            if not applied_any:
                break
            snap = CodebaseSnapshot(self.targets, overrides)

        # Tidy-up passes on changed files: emptied hook subscriptions, then imports that became unused.
        for key in list(overrides):
            for tidy in (self._tidy_subscriptions, self._tidy_imports):
                current = CodebaseSnapshot(self.targets, overrides).module(*key)
                original = base.module(*key)
                new_src, applied = tidy(current, original)
                if applied:
                    overrides[key] = new_src
                    edits_log[key].extend(applied)

        final = CodebaseSnapshot(self.targets, overrides)
        remaining = [r for r in final.scan.references if r.flag_name == flag_name]
        for r in remaining:
            if r.classification != ReferenceClassification.CONFIRMED:
                manual.setdefault(r.id, ManualStep(repository=r.repository, file_path=r.location.file_path, line=r.location.line,
                                                   reference_id=r.id, reason=f"{r.classification.value} reference: confirm and clean up manually"))
            elif r.id not in manual:
                manual[r.id] = ManualStep(repository=r.repository, file_path=r.location.file_path, line=r.location.line,
                                          reference_id=r.id, reason="reference could not be rewritten automatically")

        files = []
        for key, new_src in sorted(overrides.items()):
            old = base.module(*key).src
            if new_src == old:
                continue
            diff = "".join(difflib.unified_diff(
                old.decode("utf-8", "replace").splitlines(keepends=True), new_src.decode("utf-8", "replace").splitlines(keepends=True),
                fromfile=f"a/{key[1]}", tofile=f"b/{key[1]}"))
            files.append(FileChange(repository=key[0], file_path=key[1], original_sha256=sha256(old), patched_sha256=sha256(new_src),
                                    diff=diff, patched_content=new_src.decode("utf-8"), edits=edits_log.get(key, [])))

        follow_ups = self._follow_ups(base, final, {k for k in overrides})
        if direction == CleanupDirection.INLINE_VALUE:
            notes.append(f"inlined production value {inline_value!r}; fallback/type-guard expressions around it may now be simplifiable")
        status = (PatchStatus.NOT_GENERATED if not files else PatchStatus.PARTIAL if manual else PatchStatus.COMPLETE)
        pid = stable_id("patch", flag_name, direction.value, repr(inline_value), base.scan.scan_id,
                        *(f"{f.file_path}:{f.patched_sha256}" for f in files))
        return CleanupPatch(id=pid, flag_name=flag_name, direction=direction, inline_value=inline_value, status=status,
                            files=files, manual_steps=list(manual.values()), follow_ups=follow_ups,
                            base_scan_id=base.scan.scan_id, notes=notes)

    # ------------------------------------------------------------------
    def _tidy_subscriptions(self, m: ModuleInfo, original: ModuleInfo) -> tuple[bytes, list[AppliedEdit]]:
        """Drop useFlags() bindings that only the cleanup made unused."""
        src = m.src
        edits: list[Edit] = []

        def unused_by_cleanup(name: str) -> bool:  # declaration itself counts once
            return _ident_count(original, name) > 1 and _ident_count(m, name) <= 1

        for d in A.walk(m.root):
            if d.type != "variable_declarator":
                continue
            v = A.unwrap(d.child_by_field_name("value"))
            fn = v.child_by_field_name("function") if v is not None and v.type == "call_expression" else None
            b = m.bindings.get(A.text(fn, src)) if fn is not None else None
            if b is None or b.kind.value != "use_flags_hook":
                continue
            stmt = d.parent
            if stmt is None or stmt.type not in ("lexical_declaration", "variable_declaration") or len(stmt.named_children) != 1:
                continue
            args = [a for a in v.child_by_field_name("arguments").named_children if a.type != "comment"]
            args_empty = all(a.type == "array" and not a.named_children for a in args)
            name = d.child_by_field_name("name")
            line = stmt.start_point[0] + 1
            if name.type == "identifier":
                dead_all = unused_by_cleanup(A.text(name, src))
                entries, dead = [], []
            elif name.type == "object_pattern":
                entries = [e for e in name.named_children if e.type in ("shorthand_property_identifier_pattern", "pair_pattern")]
                local = lambda e: A.text(e if e.type != "pair_pattern" else e.child_by_field_name("value"), src)
                dead = [e for e in entries if unused_by_cleanup(local(e))]
                dead_all = bool(entries) and len(dead) == len(entries)
            else:
                continue
            if dead_all:
                if args_empty:
                    s, e = _whole_line_removal(src, stmt.start_byte, stmt.end_byte)
                    edits.append(Edit(s, e, "", EditKind.REMOVE_STATEMENT, "useFlags() no longer subscribes to any flag; removed", None, line))
                else:
                    call = A.text(v, src)
                    edits.append(Edit(stmt.start_byte, stmt.end_byte, f"{call};", EditKind.REMOVE_STATEMENT,
                                      "unused useFlags() result binding removed; remaining subscriptions kept", None, line))
            else:
                for e in dead:
                    i = entries.index(e)
                    start, end = (e.start_byte, entries[i + 1].start_byte) if i < len(entries) - 1 else (entries[i - 1].end_byte, e.end_byte)
                    edits.append(Edit(start, end, "", EditKind.REMOVE_STATEMENT, f"unused `{A.text(e, src)}` binding removed", None, line))
        return self._as_applied(m, edits)

    def _tidy_imports(self, m: ModuleInfo, original: ModuleInfo) -> tuple[bytes, list[AppliedEdit]]:
        src = m.src
        before, after = _identifier_uses(original), _identifier_uses(m)
        edits: list[Edit] = []
        for stmt in m.root.named_children:
            if stmt.type != "import_statement":
                continue
            locals_: list = []
            for n in A.walk(stmt):
                if n.type == "identifier" and n.parent is not None and n.parent.type in ("import_clause", "import_specifier", "namespace_import"):
                    if n.parent.type == "import_specifier" and n.parent.child_by_field_name("alias") not in (None, n):
                        continue
                    locals_.append(n)
            dead = [n for n in locals_ if before[A.text(n, src)] > 0 and after[A.text(n, src)] == 0]
            if not dead:
                continue
            if len(dead) == len(locals_):
                s, e = _whole_line_removal(src, stmt.start_byte, stmt.end_byte)
                edits.append(Edit(s, e, "", EditKind.REMOVE_UNUSED_IMPORT,
                                  f"import of {', '.join(A.text(n, src) for n in dead)} no longer used; removed", None, stmt.start_point[0] + 1))
                continue
            for n in dead:
                spec = n.parent
                if spec.type != "import_specifier":
                    continue  # default/namespace import partially unused: leave for review
                named = spec.parent
                specs = [c for c in named.named_children if c.type == "import_specifier"]
                i = specs.index(spec)
                start, end = (spec.start_byte, specs[i + 1].start_byte) if i < len(specs) - 1 else (specs[i - 1].end_byte, spec.end_byte)
                edits.append(Edit(start, end, "", EditKind.REMOVE_UNUSED_IMPORT, f"`{A.text(n, src)}` no longer used; import removed",
                                  None, stmt.start_point[0] + 1))
        return self._as_applied(m, edits)

    @staticmethod
    def _as_applied(m: ModuleInfo, edits: list[Edit]) -> tuple[bytes, list[AppliedEdit]]:
        if not edits:
            return m.src, []
        new, chosen = _apply(m.src, edits)
        return new, [AppliedEdit(kind=e.kind, file_path=m.path, line=e.line, reference_id=e.reference_id, description=e.description,
                                 before=m.src[e.start:e.end].decode("utf-8", "replace"), after=e.replacement) for e in chosen]

    @staticmethod
    def _follow_ups(base: CodebaseSnapshot, final: CodebaseSnapshot, changed: set) -> list[str]:
        b, f = SymbolIndex.build(base), SymbolIndex.build(final)
        out = []
        for key in sorted(changed):
            before, after = base.module(*key), final.module(*key)
            for name, binding in sorted(after.bindings.items()):
                if binding.kind.value in ("server_flags", "client", "react_flags") and \
                        _ident_count(before, name) > 1 and _ident_count(after, name) <= 1:
                    out.append(f"`{name}` ({binding.kind.value}) in {key[0]}:{key[1]} is no longer used; the code that "
                               "creates or passes it may be removable (Flagsmith calls can have side effects such as network requests) - review")
        for name, defs in sorted(b.definitions.items()):
            if not b.usages.get(name) or f.usages.get(name):
                continue
            d = defs[0]
            file_defs = [n for n, ds in b.definitions.items() if any(x.module.path == d.module.path for x in ds)]
            only_symbol = file_defs == [name]
            out.append(f"`{name}` ({d.kind}, {d.module.repository}:{d.module.path}) is no longer used"
                       + ("; the whole file may be removable" if only_symbol else "; consider removing it") + " - review before deleting")
        return out


def _ident_count(m: ModuleInfo, name: str) -> int:
    return sum(1 for n in A.walk(m.root) if n.type in ("identifier", "shorthand_property_identifier_pattern") and A.text(n, m.src) == name)
