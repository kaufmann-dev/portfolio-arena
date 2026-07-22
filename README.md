# Portfolio Arena

A self-hosted web app that runs a long-term experiment: **can LLMs pick portfolios that beat SPY?**

Portfolio Arena is evaluator-agnostic: it exposes an authenticated MCP contract for scheduling and
submitting automated evaluations without containing or deploying an evaluator itself. The companion
[Portfolio Arena Evaluator](https://github.com/kaufmann-dev/portfolio-arena-evaluator) is one client
of that contract. Manual prompt-copy and MCP workflows remain available.

The app simulates the allocations as paper portfolios from Yahoo Finance data and tracks them live
against SPY on a public leaderboard. It is an _arena_: honest, deterministic measurement — not
trading and not advice.

## Architecture

- **Backend** — FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL (`backend/`). Serves the built SPA
  with fallback routing.
- **Frontend** — Svelte 5 + Vite + TypeScript SPA (`frontend/`), built to `frontend/dist/`.
- **Admin authentication** — confidential OpenID Connect Authorization Code + PKCE, backed by
  opaque server-side sessions. Public leaderboard and detail views remain anonymous.
- **Prices** — Yahoo Finance chart endpoint (daily adjusted closes), fetched in parallel with
  httpx and cached in Postgres with a ~1h TTL.
- **No stored NAVs.** Every NAV series is recomputed on request from the
  entered allocations + cached price series. Adjusted closes change retroactively
  (dividends/splits), so recomputation is _more_ correct than snapshotting.
- **MCP server** (`/mcp`) — an API-key-authenticated [Model Context Protocol](https://modelcontextprotocol.io)
  endpoint exposing the full app surface as tools, including the evaluator lease/submission
  protocol. See below.

## Experiment-integrity rules (enforced in code)

- **No backdating / no lookahead.** An allocation entered at time T takes effect at the first
  market close strictly after T (early closes honored). Entered Saturday → effective Monday's
  close.
- **Positions lock at the effective close.** Until then there is a typo-correction window
  (edit/delete allowed); afterwards positions and effective date are frozen — only the note
  stays editable.
- **One portfolio, one prompt.** A portfolio has a fixed prompt chosen at creation, like its
  agent; it can be reassigned later but is not chosen per allocation.
- **Structured allocation policy.** Every prompt defines server-enforced minimum and maximum
  position weights. The default is 10–25%, which implies 4–10 positions.
- **Benchmarks use the identical engine.** `SPY Buy & Hold` and `RSP Buy & Hold` are system
  portfolios whose single 100% allocation is auto-aligned to the earliest real portfolio
  inception, valued by the same code path at zero cost.
- **Costs on turnover.** Every trade pays a flat fee (default 10 bps of traded notional, frozen
  per portfolio at creation). Initial deployment pays one side; rebalances pay both sides.
- **Notes.** Each allocation has an optional free-text note (e.g. the model's regime call).
- **Honesty labels.** Portfolios younger than 6 months are badged "too early to judge"; stale or
  frozen (possibly delisted) price data is flagged, never guessed.

## Instruments

Portfolios are fully invested, long-only, and USD-denominated. Accepted Yahoo instrument types are
`EQUITY` and `ETF`; adjusted closes provide the total-return basis. Cash, mutual funds, crypto,
raw indices, FX pairs, futures, shorts, and leverage are rejected. Current S&P 500 membership can
be part of a strategy prompt, but is deliberately a research judgment rather than a stale hard-coded
symbol list.

## MCP server

The app mounts a streamable-HTTP [MCP](https://modelcontextprotocol.io) server at `/mcp`. It
exposes the entire app surface as tools — everything an admin or visitor can do (manage
portfolios, agents, prompts, allocations; validate symbols; read the leaderboard and
per-portfolio history) — **except** API-key management, which stays in the admin panel.

- **Auth.** Every request needs an API key (`Authorization: Bearer <key>`, or `X-API-Key`);
  there is no anonymous access. Create and revoke keys in the admin panel's **API Keys** tab.
  The plaintext key is shown once at creation; only a SHA-256 hash is stored.
- **Flagship read tools.** `get_portfolio(slug_or_id)` returns everything an agent needs to
  rebalance one portfolio (strategy and structured policy, drifted holdings with entry/current
  prices, the full allocation history with general and per-position notes, performance metrics).
  `get_arena_overview()` compares every portfolio's performance at once.
- **Automation tools.** `get_evaluation_schedule` returns Arena's server time and next actionable
  NYSE session (`scheduled_for`, `opens_at`, `cutoff_at`, and `state`).
  `begin_evaluation_run`, `submit_evaluation_allocation`, and `fail_evaluation_run` enforce the
  window, lease, two-attempt limit, and atomic submission. `list_evaluation_runs` exposes the
  persisted audit history also shown in the admin evaluation-history UI.
- **Connecting a client.** e.g. `claude mcp add --transport http arena https://<host>/mcp
--header "Authorization: Bearer <key>"`.

## Authentication Setup

Admin login uses OIDC Authorization Code + PKCE (`S256`) and stores a server-side opaque session for
admin-only access; provider policy defines who is admitted.

- **Public Client:** Off (the backend stores a client secret).
- **Callback URL:** `/api/auth/callback`
- **Logout Callback URL:** `/api/auth/logged-out`
- **Authentication environment variables:** `ARENA_PUBLIC_URL`, `ARENA_OIDC_ISSUER_URL`,
  `ARENA_OIDC_CLIENT_ID`, `ARENA_OIDC_CLIENT_SECRET`, `ARENA_OIDC_STATE_SECRET` (all required),
  documented in [Environment Variables](#environment-variables).

## Evaluator Integration

Arena is the sole scheduling authority. `get_evaluation_schedule()` returns `server_time`,
`scheduled_for`, `opens_at`, `cutoff_at`, and `state` (`upcoming` or `open`). Before a trading-day
window opens it returns that day's upcoming session; while the window is active it returns that
session as open; after cutoff and on non-trading days it returns the next session. Scheduled close
times include NYSE holidays, daylight-saving changes, and early closes.

External evaluators choose their own portfolio/model pairs and poll this tool. Arena retains all
leases, attempt limits, cutoff enforcement, proposal and symbol validation, atomic submission,
persistence, and evaluation history. The repositories share no source package; their only boundary
is the authenticated MCP contract. See the standalone
[Portfolio Arena Evaluator](https://github.com/kaufmann-dev/portfolio-arena-evaluator) for the Codex
worker, Podman setup, and deployment instructions.

When upgrading an existing Arena database, migration `0006` intentionally aborts if historical cash
positions exist. Back up the database and resolve those rows before deploying; the migration will
not silently rewrite the experiment's history.

Migration `0007` preserves portfolios, allocations, prompts, agents, settings, evaluation history,
price data, and MCP API keys. It removes only the obsolete local-password user table and replaces it
with short-lived browser-session records; existing JWT browser sessions stop working immediately.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Backend (needs a PostgreSQL, e.g.:
#   podman run -d --name arena-pg -e POSTGRES_PASSWORD=arena -e POSTGRES_DB=arena \
#     -p 5432:5432 postgres:16-alpine)
export DATABASE_URL=postgresql://postgres:arena@localhost:5432/arena
export ARENA_PUBLIC_URL=http://localhost:5173
export ARENA_OIDC_ISSUER_URL=https://identity.example.com/application/o/portfolio-arena
export ARENA_OIDC_CLIENT_ID=portfolio-arena-local
export ARENA_OIDC_CLIENT_SECRET=set-locally
export ARENA_OIDC_STATE_SECRET="$(openssl rand -hex 32)"
cd backend && ../.venv/bin/uvicorn app.main:app --reload

# Frontend dev server (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

### Tests

```sh
cd backend && ../.venv/bin/python -m pytest        # valuation engine, calendar, API
cd frontend && npm run check && npm run build      # svelte-check + production build
cd frontend && npm run format                      # Prettier (format:check to verify only)
```

API tests use [testcontainers](https://testcontainers.com/) and start a throwaway Postgres via
the podman user socket (`systemctl --user start podman.socket`), or set `TEST_DATABASE_URL` to
reuse an existing database. Yahoo is stubbed in tests; nothing hits the network.

## Coolify Deployment

- Create one application from `kaufmann-dev/portfolio-arena` with Build Pack `Nixpacks` and Base
  Directory `/`.
- Attach a PostgreSQL resource, configure a public domain, and use `GET /api/leaderboard` as the
  health check.
- The tracked `nixpacks.toml` builds the SPA, runs migrations, and starts FastAPI on Coolify's
  injected `PORT`.
- Deploy an evaluator independently. The companion
  [Portfolio Arena Evaluator](https://github.com/kaufmann-dev/portfolio-arena-evaluator) documents
  its separate Dockerfile application and persistent Codex storage.

### Environment Variables

**Required**

Web app:

| Variable                   | Purpose                                                               |
| -------------------------- | --------------------------------------------------------------------- |
| `DATABASE_URL`             | PostgreSQL connection URL                                             |
| `ARENA_PUBLIC_URL`         | Canonical externally reachable origin, with no path    (**required**) |
| `ARENA_OIDC_ISSUER_URL`    | OIDC issuer URL used for discovery                    (**required**)  |
| `ARENA_OIDC_CLIENT_ID`     | Confidential OIDC client ID                           (**required**)  |
| `ARENA_OIDC_CLIENT_SECRET` | Confidential OIDC client secret                       (**required**)  |
| `ARENA_OIDC_STATE_SECRET`  | Random secret of at least 32 characters for OIDC state (**required**) |

**Optional**

Web app:

| Variable                        | Default | Purpose                             |
| ------------------------------- | ------- | ----------------------------------- |
| `ARENA_DEFAULT_COST_BPS`        | `10`    | Default cost bps for new portfolios |
| `ARENA_DB_CONNECT_RETRIES`      | `30`    | Retries before failing startup      |
| `ARENA_DB_CONNECT_RETRY_DELAY`  | `2.0`   | Seconds between retries             |
| `ARENA_PRICE_CACHE_TTL_SECONDS` | `3600`  | Price cache TTL                     |
| `PORT`                          | `8000`  | Listen port                         |

## Non-goals (v1)

No broker integration, OpenAI Platform API execution, shorts or leverage, options/futures,
intraday prices, cash positions, OpenCode automation, application-managed user accounts, external
notifications, historical backtesting, or significance testing beyond the age badge.
