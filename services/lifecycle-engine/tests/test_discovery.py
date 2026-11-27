from __future__ import annotations

import json

from lifecycle_engine.schemas.discovery import (
    FlagApi,
    NameResolution,
    ReferenceClassification as C,
    ScanResult,
    UsageType,
)


def _by_flag(result, name):
    return [r for r in result.references if r.flag_name == name]


# --- static references -------------------------------------------------------

def test_default_import_literal_has_feature(scan_files):
    res = scan_files({"a.ts": """
        import flagsmith from "flagsmith";
        if (flagsmith.hasFeature("new_ui")) { render(); }
    """})
    [ref] = res.references
    assert ref.flag_name == "new_ui"
    assert ref.classification == C.CONFIRMED
    assert ref.api == FlagApi.HAS_FEATURE
    assert ref.usage_type == UsageType.BOOLEAN_EVALUATION
    assert (ref.location.file_path, ref.location.line, ref.location.column) == ("a.ts", 2, 5)
    assert ref.context.in_conditional is True
    assert any("default import from 'flagsmith'" in e for e in ref.evidence)


def test_get_value_and_require(scan_files):
    res = scan_files({"a.js": """
        const flagsmith = require("flagsmith");
        const color = flagsmith.getValue('button_color');
    """})
    [ref] = res.references
    assert ref.flag_name == "button_color"
    assert ref.usage_type == UsageType.VALUE_RETRIEVAL
    assert ref.classification == C.CONFIRMED


def test_template_literal_without_substitution_is_static(scan_files):
    res = scan_files({"a.ts": 'import f from "flagsmith";\nf.hasFeature(`plain_name`);\n'})
    assert res.references[0].flag_name == "plain_name"
    assert res.references[0].is_dynamic is False


def test_local_and_object_constants(scan_files):
    res = scan_files({"a.ts": """
        import flagsmith from "flagsmith";
        const NAME = "const_flag";
        const FLAGS = { OTHER: "obj_flag" } as const;
        enum Keys { E = "enum_flag" }
        flagsmith.hasFeature(NAME);
        flagsmith.hasFeature(FLAGS.OTHER);
        flagsmith.hasFeature(Keys.E);
    """})
    names = [(r.flag_name, r.name_resolution) for r in res.references]
    assert names == [
        ("const_flag", NameResolution.LOCAL_CONSTANT),
        ("obj_flag", NameResolution.LOCAL_CONSTANT),
        ("enum_flag", NameResolution.LOCAL_CONSTANT),
    ]


def test_server_sdk_chain_across_files(scan_files):
    res = scan_files({
        "client.ts": 'import Flagsmith from "flagsmith-nodejs";\nexport const fs = new Flagsmith({ environmentKey: "k" });\n',
        "h.ts": """
            import { fs } from "./client";
            export async function h() {
              const flags = await fs.getEnvironmentFlags();
              return flags.getFeatureValue("rate") ?? flags.getFlag("other").enabled;
            }
        """,
    })
    got = {(r.flag_name, r.api) for r in res.references}
    assert got == {("rate", FlagApi.GET_FEATURE_VALUE), ("other", FlagApi.GET_FLAG)}
    assert all(r.classification == C.CONFIRMED for r in res.references)


def test_react_hook_patterns(scan_files):
    res = scan_files({"c.tsx": """
        import { useFlags } from "flagsmith/react";
        export function C() {
          const flags = useFlags(["a", "b"]);
          const { c: renamed } = useFlags(["c"]);
          return <div>{flags.a.enabled && flags.b.value}{renamed.enabled ? 1 : 2}</div>;
        }
    """})
    kinds = sorted((r.flag_name, r.usage_type.value) for r in res.references)
    assert kinds == [
        ("a", "boolean_evaluation"), ("a", "hook_subscription"),
        ("b", "hook_subscription"), ("b", "value_retrieval"),
        ("c", "boolean_evaluation"), ("c", "hook_subscription"),
    ]


def test_typed_parameter_and_class_field(scan_files):
    res = scan_files({"s.ts": """
        import type { IFlagsmith } from "flagsmith";
        export function f(client: IFlagsmith) { return client.hasFeature("p"); }
        class S { constructor(private flagsmith: IFlagsmith) {} m() { return this.flagsmith.getValue("q"); } }
    """})
    assert {r.flag_name for r in res.references} == {"p", "q"}
    assert all(r.classification == C.CONFIRMED for r in res.references)


