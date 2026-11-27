"""Module 2 — code intelligence schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from .common import EngineModel, SourceSpan, utcnow
from .discovery import FlagReference


class BranchKind(str, Enum):
    IF_ELSE = "if_else"
    IF = "if"
    GUARD_CLAUSE = "guard_clause"
    TERNARY = "ternary"
    LOGICAL_AND = "logical_and"
    LOGICAL_OR = "logical_or"


class Polarity(str, Enum):
    POSITIVE = "positive"   # condition true when the flag is enabled
    NEGATIVE = "negative"   # condition true when the flag is disabled


class CodePath(EngineModel):
    span: SourceSpan
    excerpt: str
    callees: list[str] = Field(default_factory=list)
    rendered_components: list[str] = Field(default_factory=list)
    is_empty: bool = False
    description: str


class FlagBranch(EngineModel):
    id: str
    reference_id: str
    kind: BranchKind
    polarity: Polarity
    condition: str
    condition_is_exact: bool = Field(description="True when the flag check is the entire condition (modulo negation/boolean comparison).")
    construct_span: SourceSpan
    enabled_path: CodePath | None = None
    disabled_path: CodePath | None = None
    determinable: bool = Field(description="Whether enabled/disabled paths were determined with certainty.")
    via_alias: str | None = Field(default=None, description="Variable the flag value was stored in, if any.")
    notes: list[str] = Field(default_factory=list)


class FallbackKind(str, Enum):
    OPTION_FALLBACK = "sdk_fallback_option"
    LOGICAL_OR_DEFAULT = "logical_or_default"
    NULLISH_DEFAULT = "nullish_default"
    TYPE_GUARD_DEFAULT = "type_guard_default"


class FallbackBehavior(EngineModel):
    reference_id: str
    kind: FallbackKind
    default_expression: str
    location: SourceSpan


class SymbolRelation(str, Enum):
    ENCLOSING = "enclosing"
    ENABLED_PATH = "used_in_enabled_path"
    DISABLED_PATH = "used_in_disabled_path"


class RelatedSymbol(EngineModel):
    name: str
    kind: str = Field(description="'function', 'component', 'class' or 'method'.")
    relation: SymbolRelation
    defined_at: SourceSpan | None = None
    exclusively_flag_gated: bool | None = Field(
        default=None, description="True when every non-definition use of the symbol in scanned code sits inside this path."
    )
    other_usages: int = 0


class RelatedTest(EngineModel):
    repository: str
    file_path: str
    strength: str = Field(description="'strong' or 'weak'.")
    reasons: list[str]


class FeatureScope(str, Enum):
    FULL_FEATURE_CANDIDATE = "full_feature_candidate"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_REFERENCED = "not_referenced"


class FindingKind(str, Enum):
    REDUNDANT_NESTED_CHECK = "redundant_nested_check"
    IDENTICAL_BRANCHES = "identical_branches"
    POTENTIALLY_UNREACHABLE_BRANCH = "potentially_unreachable_branch"
    FLAG_VALUE_ESCAPES_SCOPE = "flag_value_escapes_scope"
    COMPOUND_CONDITION = "compound_condition"
    SUBSCRIPTION_WITHOUT_EVALUATION = "subscription_without_evaluation"
    UNANALYZED_USAGE = "unanalyzed_usage"


class Finding(EngineModel):
    id: str
    kind: FindingKind
    severity: str = Field(description="'info' or 'warning'.")
    message: str
    reference_ids: list[str] = Field(default_factory=list)
    location: SourceSpan | None = None
    is_hypothesis: bool = Field(default=False, description="True when the finding depends on assumptions beyond static facts.")


class UncertaintyKind(str, Enum):
    DYNAMIC_REFERENCE = "dynamic_reference"
    POTENTIAL_REFERENCE = "potential_reference"
    NO_REFERENCES_FOUND = "no_references_found"
    INDETERMINATE_BRANCH = "indeterminate_branch"
    FLAG_VALUE_ESCAPES = "flag_value_escapes"
    UNSCANNED_CONSUMERS = "unscanned_consumers"
    PARSE_ERRORS = "parse_errors"


class Uncertainty(EngineModel):
    kind: UncertaintyKind
    description: str
    reference_ids: list[str] = Field(default_factory=list)


class FlagAnalysisReport(EngineModel):
    id: str
    flag_name: str
    generated_at: datetime = Field(default_factory=utcnow)
    repositories: list[str]
    references: list[FlagReference] = Field(description="Confirmed and potential references to this flag.")
    usage_patterns: dict[str, int] = Field(description="usage_type -> count")
    branches: list[FlagBranch] = Field(default_factory=list)
    fallbacks: list[FallbackBehavior] = Field(default_factory=list)
    related_symbols: list[RelatedSymbol] = Field(default_factory=list)
    related_tests: list[RelatedTest] = Field(default_factory=list)
    feature_scope: FeatureScope
    feature_scope_evidence: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    unresolved_dynamic_references: list[FlagReference] = Field(
        default_factory=list, description="Dynamic references in scanned repositories that might evaluate this flag."
    )
    uncertainties: list[Uncertainty] = Field(default_factory=list)
