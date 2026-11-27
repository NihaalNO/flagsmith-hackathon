from __future__ import annotations

import pytest

from lifecycle_engine.cleanup.patcher import PatchGenerator, sha256
from lifecycle_engine.discovery.languages import language_for_path, parse
from lifecycle_engine.schemas.discovery import RepositoryTarget
from lifecycle_engine.schemas.patch import PatchStatus
from lifecycle_engine.schemas.stale import CleanupDirection as D


@pytest.fixture
def patch_of(make_repo):
    def _p(files: dict[str, str], flag="x", direction=D.KEEP_ENABLED_PATH, **kw):
        root = make_repo(files)
        before = {p: (root / p).read_bytes() for p in files}
        patch = PatchGenerator([RepositoryTarget(repository="t/r", root_path=str(root))]).generate(flag, direction, **kw)
        for p, content in before.items():  # never writes to the repository
            assert (root / p).read_bytes() == content
        return patch

    return _p


def _content(patch, path):
    raw = next(f.patched_content for f in patch.files if f.file_path == path)
    if "\r\n" in raw:  # repo written with CRLF (Windows): endings must be consistent, then normalise
        assert "\n" not in raw.replace("\r\n", "")
    return raw.replace("\r\n", "\n")


def _parses(src: str, path: str) -> bool:
    return not parse(src.encode(), language_for_path(path)).root_node.has_error


IF_ELSE = {"a.ts": """
    import flagsmith from "flagsmith";
    export function f() {
      if (flagsmith.hasFeature("x")) {
        newPath();
      } else {
        oldPath();
      }
    }
"""}


def test_keep_enabled_unwraps_block_and_removes_import(patch_of):
    p = patch_of(IF_ELSE)
    out = _content(p, "a.ts")
    assert out == 'export function f() {\n  newPath();\n}\n'
    assert p.status == PatchStatus.COMPLETE and not p.manual_steps
    assert p.files[0].diff.startswith("--- a/a.ts")


def test_keep_disabled(patch_of):
    assert "oldPath();" in _content(patch_of(IF_ELSE, direction=D.KEEP_DISABLED_PATH), "a.ts")
    assert "newPath" not in _content(patch_of(IF_ELSE, direction=D.KEEP_DISABLED_PATH), "a.ts")


@pytest.mark.parametrize("expr,direction,expected", [
    ('f.hasFeature("x") ? a : b', D.KEEP_ENABLED_PATH, "const v = a;"),
    ('f.hasFeature("x") ? a : b', D.KEEP_DISABLED_PATH, "const v = b;"),
    ('!f.hasFeature("x") ? a : b', D.KEEP_ENABLED_PATH, "const v = b;"),
    ('f.hasFeature("x") && y > 1', D.KEEP_ENABLED_PATH, "const v = y > 1;"),
    ('f.hasFeature("x") && y > 1', D.KEEP_DISABLED_PATH, "const v = false;"),
    ('f.hasFeature("x") || y', D.KEEP_DISABLED_PATH, "const v = y;"),
    ('f.hasFeature("x") === false ? a : b', D.KEEP_ENABLED_PATH, "const v = b;"),
    ('(f.hasFeature("x") && a) ? 1 : 2', D.KEEP_ENABLED_PATH, "const v = (a) ? 1 : 2;"),
])
def test_boolean_simplification(patch_of, expr, direction, expected):
    out = _content(patch_of({"a.ts": f'import f from "flagsmith";\nconst v = {expr};\n'}, direction=direction), "a.ts")
    assert out.strip() == expected


def test_nested_checks_resolve_to_fixed_point(patch_of):
    out = _content(patch_of({"a.ts": """
        import f from "flagsmith";
        function g() {
          if (f.hasFeature("x")) {
            if (!f.hasFeature("x")) { never(); }
            yes();
          }
        }
    """}), "a.ts")
    assert "hasFeature" not in out and "never" not in out and "yes();" in out
    assert _parses(out, "a.ts")


def test_jsx_patterns(patch_of):
    files = {"c.tsx": """
        import { useFlags } from "flagsmith/react";
        import { A } from "./A";
        import { B } from "./B";
        export function C() {
          const flags = useFlags(["x", "other"]);
          return <div>{flags.x.enabled ? <A /> : <B />}{flags.x.enabled && <span>hi</span>}{flags.other.enabled && <i />}</div>;
        }
    """}
    p = patch_of(files)
    out = _content(p, "c.tsx")
    assert '<div><A /><span>hi</span>{flags.other.enabled && <i />}</div>' in out
    assert 'useFlags(["other"])' in out
    assert 'import { B }' not in out and 'import { A }' in out and 'import { useFlags }' in out
    assert _parses(out, "c.tsx")
    off = _content(patch_of(files, direction=D.KEEP_DISABLED_PATH), "c.tsx")
    assert '<div><B />{flags.other.enabled && <i />}</div>' in off


def test_guard_clause_and_unreachable_tail(patch_of):
    src = {"a.ts": """
        import f from "flagsmith";
        export function g() {
          if (!f.hasFeature("x")) {
            return 1;
          }
          return 2;
        }
    """}
    assert "return 2;" in _content(patch_of(src), "a.ts") and "return 1" not in _content(patch_of(src), "a.ts")
    off = _content(patch_of(src, direction=D.KEEP_DISABLED_PATH), "a.ts")
    assert "return 1;" in off and "return 2" not in off


def test_inline_value(patch_of):
    p = patch_of({"a.ts": 'import f from "flagsmith";\nconst c = f.getValue("x", { fallback: "red" });\n'},
                 direction=D.INLINE_VALUE, inline_value="blue")
    assert _content(p, "a.ts").strip() == 'const c = "blue";'


def test_unsupported_and_potential_refs_become_manual_steps(patch_of):
    p = patch_of({
        "a.ts": 'import f from "flagsmith";\nexport function g() { if (f.hasFeature("x")) { a(); } }\nconst c = f.getValue("x");\n',
        "b.js": 'export function h() { if (window.flagsmith.hasFeature("x")) { b(); } }\n',
    })
    assert p.status == PatchStatus.PARTIAL
    reasons = " | ".join(m.reason for m in p.manual_steps)
    assert "value is read" in reasons and "potential reference" in reasons
    assert "a();" in _content(p, "a.ts")


def test_undetermined_direction_generates_nothing(patch_of):
    p = patch_of(IF_ELSE, direction=D.UNDETERMINED)
    assert p.status == PatchStatus.NOT_GENERATED and p.files == []


def test_sample_new_checkout_patch(sample_targets):
    p = PatchGenerator(sample_targets).generate("new_checkout", D.KEEP_ENABLED_PATH)
    assert p.status == PatchStatus.COMPLETE
    assert sorted(p.files_changed) == ["acme/checkout-web:src/checkout/CheckoutPage.tsx", "acme/checkout-web:src/lib/cart.ts",
                                       "acme/pricing-service:src/handlers.ts"]
    for f in p.files:
        assert _parses(f.patched_content, f.file_path)
        assert f.patched_sha256 == sha256(f.patched_content.encode())
    assert any("LegacyCheckout.tsx" in x and "whole file" in x for x in p.follow_ups)
    assert p.id == PatchGenerator(sample_targets).generate("new_checkout", D.KEEP_ENABLED_PATH).id
