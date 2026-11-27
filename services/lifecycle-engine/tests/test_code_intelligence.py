from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lifecycle_engine.analyzers.code_intelligence import CodeIntelligence
from lifecycle_engine.analyzers.dynamic import may_match
from lifecycle_engine.schemas.analysis import (
    BranchKind,
    FallbackKind,
    FeatureScope,
    FindingKind,
    Polarity,
    SymbolRelation,
    UncertaintyKind,
)
from lifecycle_engine.schemas.discovery import RepositoryTarget
from lifecycle_engine.schemas.flags import EnvironmentFlagState, FlagConfiguration


@pytest.fixture(scope="module")
def ci(sample_targets_module):
    return CodeIntelligence.from_targets(sample_targets_module)


@pytest.fixture(scope="module")
def sample_targets_module():
    from tests.conftest import CHECKOUT, PRICING

    return [CHECKOUT, PRICING]


@pytest.fixture
def analyze(make_repo):
    def _a(files, flag, config=None):
        root = make_repo(files)
        return CodeIntelligence.from_targets([RepositoryTarget(repository="t/r", root_path=str(root))]).analyze(flag, config)

    return _a


def test_if_else_paths(analyze):
    r = analyze({"a.ts": """
        import f from "flagsmith";
        export function run() {
          if (f.hasFeature("x")) {
            newThing();
          } else {
            oldThing();
          }
        }
        function newThing() {}
        function oldThing() {}
    """}, "x")
    [b] = r.branches
    assert b.kind == BranchKind.IF_ELSE and b.polarity == Polarity.POSITIVE and b.determinable
    assert b.enabled_path.callees == ["newThing"] and b.disabled_path.callees == ["oldThing"]
    assert b.enabled_path.span.line == 4 and b.disabled_path.span.line == 6


@pytest.mark.parametrize("cond,polarity", [
    ('!f.hasFeature("x")', Polarity.NEGATIVE),
    ('f.hasFeature("x") === false', Polarity.NEGATIVE),
    ('!(f.hasFeature("x") !== true)', Polarity.POSITIVE),
    ('(await f.hasFeature("x"))', Polarity.POSITIVE),
])
def test_polarity_normalisation(analyze, cond, polarity):
    r = analyze({"a.ts": f"""
        import f from "flagsmith";
        async function g() {{ if ({cond}) {{ a(); }} else {{ b(); }} }}
    """}, "x")
    [b] = r.branches
    assert b.polarity == polarity
    assert (b.enabled_path.callees == ["a"]) == (polarity == Polarity.POSITIVE)


def test_ternary_and_logical_and_in_jsx(ci):
    r = ci.analyze("new_checkout")
    ternary = next(b for b in r.branches if b.kind == BranchKind.TERNARY)
    assert ternary.enabled_path.rendered_components == ["NewCheckout"]
    assert ternary.disabled_path.rendered_components == ["LegacyCheckout"]
    r2 = ci.analyze("dark_mode")
    [land] = r2.branches
    assert land.kind == BranchKind.LOGICAL_AND
    assert land.enabled_path.rendered_components == ["ThemeToggle"] and land.disabled_path is None


def test_guard_clause_negative(ci):
    [b] = ci.analyze("legacy_search").branches
    assert b.kind == BranchKind.GUARD_CLAUSE and b.polarity == Polarity.NEGATIVE
    assert "legacySearch" in b.enabled_path.callees
    assert "modernSearch" in b.disabled_path.callees


def test_alias_variable_is_followed(analyze):
    r = analyze({"a.ts": """
        import f from "flagsmith";
        function g() {
          const on = f.hasFeature("x");
          return on ? 1 : 2;
        }
    """}, "x")
    [b] = r.branches
    assert b.via_alias == "on" and b.kind == BranchKind.TERNARY and b.enabled_path.excerpt == "1"


def test_compound_condition_is_indeterminate(ci):
    r = ci.analyze("free_shipping")
    [b] = r.branches
    assert not b.determinable and not b.condition_is_exact
    assert any(f.kind == FindingKind.COMPOUND_CONDITION for f in r.findings)
    assert any(u.kind == UncertaintyKind.INDETERMINATE_BRANCH for u in r.uncertainties)


