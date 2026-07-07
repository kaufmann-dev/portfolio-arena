# Portfolio Arena — Implementation Plan

## What this is

A self-hosted web app that runs a long-term experiment: **can LLMs pick stock portfolios that beat SPY?**

Every week the operator (David) manually prompts various AI agents (Claude, Codex, Gemini, …) with portfolio-management prompts, then pastes each agent's response into this app. The app parses the proposed allocation, simulates it as a paper portfolio using Yahoo Finance adjusted closes, and tracks it live against SPY on a public leaderboard. It is an **arena**: contestants are (agent × prompt) pairs, and the app's job is honest, deterministic measurement — not trading, not automation, not advice.

Everything model-facing happens outside this app. The app never calls an LLM. Data enters exclusively through manual admin entry.

## Core design decisions (settled, do not relitigate)

1. **Lifecycle: persistent portfolios with rebalances.** A contestant is a long-lived portfolio. Week 1 creates its initial allocation; later weeks the operator pastes rebalance decisions into the same portfolio. A one-shot portfolio is simply one that never receives a second allocation. This measures compounded management skill over time.
2. **Access: public read, admin write.** Leaderboard and all portfolio pages are viewable without login. All writes require a single admin login (JWT, same pattern as market-deck, no demo user).
3. **Costs: flat bps on turnover.** Every trade pays a configurable cost (default 10 bps of traded notional). Frictionless tracking would systematically flatter high-turnover AI portfolios against buy-and-hold SPY.
4. **Stack: market-deck's proven architecture.** FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL backend; Svelte 5 + Vite + TypeScript SPA built to `frontend/dist/` and served by the backend with SPA fallback; single Nixpacks container deployed via Coolify with a separate Coolify PostgreSQL resource. Not Streamlit (wrong tool for a public product with validated multi-step entry flows). Not SvelteKit (SSR buys nothing; the backend must be Python to reuse market-deck's Yahoo service).
5. **No stored NAVs, no background jobs.** Portfolio value series are recomputed deterministically on request from immutable allocations + cached adjusted closes. Adjusted closes change retroactively (dividends/splits), so recomputation is *more* correct than snapshotting. A Postgres price cache (per ticker, TTL ~1h during market hours) keeps it fast.
6. **Everything is total-return.** Use Yahoo **adjusted closes** for positions and for SPY, so dividends are included on both sides of the comparison.

## Experiment-integrity rules (the heart of the app)

These rules exist so results can be trusted. They are enforced in code, not by convention.

- **No backdating / no lookahead.** An allocation entered at time T takes effect at the **first market close strictly after T** (`effective_close`). Entered Saturday → effective Monday's close. Prices before entry can never be claimed.
- **Immutability after lock.** An allocation is editable/deletable only until its effective close has occurred (typo-correction window). Once the market close locks its entry prices, it is permanently immutable. No edits, no deletes, ever.
- **Prompts are immutable.** Editing prompt text is impossible; a changed prompt is a *new* prompt row, and comparisons against the old one remain intact. A portfolio references the exact prompt row it uses.
- **Raw provenance.** Every allocation stores the complete raw model response verbatim, alongside the parsed positions.
- **Benchmarks are computed with the identical engine.** SPY (primary) and RSP (equal-weight S&P, secondary) are tracked as system portfolios: 100% single-ETF allocations created at the same inception dates, valued by the same code path — with zero cost, since holding SPY really is near-free.
- **Honesty labels.** The leaderboard shows age since inception and marks portfolios younger than 6 months as "too early to judge". Sample size is displayed, never hidden.

## Domain model

```
agents        one row per model/harness identity
  id, slug, name ("Claude Opus 4.8 (Claude Code)"), notes, created_at

prompts       immutable prompt texts
  id, slug, name ("weekly-manager-v1"), text (full prompt), notes, created_at

portfolios    the contestants
  id, slug, name, agent_id → agents, prompt_id → prompts,
  cost_bps (int, default from settings, frozen at creation),
  status (active | archived), is_benchmark (bool), created_at

allocations   one row per weekly decision (initial or rebalance), append-only
  id, portfolio_id → portfolios, entered_at (UTC, server-set),
  effective_date (date, computed: first US trading day whose close is after entered_at),
  raw_response (text, verbatim), note (optional operator comment), created_at
  locked = effective_date's close has passed (derived, not a column)

positions     parsed holdings of an allocation
  id, allocation_id → allocations, ticker (Yahoo symbol, uppercase), weight_pct (numeric(8,4))
  Cash is represented as ticker 'CASH' (weight rows must sum to 100 ± 0.05).

price_cache   daily adjusted closes per ticker (port of market-deck's price cache)
  ticker, as_of, series JSONB [{date, close}], fetched_at

users / settings   admin auth + app settings (port from market-deck, minus demo role)
```

