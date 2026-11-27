"""Module 2 — per-flag code intelligence reports built purely from syntax trees."""

from __future__ import annotations

import re
from collections import Counter

from ..discovery import ast_utils as A
from ..discovery.ecmascript import is_test_file
from ..discovery.scanner import CodebaseSnapshot
from ..schemas.analysis import (
    FeatureScope,
    Finding,
    FindingKind,
    FlagAnalysisReport,
    FlagBranch,
    RelatedSymbol,
    RelatedTest,
    SymbolRelation,
    Uncertainty,
    UncertaintyKind,
)
from ..schemas.common import SourceSpan, stable_id
from ..schemas.discovery import ReferenceClassification, RepositoryTarget, UsageType
from ..schemas.flags import FlagConfiguration
from .branches import BranchAnalysis, analyze_boolean_use, analyze_fallback
from .dynamic import matching_dynamic_references
from .spans import contains
from .symbols import SymbolIndex


class CodeIntelligence:
    """Analyzes flags over a parsed `CodebaseSnapshot`. Build once, analyze many flags."""

    def __init__(self, snapshot: CodebaseSnapshot):
        self.snapshot = snapshot
        self.symbols = SymbolIndex.build(snapshot)

    @classmethod
    def from_targets(cls, targets: list[RepositoryTarget]) -> "CodeIntelligence":
        return cls(CodebaseSnapshot(targets))

    # ------------------------------------------------------------------
    def analyze(self, flag_name: str, config: FlagConfiguration | None = None) -> FlagAnalysisReport:
        scan = self.snapshot.scan
        refs = [r for r in scan.references if r.flag_name == flag_name]
        branches: list[FlagBranch] = []
        fallbacks = []
        escapes: list[tuple[str, str]] = []
        unanalyzed: list[tuple[str, str]] = []

        for ref in refs:
            m, node = self.snapshot.node_for(ref)
            if node is None:
                unanalyzed.append((ref.id, "reference node could not be located in the parsed tree"))
                continue
            if ref.usage_type == UsageType.FLAG_OBJECT_RETRIEVAL:
                p = node.parent
                if p is not None and p.type == "member_expression" and A.text(p.child_by_field_name("property"), m.src) == "enabled":
                    node = p
                    ref_usage = UsageType.BOOLEAN_EVALUATION
                else:
                    ref_usage = ref.usage_type
            else:
                ref_usage = ref.usage_type
            if ref_usage == UsageType.BOOLEAN_EVALUATION:
                ba: BranchAnalysis = analyze_boolean_use(ref, node, m)
                branches.extend(ba.branches)
                escapes.extend((ref.id, e) for e in ba.escapes)
                unanalyzed.extend((ref.id, u) for u in ba.unanalyzed)
            elif ref_usage == UsageType.VALUE_RETRIEVAL:
                fallbacks.extend(analyze_fallback(ref, node, m))

        related_symbols = self._related_symbols(refs, branches)
        related_tests = self._related_tests(flag_name, refs, related_symbols)
        dynamic = matching_dynamic_references(scan.references, flag_name)
        scope, scope_evidence = self._feature_scope(refs, branches, related_symbols)
        findings = self._findings(flag_name, refs, branches, escapes, unanalyzed, config)
        uncertainties = self._uncertainties(flag_name, refs, branches, escapes, dynamic, scan)

        return FlagAnalysisReport(
            id=stable_id("analysis", flag_name, scan.scan_id),
            flag_name=flag_name,
            repositories=scan.repositories,
            references=refs,
            usage_patterns=dict(sorted(Counter(r.usage_type.value for r in refs).items())),
            branches=branches,
            fallbacks=fallbacks,
            related_symbols=related_symbols,
            related_tests=related_tests,
            feature_scope=scope,
            feature_scope_evidence=scope_evidence,
            findings=findings,
            unresolved_dynamic_references=dynamic,
            uncertainties=uncertainties,
        )

    def analyze_all(self, configs: dict[str, FlagConfiguration] | None = None) -> list[FlagAnalysisReport]:
        names = sorted({r.flag_name for r in self.snapshot.scan.references if r.flag_name} | set(configs or {}))
        return [self.analyze(n, (configs or {}).get(n)) for n in names]

    # ------------------------------------------------------------------
    def _related_symbols(self, refs, branches: list[FlagBranch]) -> list[RelatedSymbol]:
        out: dict[tuple[str, str], RelatedSymbol] = {}
        for ref in refs:
            fn = ref.context.enclosing_function
            if fn:
                base = fn.split(".")[0]
                d = self.symbols.definition(base, ref.repository)
                out.setdefault((fn, "enc"), RelatedSymbol(
                    name=fn, kind=("method" if "." in fn else (d.kind if d else "function")),
                    relation=SymbolRelation.ENCLOSING, defined_at=d.span if d else None))
        for relation, attr in ((SymbolRelation.ENABLED_PATH, "enabled_path"), (SymbolRelation.DISABLED_PATH, "disabled_path")):
            paths = [getattr(b, attr) for b in branches if getattr(b, attr) is not None]
            names = sorted({n for p in paths for n in (*p.callees, *p.rendered_components)})
            for name in names:
                d = self.symbols.definition(name, paths[0].span.repository)
                if d is None:
                    continue  # not defined in scanned code (builtin / third-party)
                usages = [u for u in self.symbols.usages.get(name, [])]
                outside = [u for u in usages if not any(_usage_in(u, p.span) for p in paths)]
                out.setdefault((name, relation.value), RelatedSymbol(
                    name=name, kind=d.kind, relation=relation, defined_at=d.span,
                    exclusively_flag_gated=not outside, other_usages=len(outside)))
        return list(out.values())

    def _related_tests(self, flag_name, refs, symbols: list[RelatedSymbol]) -> list[RelatedTest]:
        ref_files = {(r.repository, r.location.file_path) for r in refs}
        sym_names = {s.name.split(".")[-1] for s in symbols}
        tests: dict[tuple[str, str], RelatedTest] = {}
        name_re = re.compile(rf"""["'`]{re.escape(flag_name)}["'`]""")
        for repo, index in self.snapshot.indexes.items():
            for m in index.modules.values():
                if not is_test_file(m.path):
                    continue
                reasons, strong = [], False
                text = m.src.decode("utf-8", "replace")
                if name_re.search(text):
                    reasons.append(f"mentions flag name '{flag_name}'")
                    strong = True
                for imp in m.imports:
                    target = index.resolve(m.path, imp.specifier)
                    if target is not None and (repo, target.path) in ref_files:
                        reasons.append(f"imports flag-controlled module {target.path}")
                        strong = True
                used = sorted(n for n in sym_names if re.search(rf"\b{re.escape(n)}\b", text))
                if used:
                    reasons.append("references related symbols: " + ", ".join(used))
                if reasons:
                    tests[(repo, m.path)] = RelatedTest(repository=repo, file_path=m.path,
                                                        strength="strong" if strong else "weak", reasons=reasons)
        return list(tests.values())

    def _feature_scope(self, refs, branches, symbols: list[RelatedSymbol]):
        if not refs:
            return FeatureScope.NOT_REFERENCED, ["no confirmed or potential references in scanned repositories"]
        gated = [s for s in symbols if s.relation != SymbolRelation.ENCLOSING and s.exclusively_flag_gated]
        evidence = [
            f"`{s.name}` ({s.kind}, {s.defined_at.file_path if s.defined_at else '?'}) is only used in the flag's "
            f"{'enabled' if s.relation == SymbolRelation.ENABLED_PATH else 'disabled'} path"
            for s in gated
        ]
        if gated:
            return FeatureScope.FULL_FEATURE_CANDIDATE, evidence + [
                "candidate only: usages outside scanned repositories cannot be ruled out"]
        if branches:
            return FeatureScope.PARTIAL, ["flag gates inline code paths; no separately defined unit is exclusively gated"]
        return FeatureScope.UNKNOWN, ["flag is read (value/subscription) but does not gate a detected branch"]

    def _findings(self, flag_name, refs, branches: list[FlagBranch], escapes, unanalyzed, config) -> list[Finding]:
        out: list[Finding] = []
        for b in branches:
            for other in branches:
                if other is b:
                    continue
                for path in (other.enabled_path, other.disabled_path):
                    if path is not None and contains(path.span, b.construct_span):
                        side = "enabled" if path is other.enabled_path else "disabled"
                        out.append(Finding(
                            id=stable_id("finding", "nested", b.id, other.id), kind=FindingKind.REDUNDANT_NESTED_CHECK,
                            severity="warning", reference_ids=[b.reference_id], location=b.construct_span,
                            message=f"'{flag_name}' is re-checked inside its own {side} path (line {other.construct_span.line}); the inner check is redundant"))
            if b.enabled_path and b.disabled_path and _norm(b.enabled_path.excerpt) == _norm(b.disabled_path.excerpt):
                out.append(Finding(id=stable_id("finding", "identical", b.id), kind=FindingKind.IDENTICAL_BRANCHES,
                                   severity="warning", reference_ids=[b.reference_id], location=b.construct_span,
                                   message="enabled and disabled paths are textually identical; the flag check has no effect"))
            if not b.condition_is_exact:
                out.append(Finding(id=stable_id("finding", "compound", b.id), kind=FindingKind.COMPOUND_CONDITION,
                                   severity="info", reference_ids=[b.reference_id], location=b.construct_span,
                                   message=f"flag is combined with other conditions in `{b.condition}`"))
        if config is not None:
            state = config.uniform_production_state()
            all_envs_same = config.environments and all(e.is_uniform for e in config.environments) and len({e.enabled for e in config.environments}) == 1
            if state is not None:
                scope = "all known environments" if all_envs_same else "production environments"
                for b in branches:
                    dead = b.disabled_path if state else b.enabled_path
                    if dead is not None and b.determinable:
                        out.append(Finding(
                            id=stable_id("finding", "unreachable", b.id, state), kind=FindingKind.POTENTIALLY_UNREACHABLE_BRANCH,
                            severity="info", reference_ids=[b.reference_id], location=dead.span, is_hypothesis=True,
                            message=(f"the {'disabled' if state else 'enabled'} path is not taken in {scope} under the current "
                                     f"configuration (flag uniformly {'on' if state else 'off'}); it may still run in other "
                                     "environments, local development, tests or after a configuration change")))
        for ref_id, e in escapes:
            out.append(Finding(id=stable_id("finding", "escape", ref_id, e), kind=FindingKind.FLAG_VALUE_ESCAPES_SCOPE,
                               severity="warning", reference_ids=[ref_id], message=e))
        for ref_id, u in unanalyzed:
            out.append(Finding(id=stable_id("finding", "unanalyzed", ref_id, u), kind=FindingKind.UNANALYZED_USAGE,
                               severity="warning", reference_ids=[ref_id], message=u))
        subs = [r for r in refs if r.usage_type == UsageType.HOOK_SUBSCRIPTION]
        for s in subs:
            evaluated = any(r.location.file_path == s.location.file_path and r.repository == s.repository
                            and r.usage_type != UsageType.HOOK_SUBSCRIPTION for r in refs)
            if not evaluated:
                out.append(Finding(id=stable_id("finding", "sub", s.id), kind=FindingKind.SUBSCRIPTION_WITHOUT_EVALUATION,
                                   severity="info", reference_ids=[s.id], location=s.location,
                                   message="flag is subscribed via useFlags() but never evaluated in this file"))
        return out

    def _uncertainties(self, flag_name, refs, branches, escapes, dynamic, scan) -> list[Uncertainty]:
        out: list[Uncertainty] = []
        if not refs:
            out.append(Uncertainty(kind=UncertaintyKind.NO_REFERENCES_FOUND,
                                   description=f"no references to '{flag_name}' were found in {', '.join(scan.repositories)}; "
                                               "this does not prove the flag is unused (other codebases, clients or dynamic names may evaluate it)"))
        potential = [r for r in refs if r.classification == ReferenceClassification.POTENTIAL]
        if potential:
            out.append(Uncertainty(kind=UncertaintyKind.POTENTIAL_REFERENCE, reference_ids=[r.id for r in potential],
                                   description=f"{len(potential)} reference(s) could not be confirmed as Flagsmith calls"))
        if dynamic:
            out.append(Uncertainty(kind=UncertaintyKind.DYNAMIC_REFERENCE, reference_ids=[r.id for r in dynamic],
                                   description=f"{len(dynamic)} dynamic flag reference(s) could evaluate '{flag_name}' at runtime: "
                                               + "; ".join(f"{r.location.file_path}:{r.location.line} `{r.argument_expression}`" for r in dynamic)))
        indeterminate = [b for b in branches if not b.determinable]
        if indeterminate:
            out.append(Uncertainty(kind=UncertaintyKind.INDETERMINATE_BRANCH, reference_ids=[b.reference_id for b in indeterminate],
                                   description=f"{len(indeterminate)} branch(es) could not be resolved into enabled/disabled paths"))
        if escapes:
            out.append(Uncertainty(kind=UncertaintyKind.FLAG_VALUE_ESCAPES, reference_ids=sorted({r for r, _ in escapes}),
                                   description="flag value flows out of the evaluating function; downstream behaviour is not analyzed"))
        if scan.errors:
            out.append(Uncertainty(kind=UncertaintyKind.PARSE_ERRORS,
                                   description=f"{len(scan.errors)} file(s) had parse problems: " + ", ".join(e.file_path for e in scan.errors)))
        return out


def _usage_in(u, span: SourceSpan) -> bool:
    return (u.repository == span.repository and u.file_path == span.file_path
            and span.start_byte <= u.start_byte and u.end_byte <= span.end_byte)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
