"""Build the evidence packet: the *only* facts the planner (and any LLM) may cite."""

from __future__ import annotations

from ..schemas.analysis import FlagAnalysisReport
from ..schemas.common import stable_id
from ..schemas.patch import CleanupPatch
from ..schemas.planning import EvidenceRef
from ..schemas.stale import StaleFlagFinding


def build_evidence(analysis: FlagAnalysisReport | None, stale: StaleFlagFinding,
                   patch: CleanupPatch | None) -> tuple[list[EvidenceRef], dict[str, str]]:
    """Return (evidence refs, id -> direction for stale signals)."""
    out: list[EvidenceRef] = []
    directions: dict[str, str] = {}
    if analysis is not None:
        for r in analysis.references:
            out.append(EvidenceRef(id=r.id, kind="code_reference", location=r.location,
                                   summary=f"{r.classification.value} {r.api.value} ({r.usage_type.value}) at "
                                           f"{r.repository}:{r.location.file_path}:{r.location.line}"))
        for r in analysis.unresolved_dynamic_references:
            out.append(EvidenceRef(id=r.id, kind="dynamic_reference", location=r.location,
                                   summary=f"dynamic flag name `{r.argument_expression}` at {r.repository}:{r.location.file_path}:{r.location.line}"))
        for b in analysis.branches:
            out.append(EvidenceRef(id=b.id, kind="branch", location=b.construct_span,
                                   summary=f"{b.kind.value} on `{b.condition}` ({'determinable' if b.determinable else 'indeterminate'})"))
        for s in analysis.related_symbols:
            if s.defined_at is None:
                continue
            out.append(EvidenceRef(id=stable_id("sym", analysis.id, s.name, s.relation.value, length=12), kind="related_symbol",
                                   location=s.defined_at,
                                   summary=f"{s.kind} `{s.name}` ({s.relation.value}"
                                           + (", only used behind the flag)" if s.exclusively_flag_gated else ")")))
        for t in analysis.related_tests:
            out.append(EvidenceRef(id=stable_id("test", t.repository, t.file_path, length=12), kind="related_test",
                                   summary=f"{t.strength} test association {t.repository}:{t.file_path}: {'; '.join(t.reasons)}"))
        for f in analysis.findings:
            out.append(EvidenceRef(id=f.id, kind="analysis_finding", location=f.location,
                                   summary=("[hypothesis] " if f.is_hypothesis else "") + f.message))
    for e in stale.evidence:
        out.append(EvidenceRef(id=e.id, kind=f"stale:{e.kind}", summary=f"[{e.direction.value}] {e.description}"))
        directions[e.id] = e.direction.value
    for b in stale.blockers:
        out.append(EvidenceRef(id=stable_id("blk", stale.id, b.kind, length=12), kind="blocker", summary=b.description))
    for m in stale.missing_evidence:
        out.append(EvidenceRef(id=stable_id("miss", stale.id, m.kind, length=12), kind="missing_evidence", summary=m.description))
    if patch is not None:
        for fc in patch.files:
            for e in fc.edits:
                out.append(EvidenceRef(id=stable_id("edit", patch.id, fc.repository, fc.file_path, e.line, e.description, length=12),
                                       kind="patch_edit", summary=f"{fc.repository}:{fc.file_path}:{e.line} {e.kind.value}: {e.description}"))
        for ms in patch.manual_steps:
            out.append(EvidenceRef(id=stable_id("manual", patch.id, ms.file_path, ms.line, ms.reason, length=12), kind="manual_step",
                                   summary=f"{ms.repository}:{ms.file_path}:{ms.line} manual: {ms.reason}"))
    return out, directions


def allowed_locations(evidence: list[EvidenceRef], patch: CleanupPatch | None,
                      analysis: FlagAnalysisReport | None) -> dict[str, set[int]]:
    """file path -> known line numbers (any line of a known span counts; empty set = file known, no lines)."""
    loc: dict[str, set[int]] = {}
    for t in (analysis.related_tests if analysis else []):
        loc.setdefault(t.file_path, set())
    for e in evidence:
        if e.location is not None:
            loc.setdefault(e.location.file_path, set()).update(range(e.location.line, e.location.end_line + 1))
    if patch is not None:
        for fc in patch.files:
            loc.setdefault(fc.file_path, set()).update(e.line for e in fc.edits)
        for ms in patch.manual_steps:
            loc.setdefault(ms.file_path, set()).add(ms.line)
    return loc
