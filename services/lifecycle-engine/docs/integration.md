# Integration and coordination

All Member 2 code lives in `services/lifecycle-engine/`. Nothing outside that folder was
created or modified. `main` contains Member 1's backend in `apps/api/` (FastAPI, SQLAlchemy,
Alembic, Supabase auth, Flagsmith sync, Python 3.12); there is no `apps/api/app/intelligence/`,
`apps/web` or `packages/contracts` yet.

## Conflict to resolve with Member 1: lifecycle transitions

`apps/api/app/core/lifecycle.py` defines the same eight state values as the engine
(`created` … `retired`), so stored `lifecycle_status` strings are compatible. Its
`ALLOWED_TRANSITIONS` table differs in ways that break the project's safety requirements:

| Backend allows | Engine | Requirement affected |
|---|---|---|
| `created` / `in_development` / `testing` → `retired` | not a valid transition | a flag must not be retired before cleanup is merged, verified and approved |
| any transition with no evidence or approval | evidence + approvals per edge (`TRANSITION_RULES`) | recommendation vs. approved transition must be distinguishable |
| no transition history | `FlagLifecycle.history` with actor, reason, evidence, approvals | transitions must be recorded with timestamps and reasons |

Proposal: the backend's transition endpoint calls `EngineService.transition` (or
`LifecycleStateMachine.transition`) and persists the returned `FlagLifecycle`, keeping
`can_transition` only as a fast pre-check — or Member 1 adopts the engine's rule table. If the
backend needs to retire flags that never shipped (abandoned before release), that should be an
explicit, approved transition added to `TRANSITION_RULES`, not an unguarded edge.

## Overlap to resolve with Member 1: code scanning

`apps/api/app/services/code_scan.py` (used by `POST /api/repositories/{id}/scan`) records every
whole-identifier text match of each known flag name in the repository tarball. The engine's
discovery answers a different, stricter question:

| | backend `scan_tarball` | engine discovery |
|---|---|---|
| match | flag name anywhere (comments, strings, unrelated identifiers) | Flagsmith SDK calls traced to an import/instance/typed binding |
| unknown flags | not found (needs the name in the DB first) | every flag the code evaluates |
| dynamic names (`hasFeature(name)`) | invisible | reported as `dynamic` |
| constants (`FLAGS.NEW_CHECKOUT`) | matches the constant's line only if it contains the name | resolved to `new_checkout`, also across files |
| usage, branches, confidence | — | `usage_type`, `classification`, `confidence`, analysis reports |
| languages | any text file | TS/TSX/JS |

Both can coexist (the text scan is a useful cross-check for non-JS code). To use the engine on
the same tarball the backend already downloads:

```python
from lifecycle_engine.discovery.sources import scan_github_tarball, tarball_checkout

scan = scan_github_tarball(repo.full_name, tarball)          # discovery only
with tarball_checkout(repo.full_name, tarball) as target:   # any EngineService operation
    ...  # e.g. EngineService().cleanup_plan(CleanupPlanRequest(repositories=[target], ...))
```

`FlagReference` rows map directly: `path = ref.location.file_path`, `line = ref.location.line`,
`snippet = ref.context.snippet`, `commit_sha` from the tarball. Columns worth adding if the engine
output is stored: `classification`, `usage_type`, `confidence`, and the engine's stable `ref.id`.
Dynamic references have no `flag_name` and need a nullable `flag_id` or a separate table.

## Needs agreement with Member 1 (backend)

1. **Mounting** — done as backend endpoints rather than mounting the engine's own router:
   `POST/GET /api/flags/{id}/analysis` and `GET /api/flags/{id}/analyses` in
   `apps/api/app/api/routes/analysis.py`, behind the backend's auth and workspace isolation, with
   results stored in `flag_analyses` (migration `5c2e8d4a1f07`). `apps/api/app/services/engine.py`
   is the only backend module that imports the engine, lazily. Patch generation is reachable
   through the plan (only when a flag is ready for cleanup); verification is **not** exposed by the
   backend, because it runs repository test scripts on the host and needs a sandbox first.
   Still open: packaging the engine for the Vercel deploy (it installs `requirements.txt`, which does
   not include the engine; `requirements-engine.txt` does).
