# FlagPilot

**FlagPilot** (web app: **Seshat**) is a feature-flag lifecycle agent for teams using
[Flagsmith](https://flagsmith.com) and GitHub. It follows every flag from first commit to final
cleanup: it syncs flags from Flagsmith, finds where each flag is used in your code, judges whether a
flag is still active and useful, plans and verifies safe cleanups, and runs A/B experiments on
multivariate flags.

Flagsmith stays the source of truth for flag configuration. FlagPilot reads it, adds lifecycle
tracking and analysis on top, and **never changes live traffic, merges code or archives flags on its
own**. Everything it produces is a recommendation for a person to approve.

- Web app: Next.js (`apps/web`)
- API: FastAPI on Supabase Postgres (`apps/api`), deployed at **https://flagpilot-api.vercel.app**
  (interactive docs at `/docs`)
- Lifecycle intelligence engine: Python library (`services/lifecycle-engine`)

---

## Contents

1. [Features](#features)
2. [How it works](#how-it-works)
3. [Repository layout](#repository-layout)
4. [Prerequisites](#prerequisites)
5. [Quick start: run the web app against the deployed API](#quick-start-run-the-web-app-against-the-deployed-api)
6. [Full local setup](#full-local-setup)
7. [Configuration reference](#configuration-reference)
8. [Setting up the external services](#setting-up-the-external-services)
9. [Using FlagPilot](#using-flagpilot)
10. [Sending A/B experiment events from your app](#sending-ab-experiment-events-from-your-app)
11. [Testing](#testing)
12. [CI/CD and deployment](#cicd-and-deployment)
13. [Troubleshooting](#troubleshooting)
14. [Limitations](#limitations)
15. [Further documentation](#further-documentation)

---

## Features

| Area | What you get |
|---|---|
| **Board** | Every flag as a strip across 8 lifecycle stages: created → in development → testing → rolling out → fully released → ready for cleanup → cleanup in progress → retired. Move flags with a reason; the history is kept. |
| **Flagsmith sync** | Imports features and keeps them up to date: each environment's state (on/off, value, segment and identity overrides, multivariate split, last change), creation date, tags, owners, archived status. |
| **GitHub repositories** | Imports the repositories the GitHub App can read and links each to the Flagsmith project holding its flags (chosen automatically when the names clearly match, or picked by you). Scans find every place a flag appears in the default branch. |
| **AI flag analysis** | For each flag: **active / inactive** and **useful / not useful**, with a confidence, the evidence behind each label, a recommendation and the limits of the analysis. Rules over verified evidence decide the labels; your configured AI model writes the explanation, which is fact-checked against that evidence. |
| **Lifecycle intelligence** | SDK-aware code discovery (TypeScript/JavaScript, Tree-sitter), branch analysis, stale-flag detection with evidence for and against, lifecycle suggestions, cleanup plans and suggested patches. |
| **A/B experiments** | Experiments on multivariate flags. Your app reports exposures and conversions; FlagPilot computes conversion rates, lift with confidence intervals, corrected p-values, probability to beat control, a sample-ratio check and the sample size needed before deciding. |
| **Workspaces** | One workspace per GitHub account or organization that installs the GitHub App. Organization members share it (verified with GitHub); data is isolated per workspace. |
| **Activity** | An audit log of every change, sync, scan, analysis and experiment action. |

## How it works

```
               ┌──────────────────── apps/web (Next.js, "Seshat") ────────────────────┐
  browser ───▶ │ Board · Experiments · Repositories · Connections · Activity          │
               └───────────────┬──────────────────────────────────────────────────────┘
                               │  HTTPS + Supabase access token (+ X-Workspace-Id)
               ┌───────────────▼──────────────────── apps/api (FastAPI) ───────────────┐
               │ auth (Supabase JWT) · workspaces & tenancy · flags & lifecycle        │
               │ Flagsmith sync · GitHub App import & scans · analyses · experiments   │
               │           │                                                           │
               │           └── imports ─▶ services/lifecycle-engine (Python library)   │
               │                          discovery · code intelligence · stale        │
               │                          detection · lifecycle · verdicts · A/B stats │
               │                          · cleanup plans & patches · LLM providers    │
               └──┬───────────────┬──────────────────┬─────────────────┬──────────────┘
                  │               │                  │                 │
             Supabase       Flagsmith Admin     GitHub App         LLM provider
             Postgres +     API (org API key)   (repo contents,    (optional: Groq,
             Auth (GitHub                        org members)       OpenAI-compatible,
             sign-in)                                               Anthropic, ...)
```

The typical flow:

1. **Sign in with GitHub** (Supabase Auth).
2. **Install the GitHub App** on your account or organization. That creates your workspace.
3. **Connect Flagsmith** with an organisation API key (Connections page).
4. **Import repositories** from GitHub and make sure each is linked to the Flagsmith project that
   holds its flags (Repositories page).
5. **Sync** from Flagsmith (Board). Flags appear with their state in every environment.
6. **Scan** repositories to see where each flag is used, and **analyze** a flag (flag drawer → AI
   analysis) to get its verdict, stale assessment and cleanup plan.
7. **Run experiments** on multivariate flags and decide with the statistics (Experiments page).

Principles the engine follows:

- **Facts come from deterministic analysis**, not the AI: references, files, lines, configuration,
  test results and lifecycle states are never invented. The AI only explains, and its output is
  rejected if it cites anything that isn't in the evidence or claims a flag is safe to delete.
- **Missing evidence is never treated as safety.** Zero code references or a 100% rollout alone never
  makes a flag "safe to remove".
- **A person approves every change.** FlagPilot doesn't merge, push, archive or change Flagsmith
  traffic.

## Repository layout

```
.
├── apps/
│   ├── api/                       FastAPI backend (Python 3.12)
│   │   ├── app/
│   │   │   ├── api/routes/        flags, analysis, experiments, repositories, integrations, github, workspaces, audit logs
│   │   │   ├── core/              config, database, auth, tenancy, lifecycle states, security
│   │   │   ├── models/            SQLAlchemy models
│   │   │   ├── schemas/           Pydantic request/response models
│   │   │   └── services/          Flagsmith client & sync, GitHub App client, code scan, engine bridge, audit
│   │   ├── alembic/               database migrations
│   │   ├── scripts/vendor-engine.sh   copies the engine into the API bundle for Vercel
│   │   ├── tests/                 pytest (in-memory SQLite)
│   │   ├── requirements.txt       runtime dependencies (used by Vercel)
│   │   └── requirements-engine.txt    + the engine, editable (local development and CI)
│   └── web/                       Next.js 16 + React 19 + Tailwind + shadcn/ui (Node 24)
│       └── src/
│           ├── app/(app)/         pages: board (/), experiments, repositories, connections, activity
│           ├── components/seshat/ app components (board strips, flag drawer, AI analysis, ...)
│           └── lib/               API client and types, Supabase client, lifecycle helpers
├── services/
│   └── lifecycle-engine/          intelligence engine (Python ≥ 3.11)
│       ├── lifecycle_engine/      discovery, analyzers, state_machine, policies, agents, cleanup, experiments, schemas
│       ├── sample_repos/          sample TypeScript repos used by the tests
│       ├── docs/                  architecture, API contract, integration notes
│       └── tests/
└── .github/workflows/ci.yml       CI (tests, migrations, web build) and CD (migrate + deploy on main)
```

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Git | any recent | cloning |
| Python | **3.12** for the API (`apps/api/.python-version`); the engine supports 3.11+ | API and engine |
| Node.js + npm | **24** (what CI uses) | web app |

Accounts and services (see [Setting up the external services](#setting-up-the-external-services)):

- A **Supabase** project with GitHub sign-in enabled. It provides the database and authentication.
  You only need your own if you run the API yourself; the quick start uses the team's.
- A **GitHub App** (the team's is `seshat.buzz`). Needed to import and scan repositories.
- A **Flagsmith** organisation API key, preferably an admin key.
- Optionally an **LLM** API key (Groq, an OpenAI-compatible server, or Anthropic) for AI-written
  explanations. Everything works without one.

## Quick start: run the web app against the deployed API

The fastest way to work on the frontend. You only run Next.js locally; it talks to the deployed API
and the team's Supabase project.

```bash
git clone https://github.com/NihaalNO/flagsmith-hackathon.git
cd flagsmith-hackathon/apps/web
cp .env.example .env.local     # PowerShell: Copy-Item .env.example .env.local
```

Fill in `.env.local` with the Supabase project URL and publishable key (both public). Ask the team,
or read them from the API's sign-in test page at
`https://flagpilot-api.vercel.app/dev/login`, which embeds both:

```
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
NEXT_PUBLIC_API_URL=https://flagpilot-api.vercel.app
```

Then:

```bash
npm ci
npm run dev        # http://localhost:3000
```

`http://localhost:3000` must be in Supabase's allowed redirect URLs (Authentication → URL
Configuration) for GitHub sign-in to come back to your local app. It already is for the team's project.

## Full local setup

Run all three parts locally: the engine, the API and the web app.

### 1. Clone

```bash
git clone https://github.com/NihaalNO/flagsmith-hackathon.git
cd flagsmith-hackathon
```

### 2. API (`apps/api`)

The API needs a **PostgreSQL** database (migrations use Postgres features) and a **Supabase project**
for sign-in. The simplest choice is a Supabase project's database; a local Postgres also works for
data, with Supabase still handling sign-in.

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate                  # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-engine.txt     # API + lifecycle engine (editable install)
cp .env.example .env                       # PowerShell: Copy-Item .env.example .env
```

Edit `.env` (all variables are listed in the [configuration reference](#api-appsapienv)). At minimum:

- `DATABASE_URL`: your Postgres URL with the `postgresql+psycopg://` prefix. For Supabase, use the
  **Session pooler** URL (Dashboard → Connect), port 5432, `?sslmode=require`.
- `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`: the Supabase project used for sign-in.
- `ENCRYPTION_KEY`: generate one with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Keep it safe: stored Flagsmith keys can't be decrypted without it.
- `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY`: needed to import and scan repositories.

Then create the tables and start the server:

```bash
alembic upgrade head
uvicorn app.main:app --reload      # http://localhost:8000  (docs: /docs, health: /api/health)
```

Without a frontend you can test the API at `http://localhost:8000/dev/login`: sign in with GitHub,
copy the token, then in `/docs` click **Authorize** and paste it.

### 3. Lifecycle engine (`services/lifecycle-engine`)

`requirements-engine.txt` already installed the engine into the API's environment. To work on the
engine on its own (its tests, CLI and sample repositories):

```bash
cd services/lifecycle-engine
python -m venv .venv
source .venv/bin/activate              # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
python -m lifecycle_engine.cli scan sample_repos/checkout-web --repo acme/checkout-web
```

### 4. Web app (`apps/web`)

```bash
cd apps/web
cp .env.example .env.local
npm ci
npm run dev                    # http://localhost:3000
```

Point the web app at your local API by setting `NEXT_PUBLIC_API_URL=http://localhost:8000` in
`.env.local`, and make sure `CORS_ORIGINS` in the API's `.env` includes `http://localhost:3000`
(it does by default).

## Configuration reference

### API (`apps/api/.env`)

Blank values count as unset, so you can leave optional entries empty.

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Postgres URL, `postgresql+psycopg://...` (Supabase: Session pooler, `sslmode=require`). |
| `SUPABASE_URL` | yes | Supabase project URL; the API verifies sign-in tokens against it. |
| `SUPABASE_PUBLISHABLE_KEY` | for `/dev/login` | Public key used by the API's sign-in test page. |
| `ENCRYPTION_KEY` | yes | Fernet key that encrypts stored Flagsmith API keys. Back it up. |
| `CORS_ORIGINS` | no | Comma-separated browser origins allowed to call the API. Default `http://localhost:3000,http://localhost:5173`. |
| `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY` | for GitHub features | GitHub App credentials; the private key on one line with literal `\n`. |
| `PRODUCTION_ENVIRONMENT_NAMES` | no | Flagsmith environment names treated as production (case-insensitive). Default `production,prod,live`. |
| `FLAGPILOT_LLM_PROVIDER` | no | `groq`, `openai-compatible`, `anthropic`, `claude-code` (local only), `mock`, or empty for no AI. |
| `GROQ_API_KEY` | for `groq` | Groq API key. Model defaults to `openai/gpt-oss-120b`. |
| `FLAGPILOT_LLM_MODEL` | for `openai-compatible` | Model name, e.g. `gpt-4.1-mini`, `llama3.1`. |
| `FLAGPILOT_LLM_BASE_URL` | for `openai-compatible` | Server URL including the version path, e.g. `https://api.openai.com/v1`, `http://localhost:11434/v1` (Ollama). |
| `FLAGPILOT_LLM_API_KEY` | depends | API key for the LLM server (optional for local servers). |
| `FLAGPILOT_LLM_JSON_MODE` | no | `auto` (default), `json_schema`, `json_object` or `none`. |

### Web app (`apps/web/.env.local`)

All three are public values (they end up in the browser).

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable key. |
| `NEXT_PUBLIC_API_URL` | API base URL: `https://flagpilot-api.vercel.app` or `http://localhost:8000`. |

## Setting up the external services

### Supabase (database and sign-in)

1. Create a project. Copy the **Session pooler** connection string (Dashboard → Connect) into
   `DATABASE_URL` with the `postgresql+psycopg://` prefix.
2. Authentication → Providers → **GitHub**: enable it with a GitHub OAuth app. Sign-in is GitHub only.
3. Authentication → URL Configuration: add your app URLs to the redirect allow-list, e.g.
   `http://localhost:3000`, `http://localhost:8000/dev/login` and your deployed frontend URL.
4. Run `alembic upgrade head`. Every table has Row Level Security enabled with no policies, so
   Supabase's public REST API can't read FlagPilot's data; the backend connects as the owner.

### GitHub App (repositories and workspaces)

Create a GitHub App (Settings → Developer settings → GitHub Apps) with:

- **Repository permissions:** Contents: Read-only, Metadata: Read-only.
- **Organization permissions:** Members: Read-only (to verify who belongs to an organization's
  workspace).
- No webhooks are needed.

Generate a private key and set `GITHUB_APP_ID`, `GITHUB_APP_SLUG` and `GITHUB_APP_PRIVATE_KEY`.
Users install the app from the link the app shows (`https://github.com/apps/<slug>/installations/new`).

### Flagsmith

In Flagsmith: Organisation Settings → API Keys → create an **organisation API key**. Turn on
**Admin** so FlagPilot can read every project and create a project for a repository that has none.
Paste it on the **Connections** page. It's stored encrypted and never shown again.

### LLM provider (optional)

Set `FLAGPILOT_LLM_PROVIDER` and its variables (see the configuration reference). Examples:

```
# Groq
FLAGPILOT_LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...

# Any OpenAI-compatible server (OpenAI, OpenRouter, Ollama, vLLM, LM Studio, LiteLLM)
FLAGPILOT_LLM_PROVIDER=openai-compatible
FLAGPILOT_LLM_MODEL=gpt-4.1-mini
FLAGPILOT_LLM_BASE_URL=https://api.openai.com/v1
FLAGPILOT_LLM_API_KEY=sk-...
```

Each analysis with a provider configured makes two LLM calls: one for the cleanup plan and one for
the flag verdict. Without a provider you get plain-language summaries built from the same evidence.
`GET /api/health` shows which provider is active (names only, never keys).

## Using FlagPilot

1. **Sign in** at the app with GitHub.
2. **Install the GitHub App** on your account or organization when asked, and choose the repositories.
   Back in the app, pick the workspace.
3. **Connections:** paste your Flagsmith organisation API key and check it says *Connected*.
4. **Repositories:** click **Import from GitHub**. Each repository is linked to a Flagsmith project:
   an existing one with a matching name, otherwise a new one. If a repository's flags live in a
   different project, choose it in the **Flagsmith project** column; that project's flags are
   imported right away.
5. **Board:** click **Sync** to import or refresh flags from Flagsmith. Warnings explain anything that
   was skipped.
6. **Repositories → Scan:** finds every place each flag's key appears in the default branch.
7. **Open a flag** on the board:
   - **AI analysis → Analyze** downloads the repository code and returns the verdict (active or
     inactive, useful or not useful), the reasons, a recommendation and its caveats. The board strip
     then shows the verdict.
   - **Move this flag** changes its lifecycle stage, with a reason saved in its history.
   - **In code** lists where the flag is used, with GitHub links.
8. **Experiments → New experiment:** pick a multivariate flag, name the conversion event, and use the
   flag's Flagsmith variants or enter your own. Copy the event key (shown once), then **Start**. When
   the decision says *Winner*, *Control is better* or *No significant difference* at the planned
   sample size, **Stop** and **Conclude**. Roll the result out in Flagsmith yourself.

## Sending A/B experiment events from your app

Send events **from your server**, not the browser, so the key stays secret. Report an `exposure` when
a user is shown a variant (once is enough) and a `conversion` when they complete the goal:

```bash
curl -X POST https://flagpilot-api.vercel.app/api/experiments/<experiment-id>/events \
  -H "X-Experiment-Key: fpx_..." \
  -H "Content-Type: application/json" \
  -d '{"events": [
        {"type": "exposure",   "subject_id": "user-123", "variant": "one-page"},
        {"type": "conversion", "subject_id": "user-123"}
      ]}'
```

- `variant` is the variant key as configured in the experiment. Typically it's the value your
  app got from Flagsmith, e.g. `flagsmith.getValue("checkout_layout")`.
- Up to 1,000 events per request, accepted only while the experiment is running.
- Each user counts once, in the first variant reported. Only a hash of `subject_id` is stored.
- The response counts accepted events, duplicates, variant conflicts, conversions without an exposure
  and unknown variants.
- Lost or leaked key: **New key** on the experiment replaces it immediately.

Statistics: per-variant conversion rate with a 95% interval, lift against control with its interval,
two-proportion z-test with Holm correction across variants, Bayesian probability to beat control, a
chi-square check that traffic matches the planned split, and the per-variant sample size needed for
the minimum detectable effect. A result that looks significant before that sample size is shown as
*Trending, keep running*.

## Testing

```bash
# API (in-memory SQLite; never touches Supabase)
cd apps/api && pytest -q

# Lifecycle engine (includes tests that run the sample repos' own Node tests if Node is installed)
cd services/lifecycle-engine && pytest -q

# Web app
cd apps/web && npm run lint && npx tsc --noEmit && npm run build
```

Opt-in tests that call real services (and may cost money):

```bash
cd services/lifecycle-engine
FLAGPILOT_LIVE_GROQ=1 GROQ_API_KEY=gsk_... pytest -s -m groq_live      # Groq
FLAGPILOT_LIVE_CLAUDE=1 pytest -s -m claude_live                       # Claude Code CLI
```

In PowerShell, set the variables first, e.g. `$env:FLAGPILOT_LIVE_GROQ = "1"; $env:GROQ_API_KEY = "gsk_..."`.

## CI/CD and deployment

`.github/workflows/ci.yml` runs on every pull request and every push to `main`.

**CI**
| Job | What it checks |
|---|---|
| API tests + migrations | `pytest` with the engine installed, then `alembic upgrade head`, `downgrade base`, `upgrade head` on a real Postgres 17. |
| Lifecycle engine tests | `pytest` on Python 3.12. |
| Web lint + build | `npm run lint`, `npx tsc --noEmit`, `npm run build` on Node 24. |

**CD (pushes to `main` only, after CI passes)**
1. **Migrate Supabase:** `alembic upgrade head` against the production database.
2. **Deploy:** the API and the web app to Vercel. For the API, `scripts/vendor-engine.sh` first copies
   the lifecycle engine into `apps/api` (Vercel uploads only that folder).

> **A push to `main` is a production release:** it migrates the production database and deploys
> both apps. Use pull requests for anything you want reviewed first.

Required repository secrets: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_API_PROJECT_ID`,
`VERCEL_WEB_PROJECT_ID`, `DATABASE_URL`. Without them the deploy jobs skip instead of failing.
Runtime variables (database, Supabase, encryption key, GitHub App, LLM) are set in each Vercel project.

**Manual deploy of the API**

```bash
cd apps/api
alembic upgrade head            # migrations first
scripts/vendor-engine.sh        # include the engine in the bundle
vercel deploy --prod
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| **"Method Not Allowed"** when opening an API URL in the browser | The address bar sends a GET; actions such as sync are POST requests made by the app's buttons. Use the app, or `/docs`. |
| **"Nothing to sync"** (older versions) / sync imports nothing useful | No repository was linked to a Flagsmith project. Current versions sync the projects your key can see and tell you to link repositories. Link each repository to its project on the Repositories page. |
| **Scans find no flags** although they exist in Flagsmith | The repository is linked to the wrong (often new, empty) Flagsmith project. Choose the right project in the Repositories page's **Flagsmith project** column. |
| **API won't start**: validation error for a setting | A required variable is missing in `.env` (`DATABASE_URL`, `SUPABASE_URL`, `ENCRYPTION_KEY`). Blank optional values are fine. |
| **Sign-in redirects to the wrong place** or fails locally | Add your local URL (e.g. `http://localhost:3000`) to Supabase's redirect allow-list. |
| **Analysis returns 503** | Either the engine isn't installed (`pip install -r requirements-engine.txt`), or the LLM provider is misconfigured; the message names the missing variable. Send `{"use_llm": false}` to analyze without AI. |
| **AI explanation says "unavailable" or "rejected"** | The provider failed (rate limit, key, network) or its text cited something not in the evidence. The labels are still valid; a plain summary is shown instead. |
| **Experiment events return 401 / 409** | 401: wrong or missing `X-Experiment-Key`, or unknown experiment. 409: the experiment isn't running. |
| **Experiment says "Results not trustworthy"** | Sample-ratio mismatch: traffic doesn't match the planned split (assignment bug, bots, or events lost for one variant). Find the cause before reading the results. |
| **A Flagsmith sync shows warnings** | Usually a missing permission on the API key (tags, an environment); the rest still syncs. Use an admin key to avoid them. |

## Limitations

- Code discovery covers Flagsmith's **TypeScript/JavaScript** SDKs (browser, React, Node) only.
- Flagsmith change history and per-flag evaluation analytics aren't synced yet, and owner
  confirmations aren't collected in the app, so the stale assessment stops at *potentially stale*
  rather than *ready for cleanup*.
- Identity-override counts aren't reported by Flagsmith Edge projects; they're treated as none and
  each analysis notes it.
- Experiments support yes/no conversion metrics with a fixed sample size (no revenue metrics, no
  sequential testing).
- Lifecycle moves are not yet gated by the engine's evidence and approval rules (see
  `services/lifecycle-engine/docs/integration.md`).
- Repository scans, syncs and analyses run inside the request; very large repositories may hit
  Vercel's time limit.

## Further documentation

- API details, endpoints and data rules: [`apps/api/README.md`](apps/api/README.md)
- Engine overview: [`services/lifecycle-engine/README.md`](services/lifecycle-engine/README.md)
- Engine architecture and limitations: [`services/lifecycle-engine/docs/architecture.md`](services/lifecycle-engine/docs/architecture.md)
- Engine API contract and field guide: [`services/lifecycle-engine/docs/api-contract.md`](services/lifecycle-engine/docs/api-contract.md)
- Integration notes between backend and engine: [`services/lifecycle-engine/docs/integration.md`](services/lifecycle-engine/docs/integration.md)
- Live API documentation: https://flagpilot-api.vercel.app/docs
