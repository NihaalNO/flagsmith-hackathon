# FlagPilot API

Feature flag lifecycle management backend. FastAPI + SQLAlchemy 2 + Alembic on Supabase Postgres.

FlagPilot stores lifecycle metadata and orchestration history; **Flagsmith remains the source of
truth for actual flag configuration.**

## Deployed

- API: **https://flagpilot-api.vercel.app** (docs: `/docs`, sign-in test page: `/dev/login`)
- Hosted on Vercel (project `flagpilot-api`, region `sin1`, next to the Supabase DB).
- Deploy: `cd apps/api && vercel deploy --prod`. Env vars (`DATABASE_URL`, `ENCRYPTION_KEY`,
  `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `CORS_ORIGINS`) are set in the Vercel project.
- Run migrations from your machine (`alembic upgrade head`) **before** deploying code that needs them.
- `GET /api/health` reports `"database": "ok"` when the deployment can reach Supabase.

## Run locally

```bash
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-engine.txt   # or requirements.txt to run without the lifecycle engine
cp .env.example .env          # then fill in DATABASE_URL (ask the backend owner)
alembic upgrade head          # apply migrations
uvicorn app.main:app --reload # http://localhost:8000/docs
pytest                        # tests use in-memory SQLite, never Supabase
```

## Authentication (GitHub login via Supabase Auth)

Users sign in **only with GitHub** (email sign-up is disabled). The frontend uses supabase-js:

```js
import { createClient } from "@supabase/supabase-js";
const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY); // both are public values

await supabase.auth.signInWithOAuth({ provider: "github", options: { redirectTo: window.location.origin } });