Seeding: create benchmark portfolios `SPY Buy & Hold` and `RSP Buy & Hold` (`is_benchmark = true`, `cost_bps = 0`) plus a system agent "Benchmark" and prompt "buy-and-hold". Benchmark allocations are auto-created lazily: whenever the earliest real portfolio inception moves earlier, ensure benchmarks have an allocation effective from that date.

## Valuation engine (backend/app/services/valuation.py)

Pure functions, fully unit-tested; this is the correctness core of the whole app.

- **Initial allocation:** at the effective close, convert weights to fractional shares using that day's adjusted close: `shares[t] = NAV₀ · w[t] / price[t]`, with `NAV₀ = 100` (all series are base-100). Cash weight earns 0%. Charge entry cost: `NAV₀ · (1 − cash_weight) · bps/10⁴` deducted from cash proportionally.
- **Daily NAV:** `NAV(d) = cash + Σ shares[t] · adj_close[t, d]`. Weights drift; drift is real and intended.
- **Rebalance:** at the new allocation's effective close, compute drifted weights `w_drift`, target weights `w_new`, turnover `= ½ Σ_t |w_new[t] − w_drift[t]|` (t includes CASH), cost `= NAV · 2 · turnover · bps/10⁴` (both sides pay), then reset shares from `w_new` on post-cost NAV.
- **Missing prices:** non-trading days are skipped (calendar = days SPY has a close). A ticker missing a price on a trading day carries its last known price and the portfolio gets a `stale_data` flag listing the tickers and days. A ticker that stops returning data entirely (delisting) stays frozen at last price and surfaces a prominent warning; the operator resolves it manually with a corrective rebalance. No silent guessing.
- **Metrics** (computed from the base-100 series): inception-to-date return, return vs SPY over the identical window, annualized volatility, max drawdown, Sharpe (rf = 0, labeled as such), cumulative cost drag, cumulative turnover, age in days. Also 1M/3M/6M/1Y trailing returns when the portfolio is old enough.

Determinism requirement: same allocations + same price series ⇒ identical output. No wall-clock reads inside the engine (the API passes "as of" dates in).

## Yahoo price service

