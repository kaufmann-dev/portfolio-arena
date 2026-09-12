# Portfolio Arena

A self-hosted web app that runs a long-term experiment: **can LLM stock-selection strategies
produce repeatable alpha in all-long or all-short portfolios?**

Portfolio Arena includes website-controlled Codex and Muse Code evaluators. One Nixpacks deployment
starts the web app, scheduler, and evaluator workers together. The admin panel defines models and their
harness-specific capabilities, combines them into reusable Agents, and controls weekdays,
concurrency, immediate runs, cancellation, retries, and history. Manual submissions and authenticated
MCP workflows remain available.

Arena versions preserve successive experiments with different models and prompts. Every version stays
visible and has its own evaluation switch; several versions can run concurrently. Each version
contains Managed and Rebuilt tracks, split into Long and Short arenas and compared with a
direction-matched SPY reference.

Managed portfolios retain holdings and notes between decisions. Rebuilt portfolios submit independent
daily signals and select their own holding horizon from H0.5 through H20 in half-session increments.
The project measures cost-free paper performance for research.

## Architecture

- **Backend:** FastAPI, SQLAlchemy 2, Alembic and PostgreSQL (`backend/`); serves the built SPA.
- **Frontend:** Svelte 5, Vite and TypeScript (`frontend/`), built to `frontend/dist/`.
- **Evaluator:** integrated Codex and Muse Code workers with database-backed settings, queue, leases
  and audit history. The production supervisor starts the web app and workers together.
- **Authentication:** OIDC Authorization Code + PKCE with opaque browser sessions. Rankings and
  portfolio details are public; `/mcp` requires an API key.
- **Prices:** Massive grouped daily responses refresh opening and closing prices in the background,
  with bounded per-ticker history repair. Both fields use the same split/dividend adjustments.
  A developing closing price is never published with a morning opening price.
- **Valuation:** NAV is never stored. Pure calculations derive it from decisions and cached prices;
  a bounded cache memoizes exact-input analytics. Opening and closing observations are separate
  UTC timestamp/phase events. API boundaries use `{timestamp, phase}` and chart points add `nav`.
- **Publication:** prices become eligible 15 minutes after their boundary. Reads perform no provider
  I/O and retain the latest complete boundary while new data is `updating`. After ten minutes of
  publication lag it is `stale`; missing required history is `unavailable`. Missing openings are
  never replaced with closing prices. Paused versions continue receiving market data.

## Experiment-integrity rules

- **Versions control evaluation independently.** New versions are empty and paused. Global,
  version and portfolio switches must all allow evaluation. Pausing a version cancels queued work;
  already-running attempts may finish without retries. Portfolio schedules remain intact. Only
  empty versions may be deleted; portfolio IDs and URLs survive a move between versions.
- **Execution timing belongs to the portfolio.** Choose before-open/opening-price or
  before-close/closing-price evaluation. Timing locks permanently at its first decision, even after
  resetting history. Create another portfolio to change it. Separate opening and closing offsets
  each accept 15–240 minutes and default to 90 minutes on a fresh installation.
- **Manual submissions never backdate.** They target the first future matching boundary. Scheduled
  evaluations retain their scheduled session and boundary even if they finish late. NYSE holidays,
  daylight-saving changes and early closes are respected. Pending decisions can be corrected;
  decisions lock at their effective boundary. Managed allocation notes remain editable afterward.
- **Portfolio resets clear all history.** Reset permanently removes all allocations, signals and
  evaluation runs, including reports, and stops active attempts. Identity, agent assignment,
  schedules and timing lock remain. A former agent can be deleted once no portfolios or remaining
  runs reference it. Track/direction changes require empty history and no live evaluations.
  Deleting a portfolio also removes its schedule.
- **Individual evaluations can be removed.** Delete an evaluation from Automation, or an allocation
  or signal from Portfolio state, including locked decisions. The decision and its associated run
  are deleted together; performance is recalculated from the remaining history. Later evaluations
  remain intact. Deleted running evaluations cannot submit results afterward.
