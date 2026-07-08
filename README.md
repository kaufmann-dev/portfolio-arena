# Portfolio Arena

A self-hosted web app that runs a long-term experiment: **can LLMs pick portfolios that beat SPY?**

On a recurring cadence the operator prompts AI agents (Claude, Codex, Gemini, …) with
portfolio-management prompts, then enters each agent's proposed allocation here by hand. The app
simulates it as a paper portfolio from Yahoo Finance data and tracks it live against SPY on a
public leaderboard. It is an *arena*: honest, deterministic measurement — not trading, not
advice. The app never calls an LLM itself; agents drive it from outside (by hand or over the
MCP server).

## Architecture

- **Backend** — FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL (`backend/`). Serves the built SPA
  with fallback routing.
- **Frontend** — Svelte 5 + Vite + TypeScript SPA (`frontend/`), built to `frontend/dist/`.
- **Prices** — Yahoo Finance chart endpoint (daily adjusted closes; plain closes for FX), fetched
  in parallel with httpx and cached in Postgres with a ~1h TTL.
- **No stored NAVs, no background jobs.** Every NAV series is recomputed on request from the
  entered allocations + cached price series. Adjusted closes change retroactively
  (dividends/splits), so recomputation is *more* correct than snapshotting.
- **MCP server** (`/mcp`) — an API-key-authenticated [Model Context Protocol](https://modelcontextprotocol.io)
  endpoint exposing the full app surface as tools, so an AI agent can read its portfolio's
  history and enter rebalances programmatically instead of by hand. See below.

## Experiment-integrity rules (enforced in code)

- **No backdating / no lookahead.** An allocation entered at time T takes effect at the first
  market close strictly after T (early closes honored). Entered Saturday → effective Monday's
  close.
- **Positions lock at the effective close.** Until then there is a typo-correction window
  (edit/delete allowed); afterwards positions and effective date are frozen — only the note
  stays editable.
- **One portfolio, one prompt.** A portfolio has a fixed prompt chosen at creation, like its
  agent; it can be reassigned later but is not chosen per allocation.
- **Benchmarks use the identical engine.** `SPY Buy & Hold` and `RSP Buy & Hold` are system
  portfolios whose single 100% allocation is auto-aligned to the earliest real portfolio
  inception, valued by the same code path at zero cost.
- **Costs on turnover.** Every trade pays a flat fee (default 10 bps of traded notional, frozen
  per portfolio at creation). Initial deployment pays one side; rebalances pay both sides.
- **Notes.** Each allocation has an optional free-text note (e.g. the model's regime call).
- **Honesty labels.** Portfolios younger than 6 months are badged "too early to judge"; stale or
  frozen (possibly delisted) price data is flagged, never guessed.

## Instruments

Long-only equities & ETFs plus multi-currency cash; weights ≥ 0 and sum to exactly 100.

| Syntax                 | Type   | Return basis                                  |
| ---------------------- | ------ | --------------------------------------------- |
| `AAPL`, `SPY`, `BRK-B` | equity | adjusted close (total return)                 |
| `CASH:USD`, `CASH:EUR` | cash   | spot FX vs USD (via `EURUSD=X`), 0% interest  |

Rejected at validation with hints: raw indices (`^GSPC` → use SPY/QQQ/IWM), FX pairs (`=X` →
use `CASH:CCY`), futures (`=F` → use ETF equivalents such as SSO/GLD/TLT; Yahoo continuous
contracts have roll artifacts that would corrupt long-horizon measurement).

**Known simplification:** cash earns no interest in any currency. This slightly penalizes
cash-heavy contestants; the benchmark is unaffected.

## MCP server

The app mounts a streamable-HTTP [MCP](https://modelcontextprotocol.io) server at `/mcp`. It
exposes the entire app surface as tools — everything an admin or visitor can do (manage
portfolios, agents, prompts, allocations; validate symbols; read the leaderboard and
per-portfolio history) — **except** API-key management, which stays in the admin panel.

- **Auth.** Every request needs an API key (`Authorization: Bearer <key>`, or `X-API-Key`);
  there is no anonymous access. Create and revoke keys in the admin panel's **API Keys** tab.
  The plaintext key is shown once at creation; only a SHA-256 hash is stored.
- **Flagship read tools.** `get_portfolio(slug_or_id)` returns everything an agent needs to
  rebalance one portfolio (prompt text, drifted holdings with entry/current prices, the full
  allocation history with general and per-position notes, performance metrics).
  `get_arena_overview()` compares every portfolio's performance at once.
- **Connecting a client.** e.g. `claude mcp add --transport http arena https://<host>/mcp
  --header "Authorization: Bearer <key>"`.

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

- **Build Pack**: Nixpacks
- **Base Directory**: `/`
- **Health Check**: `GET /api/leaderboard`

Attach a separate Coolify PostgreSQL resource. Single instance assumed (in-memory rate limits).

### Environment Variables

**Required**

| Variable               | Purpose                   |
| ---------------------- | ------------------------- |
| `DATABASE_URL`         | PostgreSQL connection URL |
| `ARENA_JWT_SECRET`     | JWT signing secret        |
| `ARENA_ADMIN_EMAIL`    | Admin login email         |
| `ARENA_ADMIN_PASSWORD` | Admin password            |

**Optional**

| Variable                        | Default | Purpose                             |
| ------------------------------- | ------- | ----------------------------------- |
| `ARENA_DEFAULT_COST_BPS`        | `10`    | Default cost bps for new portfolios |
| `ARENA_DB_CONNECT_RETRIES`      | `30`    | Retries before failing startup      |
| `ARENA_DB_CONNECT_RETRY_DELAY`  | `2.0`   | Seconds between retries             |
| `ARENA_PRICE_CACHE_TTL_SECONDS` | `3600`  | Price cache TTL                     |
| `PORT`                          | `8000`  | Listen port                         |

## Non-goals (v1)

The app never calls an LLM itself and does no response parsing — agents drive it from outside,
via the admin panel or the MCP server. No broker integration, no shorts or leverage, no
options/futures, no intraday prices, no cash interest, no multi-user accounts, no notifications,
no historical backtesting, no significance testing beyond the age badge.
