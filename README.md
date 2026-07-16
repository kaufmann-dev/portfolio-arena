# Portfolio Arena

A self-hosted web app that runs a long-term experiment: **can LLMs pick portfolios that beat SPY?**

On every NYSE trading day, a separate worker can run selected portfolios through Codex using a
persisted ChatGPT login, live web search, Massive market data, and Portfolio Arena's MCP tools. It
submits each validated allocation before the close and records the complete run history. No OpenAI
Platform API key is used. Manual prompt-copy and MCP workflows remain available.

The app simulates the allocations as paper portfolios from Yahoo Finance data and tracks them live
against SPY on a public leaderboard. It is an *arena*: honest, deterministic measurement — not
trading and not advice.

## Architecture

- **Backend** — FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL (`backend/`). Serves the built SPA
  with fallback routing.
- **Frontend** — Svelte 5 + Vite + TypeScript SPA (`frontend/`), built to `frontend/dist/`.
- **Prices** — Yahoo Finance chart endpoint (daily adjusted closes), fetched in parallel with
  httpx and cached in Postgres with a ~1h TTL.
- **Evaluator** — a separate `Dockerfile.evaluator` worker invokes `codex exec` with ChatGPT auth,
  read-only Arena/Massive tools, and live web search. The runner alone receives Arena write tools.
- **No stored NAVs.** Every NAV series is recomputed on request from the
  entered allocations + cached price series. Adjusted closes change retroactively
  (dividends/splits), so recomputation is *more* correct than snapshotting.
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
- **Automation tools.** `begin_evaluation_run`, `submit_evaluation_allocation`, and
  `fail_evaluation_run` enforce the time window, lease, two-attempt limit, and atomic submission.
  `list_evaluation_runs` exposes the persisted audit history.
- **Connecting a client.** e.g. `claude mcp add --transport http arena https://<host>/mcp
  --header "Authorization: Bearer <key>"`.

## Automated Evaluator

The tracked allowlist in `evaluator.toml` maps six portfolio slugs to their Codex models. The worker
wakes on NYSE sessions, including holidays, daylight-saving changes, and early closes. It starts 90
minutes before the scheduled close, stops accepting submissions 10 minutes before, runs up to five
jobs concurrently, completes all first attempts before retrying failures, and permits at most two
25-minute attempts per portfolio. One portfolio failing never blocks the others.

The model process has read-only access to `get_portfolio`, symbol validation/search, Massive, and
live web search. It returns structured JSON. The worker then calls Arena's atomic submission tool,
where symbols, USD denomination, weights, prompt policy, lease, and cutoff are validated again. The
worker explicitly removes `OPENAI_API_KEY` and `CODEX_API_KEY` from model subprocesses.

### Local Podman Setup

Requirements: Podman, a ChatGPT account with access to the configured models, an Arena MCP API key,
and a Massive API key.

```sh
cp .env.evaluator.example .env.evaluator
# Fill in the three required values, then protect the file:
chmod 600 .env.evaluator

# Build the worker and complete Codex device login into a persistent volume.
./scripts/bootstrap-evaluator.sh

# Start the scheduler. The Codex login survives container replacement.
podman run --detach --name arena-evaluator --restart unless-stopped \
  --env-file .env.evaluator \
  --volume portfolio-arena-codex:/var/lib/codex \
  portfolio-arena-evaluator:local
```

Check it with `podman logs arena-evaluator` and `podman healthcheck run arena-evaluator`. The worker
stays alive and healthy if Codex is logged out; it waits without consuming evaluation attempts and
logs the exact `codex login --device-auth` remediation.

When upgrading an existing Arena database, migration `0006` intentionally aborts if historical cash
positions exist. Back up the database and resolve those rows before deploying; the migration will
not silently rewrite the experiment's history.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Backend (needs a PostgreSQL, e.g.:
#   podman run -d --name arena-pg -e POSTGRES_PASSWORD=arena -e POSTGRES_DB=arena \
#     -p 5432:5432 postgres:16-alpine)
export DATABASE_URL=postgresql://postgres:arena@localhost:5432/arena
export ARENA_JWT_SECRET=dev-secret ARENA_ADMIN_EMAIL=admin@example.com ARENA_ADMIN_PASSWORD=dev-password
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

- Create two Coolify applications from this repository.
- **Web app:** Build Pack `Nixpacks`, Base Directory `/`, health check `GET /api/leaderboard`.
  Attach a PostgreSQL resource. The tracked `nixpacks.toml` builds the SPA, runs migrations, and
  starts FastAPI on Coolify's injected `PORT`.
- **Evaluator:** Build Pack `Dockerfile`, Base Directory `/`, Dockerfile
  `/Dockerfile.evaluator`, no public domain or port. Add persistent storage at `/var/lib/codex`.
  After the first deploy, open its terminal and run `codex login --device-auth` once. Redeploys reuse
  the stored ChatGPT login.

### Environment Variables

**Required**

Web app:

| Variable               | Purpose                      |
| ---------------------- | ---------------------------- |
| `DATABASE_URL`         | PostgreSQL connection URL    |
| `ARENA_JWT_SECRET`     | JWT signing secret           |
| `ARENA_ADMIN_EMAIL`    | Initial admin login email    |
| `ARENA_ADMIN_PASSWORD` | Initial admin login password |

Evaluator:

| Variable            | Purpose                                                     |
| ------------------- | ----------------------------------------------------------- |
| `ARENA_MCP_URL`     | Public web-app MCP URL, ending in `/mcp`                    |
| `ARENA_MCP_API_KEY` | Key created in Admin → API Keys; not an OpenAI Platform key |
| `MASSIVE_API_KEY`   | Massive market-data credential                              |

**Optional**

Web app:

| Variable                        | Default | Purpose                             |
| ------------------------------- | ------- | ----------------------------------- |
| `ARENA_DEFAULT_COST_BPS`        | `10`    | Default cost bps for new portfolios |
| `ARENA_DB_CONNECT_RETRIES`      | `30`    | Retries before failing startup      |
| `ARENA_DB_CONNECT_RETRY_DELAY`  | `2.0`   | Seconds between retries             |
| `ARENA_PRICE_CACHE_TTL_SECONDS` | `3600`  | Price cache TTL                     |
| `PORT`                          | `8000`  | Listen port                         |

Evaluator:

| Variable                            | Default | Purpose                             |
| ----------------------------------- | ------- | ----------------------------------- |
| `EVALUATOR_MAX_CONCURRENCY`         | `5`     | Simultaneous Codex processes        |
| `EVALUATOR_POLL_SECONDS`            | `60`    | Scheduler polling interval          |
| `EVALUATOR_ATTEMPT_TIMEOUT_SECONDS` | `1500`  | Per-attempt timeout, maximum 25 min |
| `EVALUATOR_SERVICE_TIER`            | `fast`  | Codex service tier                  |

## Non-goals (v1)

No broker integration, OpenAI Platform API execution, shorts or leverage, options/futures,
intraday prices, cash positions, OpenCode automation, multi-user accounts, external notifications,
historical backtesting, or significance testing beyond the age badge.