def test_related_symbols_and_full_feature_scope(ci):
    r = ci.analyze("new_checkout")
    gated = {(s.name, s.relation) for s in r.related_symbols if s.exclusively_flag_gated}
    assert ("LegacyCheckout", SymbolRelation.DISABLED_PATH) in gated
    assert ("legacyTotal", SymbolRelation.DISABLED_PATH) in gated
    assert r.feature_scope == FeatureScope.FULL_FEATURE_CANDIDATE
    legacy = next(s for s in r.related_symbols if s.name == "LegacyCheckout")
    assert legacy.defined_at.file_path == "src/checkout/LegacyCheckout.tsx"


def test_shared_symbol_is_not_exclusively_gated(analyze):
    r = analyze({"a.ts": """
        import f from "flagsmith";
        export function helper() {}
        export function g() { if (f.hasFeature("x")) { helper(); } }
        export function h() { helper(); }
    """}, "x")
    helper = next(s for s in r.related_symbols if s.name == "helper")
    assert helper.exclusively_flag_gated is False and helper.other_usages == 1
    assert r.feature_scope == FeatureScope.PARTIAL


def test_related_tests_found(ci):
    tests = {t.file_path: t for t in ci.analyze("new_checkout").related_tests}
    assert tests["src/lib/cart.test.ts"].strength == "strong"


def test_fallbacks(ci):
    [fb] = ci.analyze("banner_text").fallbacks
    assert fb.kind == FallbackKind.OPTION_FALLBACK and fb.default_expression == '"Welcome back!"'
    [fb2] = ci.analyze("shipping_rate").fallbacks
    assert fb2.kind == FallbackKind.TYPE_GUARD_DEFAULT and fb2.default_expression == "5.99"


def test_nullish_fallback(analyze):
    r = analyze({"a.ts": 'import f from "flagsmith";\nconst c = f.getValue("color") ?? "blue";\n'}, "color")
    assert [(x.kind, x.default_expression) for x in r.fallbacks] == [(FallbackKind.NULLISH_DEFAULT, '"blue"')]


def test_redundant_nested_check_and_identical_branches(analyze):
    r = analyze({"a.ts": """
        import f from "flagsmith";
        function g() {
          if (f.hasFeature("x")) {
            if (f.hasFeature("x")) { a(); }
          }
          const v = f.hasFeature("x") ? b() : b();
        }
    """}, "x")
    kinds = {f.kind for f in r.findings}
    assert FindingKind.REDUNDANT_NESTED_CHECK in kinds
    assert FindingKind.IDENTICAL_BRANCHES in kinds


def test_unreachable_branch_is_a_hypothesis_given_config(ci):
    cfg = FlagConfiguration(flag_name="new_checkout", project="acme", environments=[
        EnvironmentFlagState(environment="production", is_production=True, enabled=True)])
    r = ci.analyze("new_checkout", cfg)
    unreachable = [f for f in r.findings if f.kind == FindingKind.POTENTIALLY_UNREACHABLE_BRANCH]
    assert unreachable and all(f.is_hypothesis for f in unreachable)
    assert all("may still run" in f.message for f in unreachable)


def test_value_escaping_scope_reported(analyze):
    r = analyze({"a.ts": 'import f from "flagsmith";\nexport const on = () => f.hasFeature("x");\nsend(f.hasFeature("x"));\n'}, "x")
    assert sum(f.kind == FindingKind.FLAG_VALUE_ESCAPES_SCOPE for f in r.findings) == 2
    assert any(u.kind == UncertaintyKind.FLAG_VALUE_ESCAPES for u in r.uncertainties)


def test_unreferenced_flag_reports_uncertainty(ci):
    r = ci.analyze("old_promo_banner")
    assert r.feature_scope == FeatureScope.NOT_REFERENCED
    assert any(u.kind == UncertaintyKind.NO_REFERENCES_FOUND and "does not prove" in u.description for u in r.uncertainties)


def test_dynamic_reference_matching():
    from lifecycle_engine.schemas.discovery import FlagReference

    def ref(expr):
        return FlagReference.model_construct(is_dynamic=True, argument_expression=expr, flag_name=None)

    assert may_match(ref("name"), "anything")
    assert may_match(ref("`exp_${x}`"), "exp_checkout")
    assert not may_match(ref("`exp_${x}`"), "new_checkout")
    assert not may_match(ref('"beta_" + x'), "new_checkout")
    assert may_match(ref('"beta_" + x'), "beta_ui")
