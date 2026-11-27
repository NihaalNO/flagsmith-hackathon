"""Validate LLM output against deterministic evidence. Any violation rejects the output."""

from __future__ import annotations

import re

from pydantic import ValidationError

from ..schemas.planning import LLMPlanNarrative, StatementType
from ..schemas.stale import StaleStatus

FILE_RE = re.compile(r"(?<![\w@])((?:[\w.-]+/)*[\w.-]+\.(?:tsx?|jsx?|mjs|cjs|mts|cts|py|json|ya?ml))(?::(\d+))?")
SAFETY_CLAIM_RE = re.compile(r"\b(safe(?:ly)?|guaranteed|certain(?:ly)?)\s+(?:to\s+)?(delete|remove|retire|archive)\b", re.I)
TEST_CLAIM_RE = re.compile(r"\btests?\s+(?:have\s+|has\s+|all\s+)?(pass(?:ed|es)?|succeed(?:ed|s)?|green)\b", re.I)
STATE_CLAIM_RE = re.compile(r"\b(?:flag\s+)?(?:is|was|has been|is now)\s+(retired|archived|merged)\b", re.I)
NEGATORS_RE = re.compile(r"\b(not|never|no|until|before|once|after|if|when|unless|whether|cannot|can't|isn't|is not)\b", re.I)


def _affirmative(pattern: re.Pattern, text: str) -> re.Match | None:
    """First match that is not negated or conditional ('not safe to delete', 'until it is merged')."""
    for m in pattern.finditer(text):
        window = text[max(0, m.start() - 30) : m.start()]
        if not NEGATORS_RE.search(window):
            return m
    return None


def _texts(n: LLMPlanNarrative) -> list[str]:
    out = [n.reasoning_summary, *(s.text for s in n.statements), *(r.description for r in n.risks),
           *(r.mitigation or "" for r in n.risks)]
    if n.patch_rationale:
        out.append(n.patch_rationale)
    return out


def validate_narrative(raw: dict, *, evidence_ids: set[str], locations: dict[str, set[int]],
                       stale_status: StaleStatus, archived: bool = False) -> tuple[LLMPlanNarrative | None, list[str]]:
    try:
        n = LLMPlanNarrative.model_validate(raw)
    except ValidationError as exc:
        return None, [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()]
    errors: list[str] = []
    for i, s in enumerate(n.statements):
        unknown = [e for e in s.evidence_ids if e not in evidence_ids]
        if unknown:
            errors.append(f"statement {i} cites unknown evidence ids {unknown}")
        if s.type == StatementType.FACT and not s.evidence_ids:
            errors.append(f"statement {i} is labelled a fact but cites no evidence")
    for i, r in enumerate(n.risks):
        unknown = [e for e in r.evidence_ids if e not in evidence_ids]
        if unknown:
            errors.append(f"risk {i} cites unknown evidence ids {unknown}")
    known_files = set(locations)
    for text in _texts(n):
        for m in FILE_RE.finditer(text):
            path, line = m.group(1), m.group(2)
            match = next((f for f in known_files if f == path or f.endswith("/" + path)), None)
            if match is None:
                errors.append(f"mentions file '{path}' that is not in the analysis evidence")
            elif line is not None and locations[match] and int(line) not in locations[match]:
                errors.append(f"mentions {path}:{line}, which is not a known reference/edit location")
        safety = _affirmative(SAFETY_CLAIM_RE, text)
        if safety and stale_status != StaleStatus.SAFE_TO_RETIRE:
            errors.append(f"claims safety ('{safety.group(0)}') not established by the stale assessment")
        if _affirmative(TEST_CLAIM_RE, text):
            errors.append("claims test results; no tests have been run at planning time")
        sm = _affirmative(STATE_CLAIM_RE, text)
        if sm and not archived:
            errors.append(f"claims lifecycle/repository state ('{sm.group(0)}') not present in evidence")
    return (None, errors) if errors else (n, [])
