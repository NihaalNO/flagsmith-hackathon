"""Can a dynamic reference evaluate a given flag name?

Conservative: returns False only when the static parts of the expression prove
it cannot produce the name (e.g. `exp_${x}` can never be "new_checkout").
"""

from __future__ import annotations

import re

from ..schemas.discovery import FlagReference

_STR = r"""(?:"([^"\\]*)"|'([^'\\]*)')"""


def _static_prefix_suffix(expr: str) -> tuple[str, str] | None:
    expr = expr.strip()
    if expr.startswith("`") and expr.endswith("`"):
        body = expr[1:-1]
        if "${" not in body:
            return body, body
        prefix = body.split("${", 1)[0]
        suffix = body.rsplit("}", 1)[-1]
        return prefix, suffix
    m = re.match(rf"^{_STR}\s*\+", expr)
    prefix = (m.group(1) or m.group(2) or "") if m else ""
    m2 = re.search(rf"\+\s*{_STR}$", expr)
    suffix = (m2.group(1) or m2.group(2) or "") if m2 else ""
    if prefix or suffix:
        return prefix, suffix
    return None


def may_match(ref: FlagReference, flag_name: str) -> bool:
    if not ref.is_dynamic:
        return ref.flag_name == flag_name
    parts = _static_prefix_suffix(ref.argument_expression or "")
    if parts is None:
        return True
    prefix, suffix = parts
    return flag_name.startswith(prefix) and flag_name.endswith(suffix) and len(flag_name) >= len(prefix) + len(suffix)


def matching_dynamic_references(refs: list[FlagReference], flag_name: str) -> list[FlagReference]:
    return [r for r in refs if r.is_dynamic and may_match(r, flag_name)]
