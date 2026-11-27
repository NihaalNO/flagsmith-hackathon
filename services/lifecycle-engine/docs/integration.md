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

## Needs agreement with Member 1 (backend)

1. **Mounting** — proposed: `app.include_router(create_router(EngineService(settings, llm)), prefix="/api/v1/engine")`.
   Alternatively call `EngineService` methods directly from Member 1's own routes; either way,
   avoid two sets of routes for the same operation.
2. **Repository checkouts** — the engine takes `RepositoryTarget{repository, root_path}`. The
   GitHub integration clones/updates checkouts under a directory that is passed as
   `EngineSettings.allowed_roots`. `repository` should be the stable id (`owner/name`).
3. **Flagsmith data** — map Flagsmith API data to `FlagConfiguration` (`schemas/flags.py`):
   features → `flag_name`, `flagsmith_feature_id`, `created_at`, `owners`, `tags`, `is_archived`,
   `feature_type`; feature states per environment → `environments[]` (`enabled`, `value`,
   segment/identity override counts, multivariate allocation, `updated_at`, `is_production`);
   audit log → `recent_changes[]`; flag analytics → `activity`. Which environments count as
   production must be configurable per project.
4. **Persistence** — store `FlagLifecycle` (with `history`), `StaleFlagFinding`, `CleanupPlan`,
   `CleanupPatch`, `VerificationResult`, `DynamicReferenceReview` and owner confirmations as
   JSON or mapped tables. Ids are stable (`ref_`, `stale_`, `plan_`, `patch_`, `verify_`,
   `transition_`). Flag id convention: `"{project}:{flag_name}"`.
5. **Evidence from GitHub** — `cleanup_merged` evidence must be created by the backend from a
   merged PR (`source: "github"`, `reference: <PR URL>`); the engine never claims it.
6. **Verification commands** — configure `EngineSettings.server_commands` per repository
   (e.g. `npm ci && npm test`, `npx tsc --noEmit`). Request-supplied commands stay disabled.
   Run verification in a worker (it can take minutes).
7. **LLM provider** — pass `AnthropicProvider()` (or another `LLMProvider`) to `EngineService`;
   `None` keeps planning deterministic. Credentials are read by the SDK from the environment.
8. **Shared contracts** — if `packages/contracts` becomes the source of truth, export
   `docs/openapi.json` (regenerate from the router) rather than duplicating models by hand.

## Provided to Member 3 (frontend)

* `mocks/*.json` — real engine outputs for every operation, plus an error example.
* `docs/api-contract.md` — field-by-field guide.
* The frontend can run entirely on mocks; no live Flagsmith connection is required.

## Safety boundaries (for everyone)

The engine recommends; humans approve. It does not write to source repositories, push, open or
merge PRs, or modify/archive Flagsmith flags. The `retired` transition requires verified + merged
cleanup and two approvals, and overrides cannot bypass it.
