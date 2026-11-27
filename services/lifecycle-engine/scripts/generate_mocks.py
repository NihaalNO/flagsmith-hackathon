"""Regenerate mocks/ by running the real engine on the sample repositories.

    python scripts/generate_mocks.py

Outputs are real engine results (not hand-written). Local filesystem paths are
replaced with placeholders so the files can be shared.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from lifecycle_engine.agents.llm import MockLLMProvider  # noqa: E402
from lifecycle_engine.api.errors import to_error  # noqa: E402
from lifecycle_engine.api.service import EngineService  # noqa: E402
from lifecycle_engine.schemas.api import (  # noqa: E402
    AnalyzeFlagRequest, CleanupPlanRequest, DetectStaleRequest, DiscoverRequest, EvaluateLifecycleRequest,
    GeneratePatchRequest, TransitionRequest, VerifyCleanupRequest,
)
from lifecycle_engine.schemas.discovery import RepositoryTarget  # noqa: E402
from lifecycle_engine.schemas.flags import ConsumerCoverage, DynamicReferenceReview, FlagConfiguration  # noqa: E402
from lifecycle_engine.schemas.lifecycle import FlagLifecycle, LifecycleState  # noqa: E402

AS_OF = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
OUT = ENGINE / "mocks"
REPOS = [
    RepositoryTarget(repository="acme/checkout-web", root_path=str(ENGINE / "sample_repos" / "checkout-web")),
    RepositoryTarget(repository="acme/pricing-service", root_path=str(ENGINE / "sample_repos" / "pricing-service")),
]
CONFIGS = {c["flag_name"]: FlagConfiguration.model_validate(c)
           for c in json.loads((ENGINE / "sample_data" / "flags.json").read_text(encoding="utf-8"))["flags"]}


def scrub(text: str) -> str:
    text = re.sub(r"(?:file:///?)?(?:[A-Za-z]:)?[\\/][^\s\"'`]*?flagpilot-verify-[^\\/\s\"'`]+", "<isolated-workspace>", text)
    for p in {str(ENGINE), str(ENGINE).replace("\\", "/"), str(ENGINE).replace("\\", "\\\\"), str(Path.home())}:
        text = text.replace(p, "<local>")
    return text


def write(name: str, payload) -> None:
    data = payload if isinstance(payload, (dict, list)) else payload.model_dump(mode="json")
    (OUT / name).write_text(scrub(json.dumps(data, indent=2, ensure_ascii=False)) + "\n", encoding="utf-8")
    print("wrote", name)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    svc = EngineService(llm=MockLLMProvider())

    scan = svc.discover(DiscoverRequest(repositories=REPOS))
    write("discover.json", scan)
    write("analysis_new_checkout.json", svc.analyze_flag(AnalyzeFlagRequest(
        repositories=REPOS, flag_name="new_checkout", configuration=CONFIGS["new_checkout"])))

    rolling = FlagLifecycle(flag_id="acme-web:new_checkout", flag_name="new_checkout", state=LifecycleState.ROLLING_OUT,
                            created_at=AS_OF, updated_at=AS_OF)
    write("lifecycle_evaluation_new_checkout.json", svc.evaluate_lifecycle(EvaluateLifecycleRequest(
        repositories=REPOS, flag_name="new_checkout", configuration=CONFIGS["new_checkout"], lifecycle=rolling, as_of=AS_OF)))
    try:
        svc.transition(TransitionRequest(lifecycle=rolling, to_state=LifecycleState.FULLY_RELEASED, actor="ui-user",
                                         reason="100% in production"))
    except Exception as exc:  # documented error example
        status, body = to_error(exc)
        write("error_transition_requirements.json", {"http_status": status, **body.model_dump(mode="json")})

    write("stale_findings.json", svc.detect_stale(DetectStaleRequest(
        repositories=REPOS, configurations=list(CONFIGS.values()), as_of=AS_OF)))

    # The one dynamic reference `readFlag(client, name)` reviewed by a human for new_checkout:
    reviews = [DynamicReferenceReview(reference_id=r.id, reviewed_by="checkout-lead@acme.example", reviewed_at=AS_OF,
                                      excluded_flags=["new_checkout"], notes="readFlag is only used for experiment flags")
               for r in scan.dynamic()]
    coverage = ConsumerCoverage(scanned_repositories=[r.repository for r in REPOS], all_consumers_scanned=True,
                                dynamic_reference_reviews=reviews)
    plan = svc.cleanup_plan(CleanupPlanRequest(repositories=REPOS, flag_name="new_checkout", configuration=CONFIGS["new_checkout"],
                                               coverage=coverage, as_of=AS_OF))
    write("cleanup_plan_new_checkout.json", plan)
    write("cleanup_plan_legacy_search.json", svc.cleanup_plan(CleanupPlanRequest(
        repositories=REPOS, flag_name="legacy_search", configuration=CONFIGS["legacy_search"], as_of=AS_OF)))
    write("patch_new_checkout.json", plan.patch)
    write("verification_ready_new_checkout.json", svc.verify_cleanup(VerifyCleanupRequest(
        repositories=REPOS, patch=plan.patch, dynamic_reference_reviews=reviews)))

    # A failing verification: legacy_search patch verified without the dynamic-reference review and with
    # an extra test that still expects the legacy behaviour is shown in tests; here we show 'incomplete'.
    legacy_patch = svc.generate_patch(GeneratePatchRequest(repositories=REPOS, flag_name="legacy_search",
                                                           configuration=CONFIGS["legacy_search"]))
    write("verification_incomplete_legacy_search.json", svc.verify_cleanup(VerifyCleanupRequest(
        repositories=REPOS, patch=legacy_patch)))


if __name__ == "__main__":
    main()
