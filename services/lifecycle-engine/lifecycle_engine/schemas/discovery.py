"""Module 1 — feature flag discovery schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from .common import EngineModel, SourceSpan, utcnow


class FlagsmithSdk(str, Enum):
    JS_CLIENT = "flagsmith-js"          # 'flagsmith', 'flagsmith/isomorphic', 'flagsmith-es', 'react-native-flagsmith'
    REACT = "flagsmith-react"           # 'flagsmith/react'
    NODE_SERVER = "flagsmith-nodejs"    # 'flagsmith-nodejs'
    UNKNOWN = "unknown"


class FlagApi(str, Enum):
    HAS_FEATURE = "hasFeature"
    GET_VALUE = "getValue"
    GET_TRAIT = "getTrait"
    IS_FEATURE_ENABLED = "isFeatureEnabled"
    GET_FEATURE_VALUE = "getFeatureValue"
    GET_FLAG = "getFlag"
    USE_FLAGS_SUBSCRIPTION = "useFlags"
    FLAGS_OBJECT_ACCESS = "flags.<name>"


class UsageType(str, Enum):
    BOOLEAN_EVALUATION = "boolean_evaluation"
    VALUE_RETRIEVAL = "value_retrieval"
    FLAG_OBJECT_RETRIEVAL = "flag_object_retrieval"
    HOOK_SUBSCRIPTION = "hook_subscription"
    TRAIT_READ = "trait_read"


class ReferenceClassification(str, Enum):
    CONFIRMED = "confirmed"   # receiver resolved to a Flagsmith binding and name is static
    POTENTIAL = "potential"   # API shape matches but receiver could not be tied to Flagsmith
    DYNAMIC = "dynamic"       # Flagsmith API call whose flag name cannot be resolved statically


class NameResolution(str, Enum):
    LITERAL = "literal"
    LOCAL_CONSTANT = "local_constant"
    IMPORTED_CONSTANT = "imported_constant"
    UNRESOLVED = "unresolved"


class ReferenceContext(EngineModel):
    snippet: str = Field(description="The source line(s) containing the reference.")
    enclosing_function: str | None = None
    in_conditional: bool = False
    is_test_file: bool = False


class FlagReference(EngineModel):
    id: str
    repository: str
    location: SourceSpan
    flag_name: str | None = Field(description="Statically resolved flag name; None for dynamic references and trait reads.")
    trait_key: str | None = Field(default=None, description="Trait key, only for usage_type=trait_read.")
    sdk: FlagsmithSdk
    api: FlagApi
    usage_type: UsageType
    classification: ReferenceClassification
    is_dynamic: bool
    name_resolution: NameResolution
    argument_expression: str | None = Field(default=None, description="Raw source of the flag-name argument.")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list, description="Human-readable reasons for the classification.")
    context: ReferenceContext


class FileScanError(EngineModel):
    repository: str
    file_path: str
    error: str


class FlagSummary(EngineModel):
    flag_name: str
    confirmed: int = 0
    potential: int = 0
    files: list[str] = Field(default_factory=list)
    repositories: list[str] = Field(default_factory=list)


class ScanSummary(EngineModel):
    confirmed: int = 0
    potential: int = 0
    dynamic: int = 0
    trait_reads: int = 0
    flags: list[FlagSummary] = Field(default_factory=list)


class RepositoryTarget(EngineModel):
    repository: str = Field(description="Stable repository identifier, e.g. 'acme/checkout-web'.")
    root_path: str = Field(description="Local filesystem path to a checkout of the repository.")


class ScanResult(EngineModel):
    scan_id: str
    engine_version: str
    generated_at: datetime = Field(default_factory=utcnow)
    repositories: list[str]
    files_scanned: int
    files_with_references: int
    errors: list[FileScanError] = Field(default_factory=list)
    references: list[FlagReference] = Field(default_factory=list, description="Flag references (confirmed, potential, dynamic).")
    trait_reads: list[FlagReference] = Field(default_factory=list, description="Trait reads; related to evaluation but not flags.")
    summary: ScanSummary

    def for_flag(self, flag_name: str) -> list[FlagReference]:
        return [r for r in self.references if r.flag_name == flag_name]

    def dynamic(self) -> list[FlagReference]:
        return [r for r in self.references if r.classification == ReferenceClassification.DYNAMIC]