- **Prompts are shared live strategies.** A prompt supports Managed/Rebuilt/Both and Long/Short/Both
  with text for each supported combination. Edits apply to every referencing version and append
  an immutable prompt revision. Workers record the revision used when claiming a run. Restoring
  a revision creates a new edit; history and restore remain browser-admin-only. A prompt cannot be
  deleted while a portfolio or recorded evaluation revision references it. Agents and models also
  retain deletion protection for portfolio/run references.
- **Research context depends on track.** Managed evaluations receive holdings, notes, allocation
  history and performance. Rebuilt evaluations receive no prior portfolio state. Strategy and
  direction instructions are inserted into the editable track-specific wrapper in Admin → Settings.
- **Books have one direction.** Selected weights are positive and total `min(100, count × maximum weight)`.
  Server-enforced sizing defaults are 10–25% for Managed and 10–100% for Rebuilt, with at most four
  decimal places. The remainder follows the direction-matched SPY reference, outside ticker limits.
  Zero qualifying selections is a successful, explained abstention with 100% reference exposure.
  Partial allocations also require an explanatory note. Managed decisions replace prior holdings at
  the execution boundary; a technical failure makes no decision and existing holdings continue.
  Limits affect future submissions; no minimum ticker count forces unsuitable selections.
- **Benchmarks are synthetic.** Long SPY is buy-and-hold; short SPY resets to −1× at each close and
  is also marked at the open. Merely publishing another price does not trigger a portfolio trade.
- **Rebuilt comparison is portfolio-tuned.** Forty horizons H0.5, H1, H1.5 … H20 are tested at 100%
  exposure. One half-step advances to the next open/close: morning H0.5 expires that close, morning
  H1 at the next open; evening H0.5 at the next open and evening H1 at the next close. Each daily
  cohort gets `1 / ceil(H)` of the book; unused capacity stays in direction-matched SPY.
  Each direct signal return also includes its reference remainder. Recorded abstentions mature as
  zero-alpha observations across all horizons, without closing older active cohorts. Participation
  counts decisions whose execution boundary has passed: selections versus abstentions, excluding
  technical failures. Reference holdings are displayed separately from explicitly selected SPY.
  “Optimize horizon by” selects each portfolio’s best eligible horizon using Signal α/day (default),
  adjusted lower 95%, information ratio, Sharpe, Portfolio α/day, or hit rate, breaking ties toward the
  shorter horizon. Signal α/day averages completed baskets’ benchmark-relative growth normalized to
  one session; Portfolio α/day averages the simulated portfolio’s full-session return minus SPY return.
  The signal objective uses `signal_mean_daily_alpha`; portfolio alpha uses `mean_daily_alpha`.
  Rank remains based on the adjusted lower confidence bound of portfolio alpha.
  Column sorting only changes row order. The objective is preserved in the URL, comparisons, and
  portfolio details. The Signal Alpha matrix shows all forty horizons alongside rankings
  and in portfolio details; the table’s Signal α/day matches the selected horizon’s matrix cell.
  Cell backgrounds show alpha sign (red negative, green positive), with
  intensity scaled by absolute signal alpha across all displayed rows and horizons; zero is neutral.
  About’s Metrics explained section covers the calculations and worked examples.
  Evidence remains pending until minimum sample requirements are met.
- **Statistics use full sessions.** Daily observations end at the latest published phase, using
  open-to-open or close-to-close returns and 252-session annualization. An incomplete initial
  interval contributes to total return but not inference. HAC lag is `ceil(H) - 1` and the
  Bonferroni search family contains forty horizons.
- **Short loss is capped at equity.** Fixed-share shorts are fully collateralized between
  rebalances. Zero NAV causes absorbing liquidation. Managed evaluations are blocked until reset;
  rebuilt signals may continue to support future policy measurements.

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