def test_multiple_references_across_files_and_repositories(sample_scan):
    refs = _by_flag(sample_scan, "new_checkout")
    files = {(r.repository, r.location.file_path) for r in refs}
    assert files == {
        ("acme/checkout-web", "src/checkout/CheckoutPage.tsx"),
        ("acme/checkout-web", "src/lib/cart.ts"),
        ("acme/pricing-service", "src/handlers.ts"),
    }
    summary = next(s for s in sample_scan.summary.flags if s.flag_name == "new_checkout")
    assert summary.repositories == ["acme/checkout-web", "acme/pricing-service"]


def test_imported_constant_resolution(sample_scan):
    [ref] = [r for r in _by_flag(sample_scan, "new_checkout") if r.location.file_path == "src/lib/cart.ts"]
    assert ref.name_resolution == NameResolution.IMPORTED_CONSTANT
    assert ref.argument_expression == "FLAGS.NEW_CHECKOUT"


# --- dynamic and uncertain references -------------------------------------------

def test_dynamic_references_are_reported_separately(scan_files):
    res = scan_files({"a.ts": """
        import flagsmith from "flagsmith";
        export const on = (n: string) => flagsmith.hasFeature(n);
        flagsmith.getValue(`exp_${on}`);
        flagsmith.hasFeature(getName());
    """})
    assert len(res.references) == 3
    for r in res.references:
        assert r.classification == C.DYNAMIC
        assert r.is_dynamic and r.flag_name is None
        assert r.name_resolution == NameResolution.UNRESOLVED
    assert res.summary.dynamic == 3
    assert [r.argument_expression for r in res.dynamic()] == ["n", "`exp_${on}`", "getName()"]


def test_untraceable_receiver_is_potential(sample_scan):
    [ref] = _by_flag(sample_scan, "analytics_v2")
    assert ref.classification == C.POTENTIAL
    assert ref.confidence < 0.7
    assert ref.location.file_path == "src/legacy/analytics.js"


def test_matching_method_without_flagsmith_context_is_ignored(scan_files):
    res = scan_files({"form.ts": """
        export function email(form: { getValue(f: string): string }) {
          return form.getValue("email") + map.hasFeature("x");
        }
    """})
    assert res.references == []


def test_unrelated_local_function_named_like_api_is_ignored(scan_files):
    res = scan_files({"a.ts": """
        import flagsmith from "flagsmith";
        function hasFeature(n: string) { return n.length > 0; }
        hasFeature("not_a_flag");
        flagsmith.identify("user");
    """})
    assert res.references == []


def test_trait_reads_are_separate_from_flags(sample_scan):
    assert {t.trait_key for t in sample_scan.trait_reads} == {"plan"}
    assert all(t.flag_name is None for t in sample_scan.trait_reads)
    assert all(r.usage_type != UsageType.TRAIT_READ for r in sample_scan.references)


def test_test_file_detection(scan_files):
    res = scan_files({"src/__tests__/a.test.ts": 'import f from "flagsmith";\nf.hasFeature("t");\n'})
    assert res.references[0].context.is_test_file


def test_syntax_errors_are_reported_not_fatal(scan_files):
    res = scan_files({
        "ok.ts": 'import f from "flagsmith";\nf.hasFeature("ok");\n',
        "bad.ts": 'import f from "flagsmith";\nf.hasFeature("bad" {{{ \n',
    })
    assert any(e.file_path == "bad.ts" for e in res.errors)
    assert "ok" in {r.flag_name for r in res.references}


# --- structured output -----------------------------------------------------------

def test_scan_result_is_validated_json_and_ids_are_stable(sample_targets, sample_scan):
    from lifecycle_engine.discovery.scanner import scan_repositories

    payload = json.loads(sample_scan.model_dump_json())
    again = ScanResult.model_validate(payload)
    assert again.references == sample_scan.references
    rescanned = scan_repositories(sample_targets)
    assert [r.id for r in rescanned.references] == [r.id for r in sample_scan.references]
    assert rescanned.scan_id == sample_scan.scan_id
    assert len({r.id for r in sample_scan.references}) == len(sample_scan.references)