const { data: { session } } = await supabase.auth.getSession();
await fetch("https://flagpilot-api.vercel.app/api/flags", {
  headers: { Authorization: `Bearer ${session.access_token}` },
});
```

- Every `/api/*` route except `/api/health` returns **401** without a valid token.
- `GET /api/me` returns the signed-in user. Audit log entries record the user as `actor`.
- Tokens expire after ~1 hour; supabase-js refreshes them automatically.
- Allowed redirect URLs: `http://localhost:5173`, `http://localhost:3000`, `http://localhost:8000/dev/login`,
  `https://flagpilot-api.vercel.app/dev/login`. A deployed frontend URL must be added in Supabase (Auth → URL Configuration) before deploying.
- **Testing without a frontend:** open `http://localhost:8000/dev/login`, sign in with GitHub, copy the
  token, then in `/docs` click **Authorize** and paste it.

## Workspaces (multi-tenant)

A workspace = one GitHub account or organization that has the **seshat.buzz** GitHub App installed.
Everything (flags, repos, integrations, audit log) belongs to exactly one workspace and is
invisible to other workspaces (enforced centrally in `app/core/tenancy.py`).

- **Personal account install** → only that GitHub user can access it.
- **Organization install** → every member of that GitHub org (verified with GitHub on each
  `GET /api/workspaces`; people who leave the org lose access on their next check).
- Each workspace connects **its own Flagsmith** API key; repo projects are created in that org.

**Frontend onboarding flow**
1. Sign in with GitHub (Supabase).
2. `GET /api/workspaces` → `{workspaces: [...], install_url}`.
   - Empty → show "Install the GitHub App" button linking to `install_url`; after installing, call again.
   - One → use it (no header needed).
   - Several → let the user pick; send `X-Workspace-Id: <id>` on every request.
3. If the workspace has no Flagsmith integration yet → ask for a Flagsmith **admin** Organisation API key,
   `POST /api/integrations` `{provider: "flagsmith", name, api_token}`.
4. `POST /api/github/sync` → imports the workspace's repos and creates a Flagsmith project per repo.

Data routes without an accessible workspace return **409** (none chosen / none installed) or **403**
(no access to the requested `X-Workspace-Id`).

## API contract (v0.1)

Interactive docs: `/docs` (Swagger) and `/redoc`. Machine-readable schema: `/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check (public) |
| GET | `/api/workspaces` | Your workspaces (verified with GitHub) + GitHub App install link |
| GET | `/api/me` | Signed-in user incl. GitHub login |
| GET | `/api/flags?status=` | List flags (newest first), optional lifecycle filter |
| POST | `/api/flags` | Create flag → 201, 409 if name exists. With `integration_id`, creates it in Flagsmith first (502 if Flagsmith fails; nothing stored) |
| GET | `/api/flags/{id}` | Get flag → 404 if missing |
| PATCH | `/api/flags/{id}` | Update description/owner/project_name/environment/enabled |
| DELETE | `/api/flags/{id}` | Delete flag → 204 |
| POST | `/api/flags/{id}/lifecycle` | `{"status": "...", "reason": "..."}` → 422 if transition not allowed |
| GET | `/api/flags/lifecycle/transitions` | Map of status → allowed next statuses |
| GET/POST | `/api/repositories` | List / register a repo (`{"full_name": "org/repo"}`) |
| GET/DELETE | `/api/repositories/{id}` | Get / remove repo |
| PUT | `/api/repositories/{id}/flagsmith-project` | `{"flagsmith_project_id": 5}` → link the repo to an existing Flagsmith project (404 if the key can't see it) and sync its flags → `{repository, flagsmith_project_name, sync, sync_error}` |
| GET/POST | `/api/integrations` | List / add integration (`provider`: `flagsmith` \| `github`, optional write-only `api_token`) |
| GET/PATCH/DELETE | `/api/integrations/{id}` | Get / update (name, config, rotate `api_token`) / remove |
| POST | `/api/integrations/{id}/test` | Check the API key → `{ok, detail, organisations}`; sets `status` |
| GET | `/api/integrations/{id}/flagsmith/projects` | Projects visible to the key |
| GET | `/api/integrations/{id}/flagsmith/projects/{pid}/environments` | Environments (`id`, `name`, `api_key`) |
| POST | `/api/integrations/{id}/sync` | Import/update flags from `config.project_id` → `{created, linked, updated, unchanged, conflicts, missing_in_flagsmith}` |
| GET | `/api/audit-logs?resource_type=&resource_id=&limit=` | Audit trail, newest first |
| POST | `/api/flags/{id}/analysis` | Run the lifecycle engine on the flag's repos → 201 with stored result (see below); 400 no connected repo, 502 GitHub, 503 engine not installed |
| GET | `/api/flags/{id}/analysis` | Latest stored analysis → 404 if never analyzed |
| GET | `/api/flags/{id}/analyses` | Analysis history (summaries, newest first) |
| GET | `/api/flags/{id}/environments` | The flag's state in every Flagsmith environment, as of the last sync |

### Flag lifecycle

```
created → in_development → testing → rolling_out → fully_released
        → ready_for_cleanup → cleanup_in_progress → retired
```

Some backward moves are allowed (e.g. `testing → in_development`, `rolling_out → testing`).
Use `GET /api/flags/lifecycle/transitions` instead of hard-coding the rules in the frontend.

### Example

```bash
curl -X POST http://localhost:8000/api/flags -H "Content-Type: application/json" \
  -d '{"name":"new_checkout","description":"New checkout","owner":"payments-team","enabled":false}'
```

## Flagsmith integration

1. In Flagsmith: Organisation Settings → API Keys → create an **Organisation API key**.
2. `POST /api/integrations` with `{"provider":"flagsmith","name":"Flagsmith","api_token":"<key>"}`
   (self-hosted: add `"config":{"base_url":"https://your-host/api/v1"}`).
3. `POST /api/integrations/{id}/test` → should return `ok: true`.
4. Pick a project and environment via the two `flagsmith/...` endpoints, then
   `PATCH /api/integrations/{id}` with `{"config":{"project_id":5,"environment_key":"<env api_key>"}}`.
5. `POST /api/integrations/{id}/sync`.

Sync rules: Flagsmith owns name/description/enabled; FlagPilot owns lifecycle (sync never changes it).
`enabled` mirrors the configured environment (identity/segment overrides ignored). A FlagPilot-only flag
with the same name is linked, not duplicated. Features deleted in Flagsmith are reported in
`missing_in_flagsmith`, never auto-deleted. Sync runs inline for now (background jobs later).

Sync also records, for the lifecycle engine:
- **Feature metadata** on each flag: Flagsmith creation date, tag labels, owner user ids, type
  (STANDARD / MULTIVARIATE) and archived status.
- **Every environment's state** (`GET /api/flags/{id}/environments`): enabled, value, segment-override
  count, identity-override count (`null` on Flagsmith Edge projects, which don't report it), the
  multivariate split, and when it last changed. Read from `GET projects/{id}/features/?environment=<id>`,
  one request per environment. Environments removed in Flagsmith are dropped.
- Which environments count as production comes from `PRODUCTION_ENVIRONMENT_NAMES` (Flagsmith has
  no production flag).

Missing permissions (e.g. tags, or one environment) don't fail the sync; they're listed in the
result's `warnings`. `environment_states` counts the states recorded.

API tokens are encrypted with `ENCRYPTION_KEY` (Fernet) and never returned by the API.
**Back up `ENCRYPTION_KEY`**: if it's lost or changed, stored tokens can't be decrypted and must be re-entered.

## Database notes (Supabase)

- Connect through the **Session pooler** (port 5432) with `sslmode=require`.
- Every table has Row Level Security enabled with no policies, so Supabase's public REST API
  (anon key) cannot read it. **New tables must enable RLS in their migration too.**
- Schema changes: edit models → `alembic revision --autogenerate -m "..."` → review → `alembic upgrade head`.

## Lifecycle engine analysis

`POST /api/flags/{id}/analysis` (body optional: `{"use_llm": false}`) downloads the default branch of
the repositories linked to the flag's Flagsmith project (all GitHub-connected repos if the flag has no
project), runs the lifecycle engine in `services/lifecycle-engine`, and stores the result in
`flag_analyses`. The response has summary fields (`stale_status`, `stale_confidence`,
`cleanup_direction`, `suggested_state`, `recommended_action`, reference counts, `llm_status`) and
`result` with the full engine output: `analysis` (SDK-aware references, branches, related code and
tests), `stale_finding` (evidence for/against, missing evidence, blockers), `lifecycle_evaluation`
(suggested state and what each next transition still needs), `cleanup_plan`, and `patch` (only when
the flag is ready for cleanup). Field guide: `services/lifecycle-engine/docs/api-contract.md`.

Everything returned is a recommendation. Running an analysis never changes the flag, its lifecycle
status, Flagsmith or any repository.

- **Install:** `pip install -r requirements-engine.txt`. The engine is imported lazily, so without it
  the API runs normally and only these endpoints answer 503. Vercel installs `requirements.txt`, so
  **the deployed API has no engine yet**; shipping it there needs a decision (a git dependency on
  `services/lifecycle-engine`, or including it in the build).
- **Evidence used:** every synced environment's state (production = names in
  `PRODUCTION_ENVIRONMENT_NAMES`; overrides and multivariate splits count as active targeting; last
  change dates give configuration stability), Flagsmith's creation date, tags, owners and archived
  status. Flags never synced fall back to the single mirrored environment. **Still missing:**
  Flagsmith change history, evaluation analytics, and owner confirmation that a flag is no longer
  needed, so a flag reaches at most `potentially_stale`, never `ready_for_cleanup`, until owner
  confirmation is collected. `result.data_notes` lists what each analysis approximated (e.g. identity
  overrides unknown on Edge projects, treated as none).
- **LLM narrative:** set `FLAGPILOT_LLM_PROVIDER` in `.env`. Unset means a deterministic narrative.
  - `groq`: Groq's API. Set `GROQ_API_KEY`; `FLAGPILOT_LLM_MODEL` defaults to `openai/gpt-oss-120b`.
    With `FLAGPILOT_LLM_JSON_MODE=auto` (default), models Groq supports for structured outputs
    (the `openai/gpt-oss-*` family and a few preview models) get `json_schema`; others such as
    `llama-3.3-70b-versatile` get JSON mode. Groq's free tier has low rate limits; a 429 falls back
    to the deterministic narrative.
  - `openai-compatible`: any OpenAI Chat Completions server (OpenAI, OpenRouter, Ollama, vLLM,
    LM Studio, LiteLLM). Needs `FLAGPILOT_LLM_MODEL` and `FLAGPILOT_LLM_BASE_URL` (with the version
    path, e.g. `https://api.openai.com/v1`); `FLAGPILOT_LLM_API_KEY` is optional for local servers.
    `FLAGPILOT_LLM_JSON_MODE=json_object` or `none` for servers without structured outputs
    (`auto`, the default, means `json_schema` here).
  - `anthropic`, `claude-code` (local only; needs a signed-in Claude Code CLI) or `mock`.
  - A misconfigured provider makes `POST .../analysis` answer 503 with the reason; send
    `{"use_llm": false}` to analyze without it. Whatever the model returns is validated against the
    engine's evidence; invalid narratives are rejected and the deterministic one is used.
- Blank values in `.env` count as unset (`env_ignore_empty`), so copying `.env.example` and leaving
  optional entries empty is fine.
- Runs synchronously in the request, like repository scans; very large repos may hit Vercel's time limit.

## Not built yet

Roles within a workspace (all members can do everything), handling uninstalled GitHub Apps, GitHub App integration, pushing edits back to Flagsmith, scheduled/background sync.