2. **Repository checkouts** — the engine takes `RepositoryTarget{repository, root_path}`. The
   GitHub App integration downloads tarballs; `tarball_checkout` turns one into a temporary
   target (safe extraction: regular files only, no links or `..` paths, size limits). When the
   router is used, put those temp directories under `EngineSettings.allowed_roots`.
   `repository` should be `Repository.full_name` (`owner/name`). The backend's `Flag` row holds a
   single `environment` and `enabled`; per-environment state for `FlagConfiguration.environments`
   has to come from the Flagsmith sync.
3. **Runtime** — the engine is tested on Python 3.11 and 3.14 (backend targets 3.12) and adds
   `tree-sitter`, `tree-sitter-typescript` and `tree-sitter-javascript` (binary wheels) to the
   backend's dependencies if it is imported there; check the Vercel bundle size.
4. **Flagsmith data** — mostly done: the backend sync stores feature metadata and every
   environment's state (`flag_environment_states`), and `apps/api/app/services/engine.py` maps them
   to `FlagConfiguration` (`created_at`, `owners` as `flagsmith-user:<id>`, `tags`, `is_archived`,
   `feature_type`, `environments[]` with overrides, split and `updated_at`). Production is
   `PRODUCTION_ENVIRONMENT_NAMES` (global, not yet per project). Identity overrides are unknown on
   Edge projects and treated as none (noted in `data_notes`). Still open: audit log →
   `recent_changes[]` and evaluation analytics → `activity`
   (`projects/{pk}/features/{id}/evaluation-data/`, one request per feature and environment).
5. **Persistence** — store `FlagLifecycle` (with `history`), `StaleFlagFinding`, `CleanupPlan`,
   `CleanupPatch`, `VerificationResult`, `DynamicReferenceReview` and owner confirmations as
   JSON or mapped tables. Ids are stable (`ref_`, `stale_`, `plan_`, `patch_`, `verify_`,
   `transition_`). Flag id convention: `"{project}:{flag_name}"`.
6. **Evidence from GitHub** — `cleanup_merged` evidence must be created by the backend from a
   merged PR (`source: "github"`, `reference: <PR URL>`); the engine never claims it.
7. **Verification commands** — configure `EngineSettings.server_commands` per repository
   (e.g. `npm ci && npm test`, `npx tsc --noEmit`). Request-supplied commands stay disabled.
   Run verification in a worker (it can take minutes).
8. **LLM provider** — configured in the backend's `.env` (`FLAGPILOT_LLM_PROVIDER`, `GROQ_API_KEY`, `FLAGPILOT_LLM_MODEL`,
   `FLAGPILOT_LLM_BASE_URL`, `FLAGPILOT_LLM_API_KEY`, `FLAGPILOT_LLM_JSON_MODE`); or pass
   `GroqProvider()`, `OpenAICompatibleProvider()`, `AnthropicProvider()`, `ClaudeCodeProvider()` or another `LLMProvider`
   to `EngineService`; `None` keeps planning deterministic. The Claude Code provider needs the
   `claude` CLI installed and signed in on the machine that runs planning (fine for local/dev; a
   serverless deploy such as Vercel should use the SDK provider instead).
9. **Shared contracts** — if `packages/contracts` becomes the source of truth, export
   `docs/openapi.json` (regenerate from the router) rather than duplicating models by hand.

## Provided to Member 3 (frontend)

* `mocks/*.json` — real engine outputs for every operation, plus an error example.
* `docs/api-contract.md` — field-by-field guide.
* The frontend can run entirely on mocks; no live Flagsmith connection is required.

## Safety boundaries (for everyone)

The engine recommends; humans approve. It does not write to source repositories, push, open or
merge PRs, or modify/archive Flagsmith flags. The `retired` transition requires verified + merged
cleanup and two approvals, and overrides cannot bypass it.