Port `backend/app/services/yahoo.py` from `~/Projects/market-deck` (direct chart-endpoint fetching with `httpx` in a thread pool — yfinance was dropped there for being slow; see market-deck's `docs/bugs/slow-global-ticker-loading.md`). Strip everything except: chart fetching (adjusted closes, `range` computed from earliest needed date, `interval=1d`), symbol search/validation, the unresolved-symbol cooldown, and the Postgres price cache with TTL. No fundamentals, no news, no crumb flow — this app only needs closes and symbol validation.

## Response parser (backend/app/services/parser.py)

Input: pasted raw model response. Output: proposed positions + diagnostics. Never saves anything by itself — parsing always flows into a human-confirmed preview.

- Strategy: scan from the **last line upward** for the first line that parses as an allocation list (the standard prompt format ends with "Final line with holdings and weights only").
- Tolerant grammar: `TICKER weight` pairs separated by commas/semicolons/pipes/newlines; weight as `12.5%`, `12.5`, or `0.125` (auto-detect scale: if the sum is ≈ 1.0, multiply by 100); `CASH`/`Cash` recognized case-insensitively; tickers uppercased; `BRK.B`-style dots preserved (Yahoo wants `BRK-B` — apply known symbol normalizations, and validation catches the rest).
- Validation (blocking): weights sum to 100 ± 0.5 (preview shows exact sum; admin can accept auto-normalization to exactly 100), all weights > 0 (long-only), no duplicate tickers, every non-CASH ticker resolves via Yahoo symbol lookup.
- The preview screen shows the parsed table, validation results, and per-ticker resolved names; the admin edits inline if the parser got something wrong, then confirms to save.

## API (FastAPI, /api prefix)

Public (no auth, rate-limited in-memory like market-deck):
- `GET /api/leaderboard` — all portfolios with metrics, benchmark rows included, plus flags (`too_early`, `stale_data`)
- `GET /api/portfolios/{slug}` — detail: metadata, agent, prompt, metrics, base-100 NAV series, SPY series over the same window, current drifted weights, allocation history (dates, turnover, cost — raw text included)
- `GET /api/prompts` / `GET /api/prompts/{slug}` — prompt text + portfolios using it
- `GET /api/agents` — agents + their portfolios
- `GET /api/compare?slugs=a,b,c` — overlaid base-100 series for the chart

Admin (JWT bearer, port market-deck auth minus demo):
- `POST /api/auth/login`, `GET /api/auth/me`, `PUT /api/auth/password`
- `POST /api/agents`, `PATCH /api/agents/{id}` (name/notes only)
- `POST /api/prompts` (create only — no update route exists)
- `POST /api/portfolios` (agent + prompt + name; first allocation attached in the same flow)
- `POST /api/parse` — raw text in, parsed preview out (stateless)
- `POST /api/portfolios/{id}/allocations` — confirmed positions + raw text
- `PUT /api/allocations/{id}` / `DELETE /api/allocations/{id}` — **403 once locked** (effective close passed)
- `PATCH /api/portfolios/{id}` — archive/unarchive, rename
- `DELETE /api/prices/cache`

## Frontend (Svelte 5 + Vite + TS, SPA)

Pages:
1. **Leaderboard `/`** — the product. Table: rank, portfolio, agent, prompt, inception, ITD return, **vs SPY** (the headline column, sorted by default), max DD, Sharpe, turnover, sparkline. Benchmark rows pinned and visually distinct. Filter by agent/prompt; archived hidden behind a toggle. "Too early" badge under 6 months. Checkbox-select rows → overlay comparison chart (`/api/compare`).
2. **Portfolio detail `/p/{slug}`** — base-100 NAV vs SPY chart with allocation-date markers; metrics row; current drifted holdings table (weight, drift since last rebalance); allocation timeline, each entry expandable to full raw model response; stale-data/delisting warnings.
3. **Prompt detail `/prompt/{slug}`** — full immutable text, portfolios using it with mini-metrics (does this prompt work across models?).
4. **Agent detail `/agent/{slug}`** — portfolios by this model (does this model work across prompts?).
5. **Admin `/admin`** — login; "New allocation" flow: pick portfolio → paste raw response → parsed preview with validation + inline edit → confirm; "New portfolio" wizard (pick/create agent, pick/create prompt, then same paste flow); agents/prompts management; settings (default cost bps).

Charting: lightweight SVG/canvas line charts, self-written or a tiny dependency — match whatever market-deck's frontend uses; no heavyweight chart library. Long tables and charts scroll within their own containers. Dark/light theme like market-deck.

## Deployment (Coolify + Nixpacks)

Copy market-deck's pattern verbatim: `nixpacks.toml` (pip install + `cd frontend && npm ci && npm run build`; start = `python -m app.migrate && uvicorn app.main:app`), `.python-version`, `.nvmrc`, `requirements.txt`. Env vars: `DATABASE_URL`, `ARENA_JWT_SECRET`, `ARENA_ADMIN_EMAIL`, `ARENA_ADMIN_PASSWORD`, optional cache TTLs and `ARENA_DEFAULT_COST_BPS` (default 10). Health check: a public endpoint that touches the DB (e.g. `GET /api/leaderboard`). Single instance assumed (in-memory rate limits), same as market-deck.

## Testing

- **Valuation engine (highest priority):** pytest with synthetic price fixtures — initial allocation math, drift, rebalance turnover + two-sided costs, cash handling, next-close effectiveness (weekend/holiday entry), missing-price carry-forward and flags, base-100 SPY comparison over identical windows, determinism.
- **Parser:** fixture files of real pasted responses (Claude/Codex/Gemini formats, percent vs decimal weights, final-line variants, garbage input) → expected positions or expected validation errors.
- **API:** auth boundaries (public reads open, writes 401/403), allocation lock enforcement (edit after effective close → 403), prompt immutability (no update route).
- **Frontend:** `svelte-check` + production build.

## Build order

1. **Scaffold** — repo layout mirroring market-deck (`backend/` package, `frontend/` Vite app, Alembic baseline migration with full schema, auth port, settings).
2. **Yahoo service + price cache** — port and strip from market-deck; symbol validation endpoint.
3. **Valuation engine + tests** — pure functions, fixtures first. *Nothing else matters if this is wrong.*
4. **Parser + tests.**
5. **API routes** — public reads, admin writes, lock enforcement, benchmark auto-seeding.
6. **Frontend** — leaderboard → portfolio detail → admin entry flow → prompt/agent pages → compare chart.
7. **Deploy** — nixpacks/Coolify files, README (setup, env vars, integrity rules), first deploy, seed benchmarks, enter the first real portfolios.

## Explicit non-goals (v1)

No LLM API calls, no automation of weekly runs, no IBKR or any broker, no intraday prices, no shorts/leverage/options (parser rejects negative weights), no multi-user accounts, no email/notifications, no historical backtesting, no statistical-significance machinery beyond the age badge (revisit after ~1 year of data), no non-US listings beyond what Yahoo symbols support.
