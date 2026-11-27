# FlagPilot API

Feature flag lifecycle management backend. FastAPI + SQLAlchemy 2 + Alembic on Supabase Postgres.

FlagPilot stores lifecycle metadata and orchestration history; **Flagsmith remains the source of
truth for actual flag configuration.**

## Run locally

```bash
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in DATABASE_URL (ask the backend owner)
alembic upgrade head          # apply migrations
uvicorn app.main:app --reload # http://localhost:8000/docs
pytest                        # tests use in-memory SQLite, never Supabase
```

## API contract (v0.1)

Interactive docs: `/docs` (Swagger) and `/redoc`. Machine-readable schema: `/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check |
| GET | `/api/flags?status=` | List flags (newest first), optional lifecycle filter |
| POST | `/api/flags` | Create flag → 201, 409 if name exists. With `integration_id`, creates it in Flagsmith first (502 if Flagsmith fails; nothing stored) |
| GET | `/api/flags/{id}` | Get flag → 404 if missing |
| PATCH | `/api/flags/{id}` | Update description/owner/project_name/environment/enabled |
| DELETE | `/api/flags/{id}` | Delete flag → 204 |
| POST | `/api/flags/{id}/lifecycle` | `{"status": "...", "reason": "..."}` → 422 if transition not allowed |
| GET | `/api/flags/lifecycle/transitions` | Map of status → allowed next statuses |
| GET/POST | `/api/repositories` | List / register a repo (`{"full_name": "org/repo"}`) |
| GET/DELETE | `/api/repositories/{id}` | Get / remove repo |
| GET/POST | `/api/integrations` | List / add integration (`provider`: `flagsmith` \| `github`, optional write-only `api_token`) |
| GET/PATCH/DELETE | `/api/integrations/{id}` | Get / update (name, config, rotate `api_token`) / remove |
| POST | `/api/integrations/{id}/test` | Check the API key → `{ok, detail, organisations}`; sets `status` |
| GET | `/api/integrations/{id}/flagsmith/projects` | Projects visible to the key |
| GET | `/api/integrations/{id}/flagsmith/projects/{pid}/environments` | Environments (`id`, `name`, `api_key`) |
| POST | `/api/integrations/{id}/sync` | Import/update flags from `config.project_id` → `{created, linked, updated, unchanged, conflicts, missing_in_flagsmith}` |
| GET | `/api/audit-logs?resource_type=&resource_id=&limit=` | Audit trail, newest first |

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

API tokens are encrypted with `ENCRYPTION_KEY` (Fernet) and never returned by the API.
**Back up `ENCRYPTION_KEY`**: if it's lost or changed, stored tokens can't be decrypted and must be re-entered.

## Database notes (Supabase)

- Connect through the **Session pooler** (port 5432) with `sslmode=require`.
- Every table has Row Level Security enabled with no policies, so Supabase's public REST API
  (anon key) cannot read it. **New tables must enable RLS in their migration too.**
- Schema changes: edit models → `alembic revision --autogenerate -m "..."` → review → `alembic upgrade head`.

## Not built yet

Authentication, GitHub App integration, pushing edits back to Flagsmith, scheduled/background sync.
