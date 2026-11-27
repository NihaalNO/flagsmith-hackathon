"""Command line entry point.

    python -m lifecycle_engine.cli scan sample_repos/checkout-web --repo acme/checkout-web
    python -m lifecycle_engine.cli analyze new_checkout --repo acme/checkout-web=sample_repos/checkout-web --flags sample_data/flags.json
    python -m lifecycle_engine.cli stale --repo ... --flags sample_data/flags.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .schemas.discovery import RepositoryTarget
from .schemas.flags import FlagConfiguration


def _targets(values: list[str]) -> list[RepositoryTarget]:
    out = []
    for v in values:
        repo, _, path = v.partition("=")
        out.append(RepositoryTarget(repository=repo if path else Path(v).name, root_path=path or v))
    return out


def _configs(path: str | None) -> dict[str, FlagConfiguration]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {c["flag_name"]: FlagConfiguration.model_validate(c) for c in data.get("flags", data)}


def main(argv: list[str] | None = None) -> int:
    from .api.service import EngineService
    from .schemas.api import AnalyzeFlagRequest, CleanupPlanRequest, DetectStaleRequest, DiscoverRequest

    p = argparse.ArgumentParser(prog="flagpilot-engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="discover flag references")
    s.add_argument("path", nargs="?")
    s.add_argument("--repo", action="append", default=[], help="id=path (repeatable) or id when path is given")
    for name in ("analyze", "plan"):
        a = sub.add_parser(name)
        a.add_argument("flag")
        a.add_argument("--repo", action="append", required=True, help="id=path (repeatable)")
        a.add_argument("--flags", help="JSON file with flag configurations")
    st = sub.add_parser("stale")
    st.add_argument("--repo", action="append", required=True)
    st.add_argument("--flags", required=True)
    args = p.parse_args(argv)

    svc = EngineService()
    if args.cmd == "scan":
        repos = args.repo if not args.path else [f"{args.repo[0] if args.repo else Path(args.path).name}={args.path}"]
        result = svc.discover(DiscoverRequest(repositories=_targets(repos)))
    elif args.cmd == "analyze":
        result = svc.analyze_flag(AnalyzeFlagRequest(repositories=_targets(args.repo), flag_name=args.flag,
                                                     configuration=_configs(args.flags).get(args.flag)))
    elif args.cmd == "plan":
        result = svc.cleanup_plan(CleanupPlanRequest(repositories=_targets(args.repo), flag_name=args.flag,
                                                     configuration=_configs(args.flags).get(args.flag)))
    else:
        result = svc.detect_stale(DetectStaleRequest(repositories=_targets(args.repo), configurations=list(_configs(args.flags).values())))
    sys.stdout.write(result.model_dump_json(indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