The streamable-HTTP [MCP](https://modelcontextprotocol.io) endpoint is `/mcp`. Every request requires
`Authorization: Bearer <key>` or `X-API-Key`. Create/revoke keys in Admin → API Keys; plaintext is
shown once and only SHA-256 hashes are stored.

- `list_versions` and version create/update/delete tools manage experiment groups.
  `list_portfolios(version_id)` returns inventory, assignments, timing and editing blockers.
- `get_portfolio(slug_or_id)` supplies the applicable strategy and research context, sizing policy,
  execution boundary and next effective boundary. `get_effective_date(portfolio_id)` previews
  manual timing. `create_allocation` submits Managed decisions; `create_signal` submits Rebuilt.
- `get_arena_overview(direction, version_id)` and `get_rebuilt_analysis(direction, version_id)`
  return scoped results. Both accept optional `objective`: `ci_lower` (default), `information_ratio`,
  `sharpe`, `mean_daily_alpha`, or `hit_rate`. Rebuilt analysis includes the forty-column signal matrix.
- Model, agent, prompt and portfolio tools expose administration with reference-aware deletion.
  Prompt revision history/restore and API-key management remain browser-only.
- Settings tools manage allocation policies, wrappers and direction instructions. Evaluator tools
  mirror dashboard, scheduling, queue, cancellation and retry controls. Inventory, dashboard and
  run history support optional version filters.
- `delete_evaluation_run(run_id)` removes one run and its result. `delete_allocation(allocation_id)`
  and `delete_signal(signal_id)` also remove the associated run, including for locked decisions.
  `reset_portfolio(portfolio_id)` clears all decisions and runs for that portfolio.
- Internal worker credentials can access only the small read-only portfolio research tool surface;
  queue claims and atomic submissions use private deployment routes.

Connect a client with `claude mcp add --transport http arena https://<host>/mcp
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
generated from it. A portfolio whose Agent uses Codex or Muse Code automatically appears in the admin
**Automation** tab, initially disabled. Rebuilt automation runs every Monday through Friday; managed
automation can run on any selected weekdays or remain manual-only. If a selected day is an NYSE
holiday, that evaluation shifts to the next trading day and is deduplicated if multiple selected days
converge on the same session. Opening and closing times honor early closes and daylight-saving changes.

The website can queue a portfolio when global, version and portfolio evaluation are enabled. Each run captures its Agent and model IDs,
harness, harness-specific execution model ID, optional reasoning effort, timeout, and attempt limit
when it is queued. Harness defaults are used; Portfolio Arena does not configure a service tier.
Scheduled runs enter the queue at the configured offset before their opening or closing boundary;
polling and concurrency may delay their actual start. Successful scheduled submissions retain the
scheduled boundary even when they finish afterward. Pausing stops
new claims while active work finishes. Queued work can be cancelled immediately; running work
receives a cancellation request and its harness process and MCP children are terminated. Failed runs can be retried
manually. All paths use the same server-side proposal and symbol validation and atomically create
either a managed allocation or rebuilt signal. At claim time, the worker receives a complete
execution prompt rendered from the portfolio's selected mode-and-direction-specific strategy text
and the editable wrapper for its mode. A liquidated managed short cannot be enabled, queued, claimed,
retried, or submitted again until its portfolio history is reset.

Codex runs with a read-only sandbox and read-only Portfolio Arena MCP tools. It authenticates through
the Codex CLI's persisted ChatGPT login, not an OpenAI API key. Muse Code runs via `muse exec`
with web tools enabled and shell/file writes disabled. It uses the same read-only Arena MCP token
and Massive MCP server. Muse returns JSON in its root terminal event; the worker validates the
structured response before submission. Both harnesses return `proposal` for full or partial selections,
`abstained` for completed research with no qualifying securities, or `blocked` only when portfolio
context or required research is unavailable. The latter requires `blocked_reason` of
`portfolio_unavailable` or `research_unavailable`. Partial allocations and abstentions require a note
and report. They save normal decisions and finish successfully without retries. Actual execution,
research-access, and response-validation failures remain retryable. Successful recovery clears the
terminal error; blocked reports are retained. Existing historical failures are left unchanged.

Muse uses its persisted Meta account login and subscription. `META_API_KEY` is an optional alternative
and takes precedence when set, matching the Muse CLI. After authentication, each Muse worker process
imports the visible models and explicit reasoning variants from Meta's authenticated Muse Code
catalog once through the CLI, which handles account-token exchange and renewal. Model discovery
does not start an agent turn. Existing model capabilities and admin edits are preserved. No models or
reasoning tiers are guessed when the catalog is unavailable. Restart the worker to discover newly
available models. Authentication and runtime health are shown separately per harness. The concurrency
setting applies separately to each harness across all of its workers: a limit of 8 permits up to 8
Codex and 8 Muse Code evaluations at once. Runs awaiting cancellation count against their harness's
limit until they stop. An unconfigured Muse login does not stop Codex evaluations.

Runtime credentials are deployment-only: `MASSIVE_API_KEY` is passed to both the web process for
valuations and the worker for research, while the internal worker bearer token is generated in
memory at startup.

### Upgrading to versioned experiments

Migration `0027` requires a database backup and a deployment cutover with old evaluator workers
stopped or drained. It preserves ordinary portfolio IDs, decisions, schedules, prompts and evaluation
history. Portfolios using model slug `gpt-5-6-sol` enter paused **v1**; newer portfolios enter enabled
**v2**. All surviving previously archived records become usable. It permanently removes Meta
portfolios, their decisions/runs, synthesis prompts, families and batches; ordinary source runs remain.
It removes archive/cost/common-policy fields, backfills closing execution and permanent timing locks,
and copies the configured closing queue offset to the new opening offset. Historical prompt revision
references remain unknown (`null`); new claims record them. Duplicate agent execution profiles cause
an explicit migration failure rather than silently merging audit identities.

Cached prices are cleared once so the refresher rebuilds both opening and closing history. Results
are recomputed without transaction costs. The migration is destructive and cannot be downgraded;
rollback requires restoring the backup. Historical migrations remain in the repository for upgrading
older installations.

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
- Add persistent storage at `/var/lib/muse`. Run `XDG_CONFIG_HOME=/var/lib/muse muse login` in the
  application terminal, or set `META_API_KEY` for the worker. Muse models and their available
  reasoning efforts appear automatically after authentication and successful catalog import.
- Set the required variables below. Coolify injects `PORT`; no custom start command or Dockerfile is
  needed.
- Deploy. The tracked `nixpacks.toml` builds the SPA and starts one supervisor that runs migrations,
  FastAPI, the scheduler, and the evaluator worker automatically.
- Each deployment or container restart runs `npm run update:harnesses` to install the latest stable
  Codex and Muse Code CLIs before launching the supervisor, including when the image build was cached.
  Startup requires npm registry and Meta download access and stops if either update fails. Redeploy
  or restart to pick up subsequent releases.
  The image includes `bubblewrap` for Linux sandboxing and `curl` for the Muse installer.
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

| Variable                        | Default          | Purpose                                                            |
| ------------------------------- | ---------------- | ------------------------------------------------------------------ |
| `ARENA_DB_CONNECT_RETRIES`      | `30`             | Retries before failing startup                                     |
| `ARENA_DB_CONNECT_RETRY_DELAY`  | `2.0`            | Seconds between retries                                            |
| `ARENA_PRICE_CACHE_TTL_SECONDS` | `3600`           | Seconds before a price refresh is due                              |
| `CODEX_HOME`                    | `/var/lib/codex` | Codex authentication and generated config dir                      |
| `MUSE_CONFIG_HOME`              | `/var/lib/muse`  | Muse XDG config root; login and generated config are under `muse/` |
| `META_API_KEY`                  | unset            | Optional Muse credential instead of a persisted Meta login         |
| `PORT`                          | `8000`           | Listen port; normally injected by Coolify                          |

## Non-goals

No broker integration, OpenAI Platform API execution, mixed long/short or market-neutral books,
leverage, broker-native borrow availability, margin, borrow or financing fees, options/futures,
intraday quotes beyond opening/closing boundaries, cash positions, OpenCode automation,
application-managed user accounts, external notifications, or historical backtesting.
