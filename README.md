# Portfolio Arena

A self-hosted web app that runs a long-term experiment: **can LLM stock-selection strategies
produce repeatable alpha in all-long or all-short portfolios?**

Portfolio Arena includes a website-controlled Codex evaluator. One Nixpacks deployment starts the
web app, scheduler, and evaluator worker together. The admin panel defines models and their
harness-specific capabilities, combines them into reusable Agents, and controls weekdays,
concurrency, immediate runs, cancellation, retries, and history. Manual submissions and authenticated
MCP workflows remain available.

The app maintains two separate experiments. Managed portfolios are stateful paper portfolios whose
models decide when to rebalance. Rebuilt portfolios submit an independent signal every trading day;
the arena measures every 1–20-session holding period and 10–100% exposure policy. Each track is
split into Long and Short arenas and ranked against its direction-matched SPY reference. It is an
_arena_: honest, deterministic measurement — not trading and not advice.

## Architecture

- **Backend** — FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL (`backend/`). Serves the built SPA
  with fallback routing.
- **Frontend** — Svelte 5 + Vite + TypeScript SPA (`frontend/`), built to `frontend/dist/`.
- **Evaluator** — an integrated Codex worker (`backend/app/evaluator/`) whose configuration, queue,
  leases, and history live in PostgreSQL. It uses Massive and live web search for current research.
- **Production supervisor** — one Nixpacks start command launches FastAPI and the evaluator worker,
  restarts the worker if it fails, and shuts both down together.
- **Admin authentication** — confidential OpenID Connect Authorization Code + PKCE, backed by
  opaque server-side sessions. Public leaderboard and detail views remain anonymous.
- **Prices** — Massive daily stock aggregates, fetched in parallel with httpx and converted to a
  split-and-dividend total-return basis. Series are cached in Postgres with a ~1h TTL.
- **No stored NAVs.** Managed NAVs are recomputed from allocations; rebuilt NAVs are recomputed from
  immutable daily signals and overlapping cohorts. Corporate-action adjustments change
  retroactively, so recomputation is _more_ correct than snapshotting.
- **Last-known-data fallback.** An expired cache row remains available until a Massive refresh
  succeeds. The cache refreshes after its TTL and when a newly closed session should be available
  after Massive's 15-minute delay. Responses label market data `fresh`, `stale`, or `unavailable`;
  `as_of` remains the authoritative valued close. Ordinary complete last-known data does not raise
  a UI warning; warnings are reserved for unavailable data that can make valuations incomplete.
