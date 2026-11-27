# FlagPilot Lifecycle Engine (Member 2)

Python intelligence engine that discovers Flagsmith feature-flag references, analyzes
how they are used, tracks lifecycle state, detects stale flags, plans cleanups, generates
suggested patches and verifies them in an isolated workspace. It works offline against the
bundled sample repositories and mock Flagsmith data; no credentials or LLM key are needed.

Every output is a **recommendation**. The engine never writes to a repository, opens or
merges a pull request, or archives a flag.

## Layout

```
lifecycle_engine/
  discovery/      Module 1 - Tree-sitter scanner (TS/TSX/JS), Flagsmith SDK catalog, binding resolution,
                  GitHub tarball source (sources.py)
  analyzers/      Module 2 - branches, fallbacks, related symbols/tests, findings, uncertainties
  state_machine/  Module 3 - lifecycle states, evidence-gated transitions, overrides, evaluation
  policies/       Module 4 - configurable, evidence-based stale detection
  agents/         Module 5 - evidence packet, injectable LLM provider (mock + Anthropic), planner, output validation
  cleanup/        Module 5/6 - deterministic patch generation, isolated verification
  api/            EngineService facade, FastAPI router (for apps/api), dev app
  schemas/        Pydantic contracts for everything above
sample_repos/     checkout-web (browser + React SDK), pricing-service (Node server SDK)
sample_data/      mock Flagsmith configuration (flags.json)
mocks/            real engine outputs for the frontend (regenerate: scripts/generate_mocks.py)
docs/             architecture.md, api-contract.md, integration.md, openapi.json
tests/            145 pytest tests (unit, safety, integration with real Node tests)
```

## Setup

```powershell
cd services/lifecycle-engine
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Python >= 3.11 (tested on 3.11 and 3.14). Node >= 22.6 on PATH is needed only for the two integration tests that run the
sample repositories' own test suites (they are skipped otherwise).

## Use

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m lifecycle_engine.cli scan sample_repos/checkout-web --repo acme/checkout-web
.venv\Scripts\python -m lifecycle_engine.cli stale --repo acme/checkout-web=sample_repos/checkout-web --repo acme/pricing-service=sample_repos/pricing-service --flags sample_data/flags.json
.venv\Scripts\python scripts/generate_mocks.py
.venv\Scripts\python -m uvicorn lifecycle_engine.api.app:app --reload   # dev server at /api/v1/engine
```

Python API:

```python
from lifecycle_engine.api.service import EngineService
from lifecycle_engine.schemas.api import DiscoverRequest
from lifecycle_engine.schemas.discovery import RepositoryTarget

scan = EngineService().discover(DiscoverRequest(repositories=[
    RepositoryTarget(repository="acme/checkout-web", root_path="sample_repos/checkout-web")]))
```

LLM narrative (optional): `FLAGPILOT_LLM_PROVIDER=mock` or `anthropic` (requires
`pip install -e ".[llm]"` and Anthropic credentials). Without it, the planner produces a
deterministic narrative; analysis is identical either way.

See [docs/architecture.md](docs/architecture.md) for design and limitations,
[docs/api-contract.md](docs/api-contract.md) for request/response fields, and
[docs/integration.md](docs/integration.md) for what needs agreement with Members 1 and 3.
