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
  agents/         Module 5 - evidence packet, injectable LLM provider (mock, Claude Code CLI, Anthropic SDK), planner, output validation
  cleanup/        Module 5/6 - deterministic patch generation, isolated verification
  api/            EngineService facade, FastAPI router (for apps/api), dev app
  schemas/        Pydantic contracts for everything above
sample_repos/     checkout-web (browser + React SDK), pricing-service (Node server SDK)
sample_data/      mock Flagsmith configuration (flags.json)
mocks/            real engine outputs for the frontend (regenerate: scripts/generate_mocks.py)
docs/             architecture.md, api-contract.md, integration.md, openapi.json
tests/            162 pytest tests (unit, safety, integration with real Node tests)
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

LLM narrative (optional), via `FLAGPILOT_LLM_PROVIDER`:

* `claude-code` runs the Claude Code CLI as a sub-process (`claude -p`, no tools, no MCP, empty
  working directory, evidence on stdin, `--json-schema` output). It uses your signed-in Claude Code
  account, so no API key is needed. `FLAGPILOT_LLM_MAX_BUDGET_USD` caps spend per call (default 1.0);
  a plan for the sample `new_checkout` flag cost about $0.07–0.12 and took about 30 s.
* `groq` calls Groq (`https://api.groq.com/openai/v1`) with `GROQ_API_KEY` (or
  `FLAGPILOT_LLM_API_KEY`). Model defaults to `openai/gpt-oss-120b`; `FLAGPILOT_LLM_JSON_MODE=auto`
  uses structured outputs on models that support them and JSON mode on the rest. Live test:
  `$env:FLAGPILOT_LIVE_GROQ = "1"; $env:GROQ_API_KEY = "..."; .venv\Scripts\python -m pytest -s -m groq_live`.
* `openai-compatible` calls any OpenAI Chat Completions server (OpenAI, OpenRouter, Ollama, vLLM,
  LM Studio, LiteLLM) with `FLAGPILOT_LLM_MODEL`, `FLAGPILOT_LLM_BASE_URL` (e.g.
  `http://localhost:11434/v1`), optional `FLAGPILOT_LLM_API_KEY`, and `FLAGPILOT_LLM_JSON_MODE`
  (`auto` = `json_schema` by default; `json_object` or `none` for servers without structured outputs).
  Standard library only, no extra dependency.
* `anthropic` uses the official SDK (`pip install -e ".[llm]"` and Anthropic credentials).
* `mock` is deterministic, for development and tests.

Without a provider the planner writes a deterministic narrative; analysis is identical either way.
The live CLI test is opt-in because it costs money:

```powershell
$env:FLAGPILOT_LIVE_CLAUDE = "1"; .venv\Scripts\python -m pytest -s -m claude_live
```

See [docs/architecture.md](docs/architecture.md) for design and limitations,
[docs/api-contract.md](docs/api-contract.md) for request/response fields, and
[docs/integration.md](docs/integration.md) for what needs agreement with Members 1 and 3.