- **MCP server** (`/mcp`) — an API-key-authenticated [Model Context Protocol](https://modelcontextprotocol.io)
  endpoint exposing the operational arena surface as tools, including evaluator administration.
  API-key management and archived prompt recovery stay browser-only; the worker-only queue and
  submission protocol is private to the deployment.

## Experiment-integrity rules (enforced in code)

- **Manual entries do not backdate; scheduled automation is an explicit exception.** A manual
  allocation or signal entered at time T takes effect at the first market close strictly after T
  (early closes honored). Scheduled evaluator runs always target their scheduled session, even when
  they submit after that close.
- **Submitted targets lock at the effective close.** Pending allocations and signals have a
  typo-correction window. A completed signal is entirely immutable; managed allocation notes retain
  their existing editable handoff behavior.
- **Portfolio resets are explicit and mode-specific.** Resetting a contestant deletes its managed
  allocation history or rebuilt signal history, cancels in-flight evaluator work, and preserves its
  identity, configuration, schedule, and evaluator audit records. A portfolio cannot switch mode or
  direction until its history has been reset and in-flight cancellation has completed.
- **One canonical strategy, two execution modes.** Managed evaluations receive holdings, allocation
  history, notes, performance, and costs. Rebuilt evaluations receive no prior portfolio state and
  construct each signal independently. Each mode has a global editable wrapper prompt under
  Admin → Settings.
- **One direction per portfolio.** A portfolio is entirely long or entirely short. Submitted weights
  are always positive and total exactly 100%; direction is portfolio metadata, so mixed books and
  signed position weights cannot enter the experiment.
- **Prompt changes are recoverable.** Editing a prompt appends an immutable version. Prompts are
  archived rather than deleted, and restoring an older snapshot creates another new version.
  Archived prompts and version history are browser-admin-only; public and MCP reads expose only the
  current version of active prompts.
- **Structured allocation policy.** Every prompt defines server-enforced minimum and maximum
  position weights. The default is 10–25%, which implies 4–10 positions.
- **SPY is synthetic and direction-matched.** Long arenas use buy-and-hold SPY. Short arenas use a
  daily rebalanced −1× SPY series. Every leaderboard pins its non-ranked reference over the same
  comparison window; there are no stored benchmark portfolios and no RSP benchmark.
- **Rebuilt policies are measured, not prompted.** Each daily signal is evaluated at holding
  horizons from 1 through 20 trading sessions. Exposure is tested from 10% through 100%; each active
  session contributes one `exposure / horizon` sleeve. At every close, the aggregate target is the
  sum of the still-active signal sleeves and the remainder stays in direction-matched SPY.
- **Short loss is capped at portfolio equity.** Short books use fixed absolute shares between
  rebalances, 100% collateralized exposure, and the same transaction-cost model as long books.
  Borrow and financing fees are not modeled. If NAV reaches zero, liquidation is absorbing: the
  displayed series remains zero, return observations stop after the liquidation close, managed
  allocations and evaluator runs are blocked until reset, and rebuilt signals may continue to be
  collected for future policy measurements.
- **Costs are measured on aggregate turnover.** Net results apply the portfolio's flat transaction
  cost to actual aggregate turnover, including changes in the SPY sleeve. Gross results remain
  available for diagnosis.
- **Notes.** Each managed allocation or rebuilt signal has optional portfolio- and position-level
  handoff notes.
- **Search-adjusted evidence.** Rankings use a HAC/Newey–West estimate and a Bonferroni-adjusted 95%
  confidence interval across the predeclared search family: 20 comparisons for Signal Alpha and the
  canonical objective, or 200 for an optimized holding-period × exposure search. Evidence is labeled
  `pending`, `inconclusive`, `positive`, or `negative`; incomplete, carried-forward, or frozen price
  data is flagged, never guessed.

The Rebuilt arena has three views. **Common Policy** chooses one holding horizon and exposure from an
equal-weight meta portfolio, then applies that policy to every eligible strategy. **Tuned** selects
each strategy's own best policy. **Signal Alpha** compares completed independent signals directly at
a selected holding horizon and exposes the full 1–20-session matrix. The default objective fixes
exposure at 100% and chooses the horizon with the highest adjusted lower confidence bound; diagnostic
objectives can instead maximize mean alpha, information ratio, or zero-rate Sharpe.

## Instruments

Portfolios are fully invested, all-long or all-short, and USD-denominated. Accepted Massive ticker
types are common stock (`CS`), ADR common stock (`ADRC`), and ETF (`ETF`). Massive split-adjusted
daily aggregates plus cumulative dividend adjustment factors provide the total-return basis. A
ticker is rejected when Massive's recent dividend history lacks those factors, because distributions
without a cumulative adjustment factor cannot be reconstructed reliably from aggregate prices
alone. Inactive or non-USD tickers, cash, mutual funds, crypto, raw indices, FX pairs, futures,
negative position weights, mixed long/short books, and leverage are rejected. Current S&P 500
membership can be part of a strategy prompt, but is deliberately a research judgment rather than a
stale hard-coded symbol list.

## MCP server

The app mounts a streamable-HTTP [MCP](https://modelcontextprotocol.io) server at `/mcp`. It exposes
the operational arena surface as tools: manage portfolios, agents, active prompts, managed
allocations, and rebuilt signals; validate symbols; read both arena tracks and per-portfolio
history; and administer the evaluator. API-key management and archived prompt recovery stay in the
admin panel.

- **Auth.** Every request needs an API key (`Authorization: Bearer <key>`, or `X-API-Key`);
  there is no anonymous access. Create and revoke keys in the admin panel's **API Keys** tab.
  The plaintext key is shown once at creation; only a SHA-256 hash is stored.
- **Flagship read tools.** `get_portfolio(slug_or_id)` always returns the strategy, structured
  policy, prompt mode, and next effective date. Managed mode also returns drifted holdings with
  entry/current prices, the full allocation history with notes, performance, and costs. Rebuilt
  mode intentionally omits prior signals, notes, performance, and costs. `get_arena_overview()`
  and `get_rebuilt_analysis()` require `direction="long"` or `"short"` and never mix directions.
  Rebuilt analysis exposes Common Policy, Tuned, and Signal Alpha views. Use `create_allocation` for
  managed portfolios and `create_signal` for rebuilt portfolios.
- **Prompt tools.** MCP can list, read, create, update, and archive active prompts. It cannot expose
  archived prompt content, immutable history, unarchive, or restore operations; those recovery
  controls remain in the browser admin.
- **Automation tools.** `get_evaluator_dashboard`, `update_evaluator_settings`,
  `configure_portfolio_evaluator`, `run_evaluations`, `cancel_evaluation_run`,
  `retry_evaluation_run`, and `list_evaluation_runs` mirror the website's evaluator controls.
- **Execution-profile tools.** `list_harnesses` exposes the code-defined harness registry;
  `list_models`, `create_model`, `update_model`, and `delete_model` manage model capabilities; Agent
  tools combine a model, supported harness, and model-valid reasoning effort into a reusable profile.
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

The evaluator is part of Portfolio Arena. Models declare their execution ID and available reasoning
efforts per supported harness. Agents select one of those valid profiles; their display names are
generated from it. A portfolio whose Agent uses Codex automatically appears in the admin
**Automation** tab, initially disabled. Rebuilt automation runs every Monday through Friday; managed
automation can run on any selected weekdays or remain manual-only. If a selected day is an NYSE
holiday, that evaluation shifts to the next trading day and is deduplicated if multiple selected days
converge on the same session. Scheduled close times honor early closes and daylight-saving changes.

The website can queue an enabled portfolio at any time. Each run captures its Agent and model IDs,
harness, harness-specific execution model ID, optional reasoning effort, timeout, and attempt limit
when it is queued. Harness defaults are used; Portfolio Arena does not configure a service tier.
Scheduled runs enter the queue at the configured offset before close; polling and concurrency may
delay their actual start. Runs queued before close remain eligible afterward, and successful
scheduled submissions use the scheduled session even if they finish after its close. Pausing stops
new claims while active work finishes. Queued work can be cancelled immediately; running work
receives a cancellation request and its Codex process is terminated. Failed runs can be retried
manually. All paths use the same server-side proposal and symbol validation and atomically create
either a managed allocation or rebuilt signal. At claim time, the worker receives a complete
execution prompt rendered from the portfolio's canonical strategy and the editable wrapper for its
mode. A liquidated managed short cannot be enabled, queued, claimed, retried, or submitted again
until its portfolio history is reset.

Codex runs with a read-only sandbox and read-only Portfolio Arena MCP tools. It authenticates through
the Codex CLI's persisted ChatGPT login, not an OpenAI API key. Runtime credentials are
deployment-only: `MASSIVE_API_KEY` is passed to both the web process for valuations and the worker
for research, while the internal worker bearer token is generated in memory at startup.

When upgrading an existing Arena database, migration `0006` intentionally aborts if historical cash
positions exist. Back up the database and resolve those rows before deploying; the migration will
not silently rewrite the experiment's history.

Migration `0007` preserves portfolios, allocations, prompts, agents, settings, evaluation history,
price data, and MCP API keys. It removes only the obsolete local-password user table and replaces it
with short-lived browser-session records; existing JWT browser sessions stop working immediately.

Migration `0015` clears cached Yahoo-originated price series once so they cannot mix with Massive
total-return data. The cache refills on the next valuation request.

Migration `0016` installs the daily signal arena. It preserves managed allocation history and all
evaluator audit records, resets only rebuilt v1 allocation history, removes stored benchmark
portfolios, and marks existing rebuilt portfolios as the founding v2 cohort.

Migration `0017` marks every existing portfolio as long, adds the required portfolio direction, and
moves each prompt's current content into immutable version 1. It also adds prompt archive state; no
prompt content is discarded.

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
export MASSIVE_API_KEY=set-locally
cd backend && ../.venv/bin/uvicorn app.main:app --reload

# Frontend dev server (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

### Tests

```sh
cd backend && ../.venv/bin/python -m pytest        # valuation engine, calendar, API
cd frontend && npm run test                        # warning-state unit tests
cd frontend && npm run check && npm run build      # svelte-check + production build
cd frontend && npm run format                      # Prettier (format:check to verify only)
```

API tests start a throwaway Postgres directly with rootless Podman, using an explicit keep-id user
mapping so the official image can initialize its data directory. Set `TEST_DATABASE_URL` to reuse
an existing database instead. Massive is stubbed in application tests; dedicated provider tests use
an in-memory HTTP transport, so nothing hits the network.

## Coolify Deployment

- Create one application from `kaufmann-dev/portfolio-arena` with Build Pack `Nixpacks` and Base
  Directory `/`.
- Attach a PostgreSQL resource, configure a public domain, and set the health-check path to
  `/api/health`.
- Add persistent storage at `/var/lib/codex`. After the first deployment, open the application's
  terminal and run `CODEX_HOME=/var/lib/codex codex login --device-auth`; the login survives
  redeployments in that volume.
- Set the required variables below. Coolify injects `PORT`; no custom start command or Dockerfile is
  needed.
- Deploy. The tracked `nixpacks.toml` builds the SPA and starts one supervisor that runs migrations,
  FastAPI, the scheduler, and the evaluator worker automatically.
- When replacing the former two-application setup, stop the old standalone evaluator before
  deploying this version so both schedulers cannot create work during the cutover.

### Environment Variables

**Required**

Web app:

| Variable                   | Purpose                                                               |
| -------------------------- | --------------------------------------------------------------------- |
| `DATABASE_URL`             | PostgreSQL connection URL                                             |
| `ARENA_PUBLIC_URL`         | Canonical externally reachable origin, with no path (**required**)    |
| `ARENA_OIDC_ISSUER_URL`    | OIDC issuer URL used for discovery (**required**)                     |
| `ARENA_OIDC_CLIENT_ID`     | Confidential OIDC client ID (**required**)                            |
| `ARENA_OIDC_CLIENT_SECRET` | Confidential OIDC client secret (**required**)                        |
| `ARENA_OIDC_STATE_SECRET`  | Random secret of at least 32 characters for OIDC state (**required**) |
| `MASSIVE_API_KEY`          | Massive credential used for web valuations and evaluator research     |

**Optional**

Web app:

| Variable                        | Default          | Purpose                                       |
| ------------------------------- | ---------------- | --------------------------------------------- |
| `ARENA_DEFAULT_COST_BPS`        | `10`             | Default cost bps for new portfolios           |
| `ARENA_DB_CONNECT_RETRIES`      | `30`             | Retries before failing startup                |
| `ARENA_DB_CONNECT_RETRY_DELAY`  | `2.0`            | Seconds between retries                       |
| `ARENA_PRICE_CACHE_TTL_SECONDS` | `3600`           | Seconds before a price refresh is due         |
| `CODEX_HOME`                    | `/var/lib/codex` | Codex authentication and generated config dir |
| `PORT`                          | `8000`           | Listen port; normally injected by Coolify     |

## Non-goals

No broker integration, OpenAI Platform API execution, mixed long/short or market-neutral books,
leverage, broker-native borrow availability, margin, borrow or financing fees, options/futures,
intraday prices, cash positions, OpenCode automation, application-managed user accounts, external
notifications, or historical backtesting.
