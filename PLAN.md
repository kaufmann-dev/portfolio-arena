# Portfolio Arena — Implementation Plan

## What this is

A self-hosted web app that runs a long-term experiment: **can LLMs pick portfolios that beat SPY?**

Every week the operator (David) manually prompts various AI agents (Claude, Codex, Gemini, …) with portfolio-management prompts, then enters each agent's proposed allocation into this app by hand. The app simulates it as a paper portfolio using Yahoo Finance data and tracks it live against SPY on a public leaderboard. It is an **arena**: contestants are (agent × prompt) pairs, and the app's job is honest, deterministic measurement — not trading, not automation, not advice.

Everything model-facing happens outside this app. The app never calls an LLM. Data enters exclusively through manual admin entry.

## Core design decisions (settled, do not relitigate)

1. **Lifecycle: persistent portfolios with rebalances.** A contestant is a long-lived portfolio. Week 1 creates its initial allocation; later weeks the operator enters rebalance decisions into the same portfolio. A one-shot portfolio is simply one that never receives a second allocation. This measures compounded management skill over time.
2. **Access: public read, admin write.** Leaderboard and all portfolio pages are viewable without login. All writes require a single admin login (JWT, same pattern as market-deck, no demo user).
3. **Costs: flat bps on turnover.** Every trade pays a configurable cost (default 10 bps of traded notional). Frictionless tracking would systematically flatter high-turnover AI portfolios against buy-and-hold SPY.
4. **Stack: market-deck's proven architecture.** FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL backend; Svelte 5 + Vite + TypeScript SPA built to `frontend/dist/` and served by the backend with SPA fallback; single Nixpacks container deployed via Coolify with a separate Coolify PostgreSQL resource. Not Streamlit (wrong tool for a public product with validated entry flows). Not SvelteKit (SSR buys nothing; the backend must be Python to reuse market-deck's Yahoo service).
5. **No stored NAVs, no background jobs.** Portfolio value series are recomputed deterministically on request from immutable allocations + cached price/FX series. Adjusted closes change retroactively (dividends/splits), so recomputation is *more* correct than snapshotting. A Postgres price cache (per ticker, TTL ~1h during market hours) keeps it fast.
6. **Base currency is USD; comparisons are total-return where possible.** Equities/ETFs use Yahoo **adjusted closes** (dividends included) for positions and for SPY.
7. **Instrument scope: long/short equities & ETFs, plus multi-currency cash.** Index, leveraged, inverse, commodity, and bond exposure are all expressed through investable ETFs (SPY, SSO, SH, GLD, TLT, …), which are ordinary equity positions. Raw indices (`^GSPC`), FX pairs (`=X`), and futures (`=F`) are rejected at validation — futures via Yahoo continuous contracts have roll artifacts that would corrupt the measurement, and ETFs cover their use cases. No options in v1. Manual entry only — no response parsing.

## Experiment-integrity rules (the heart of the app)

These rules exist so results can be trusted. They are enforced in code, not by convention.

- **No backdating / no lookahead.** An allocation entered at time T takes effect at the **first market close strictly after T** (`effective_close`). Entered Saturday → effective Monday's close. Prices before entry can never be claimed.
- **Immutability after lock.** An allocation is editable/deletable only until its effective close has occurred (typo-correction window). Once the market close locks its entry prices, it is permanently immutable. No edits, no deletes, ever.
- **Prompts are immutable.** Editing prompt text is impossible; a changed prompt is a *new* prompt row, and comparisons against the old one remain intact. A portfolio references the exact prompt row it uses.
- **Provenance.** Every allocation can store the raw model response verbatim (optional textarea) alongside the entered positions.
- **Benchmarks are computed with the identical engine.** SPY (primary) and RSP (equal-weight S&P, secondary) are tracked as system portfolios: 100% single-ETF allocations created at the same inception dates, valued by the same code path — with zero cost, since holding SPY really is near-free.
- **Blow-ups are permanent.** Shorts make losses beyond −100% possible. If NAV closes at ≤ 0 on any day, the portfolio is frozen at −100% from that date and shown as **BUSTED** on the leaderboard forever. An arena needs visible corpses.
- **Honesty labels.** The leaderboard shows age since inception and marks portfolios younger than 6 months as "too early to judge". Sample size is displayed, never hidden.

## Instruments

Instrument type is **derived from Yahoo symbol syntax** — no dropdown needed:

| Syntax                | Type     | Return basis |
| --------------------- | -------- | ------------ |
| `AAPL`, `SPY`, `BRK-B`| equity   | adjusted close (total return) |
| `CASH:USD`, `CASH:EUR`| cash     | spot FX vs USD (via `EURUSD=X` etc.), 0% interest |

Rejected at validation, each with a hint: raw index symbols (`^GSPC` → use SPY/QQQ/IWM), FX pairs (`=X` → use `CASH:CCY`), futures (`=F` → use ETF equivalents such as SSO for leveraged index, GLD for gold, TLT for duration; Yahoo continuous contracts have roll artifacts that would corrupt long-horizon measurement).

- **Weight conventions.** Weights are % of NAV and must sum to **exactly 100**, with shorts as negative weights — e.g. a 130/30 book is +130 long, −30 short, 0 cash; short proceeds implicitly fund the longs. This is a margin account's economics: NAV is your equity, positions are financed against it.
- **Gross exposure cap.** `Σ|non-cash weights|` ≤ configurable limit (default 200%, the Reg-T-style leverage a real margin account allows). Validation rejects allocations above it.
- **Shorts** are allowed on equities and ETFs. `CASH:*` weights must be ≥ 0.
- **Multi-currency cash**: the weight is set in USD terms at the effective close, converted to a fixed foreign-currency amount at that day's spot rate, then floats with FX until the next rebalance. FX rates are Yahoo tickers (`EURUSD=X`) and flow through the same price cache.
- **Known simplifications (documented in README and on the About page):** no borrow fees or short rebate, no interest on cash in any currency, no margin interest on implicit borrow, no forced liquidation / margin calls before the blow-up rule. These mostly flatter the contestants slightly; the benchmark is unaffected.

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
  raw_response (text, optional, verbatim), note (optional), created_at
  locked = effective_date's close has passed (derived, not a column)

positions     entered holdings of an allocation
  id, allocation_id → allocations, symbol (Yahoo symbol or CASH:CCY, uppercase),
  instrument (equity | cash — derived from symbol, stored),
  weight_pct (numeric(9,4), negative = short)

price_cache   daily series per Yahoo symbol — equities (adjusted close)
              and FX pairs share this table
  symbol, series JSONB [{date, close}], fetched_at

users / settings   admin auth + app settings (port from market-deck, minus demo role)
```

Seeding: create benchmark portfolios `SPY Buy & Hold` and `RSP Buy & Hold` (`is_benchmark = true`, `cost_bps = 0`) plus a system agent "Benchmark" and prompt "buy-and-hold". Benchmark allocations are auto-created lazily: whenever the earliest real portfolio inception moves earlier, ensure benchmarks have an allocation effective from that date.

## Valuation engine (backend/app/services/valuation.py)

Pure functions, fully unit-tested; this is the correctness core of the whole app.

- **State per position:** equities hold fractional shares (negative for shorts); cash positions hold fixed foreign-currency amounts.
- **Initial allocation** at the effective close, `NAV₀ = 100` (all series base-100): equities `shares = NAV₀ · w/100 / price` (negative for shorts, whose proceeds implicitly fund the rest of the book); `CASH:CCY` converts its USD value at spot. Entry cost `= NAV₀ · Σ|non-cash w|/100 · bps/10⁴`, deducted from USD cash (may go negative as an implicit 0%-interest borrow).
- **Daily NAV:** `NAV(d) = Σ shares·price(d) + Σ cash_ccy·fx(d)`. Weights drift; drift is real and intended. Short losses are unbounded, which is what the blow-up rule is for.
- **Rebalance** at the new allocation's effective close: compute drifted weights `w_drift`, turnover `= ½ Σ|w_new − w_drift|` over non-cash positions, cost `= NAV · 2 · turnover · bps/10⁴`, then reset all state from `w_new` on post-cost NAV.
- **Blow-up:** first close with `NAV ≤ 0` freezes the series at 0 (−100%) permanently; portfolio flagged `busted`.
- **Missing prices:** calendar = days SPY has a close. A symbol missing a price on a trading day carries its last known value and the portfolio gets a `stale_data` flag listing symbols and days. A symbol that stops returning data entirely (delisting) stays frozen at last price with a prominent warning; the operator resolves it via a corrective rebalance. No silent guessing.
- **Metrics** (from the base-100 series): inception-to-date return, return vs SPY over the identical window, annualized volatility, max drawdown, Sharpe (rf = 0, labeled as such), cumulative cost drag, cumulative turnover, current gross/net exposure, age in days; 1M/3M/6M/1Y trailing returns when old enough.

Determinism requirement: same allocations + same cached series ⇒ identical output. No wall-clock reads inside the engine (the API passes "as of" dates in).

## Yahoo price service

Port `backend/app/services/yahoo.py` from `~/Projects/market-deck` (direct chart-endpoint fetching with `httpx` in a thread pool — yfinance was dropped there for being slow; see market-deck's `docs/bugs/slow-global-ticker-loading.md`). Strip everything except: chart fetching (`interval=1d`, adjusted closes for equities, plain closes for `=X` FX symbols, `range` computed from earliest needed date), symbol search/validation, the unresolved-symbol cooldown, and the Postgres price cache with TTL. No fundamentals, no news, no crumb flow.

## Allocation entry (manual, no parsing)

Admin form: a table of rows — symbol + weight — with add/remove/reorder. On symbol blur, the backend validates it (Yahoo lookup) and shows resolved name + derived instrument type inline. Submit-time validation (blocking):

- weights sum to exactly 100 (UI shows a live running sum; one-click normalize)
- `CASH:*` weights ≥ 0; currency code must have a Yahoo FX pair (or be USD)
- no duplicate symbols; every symbol resolves; `^`, `=X`, and `=F` symbols rejected with hints (use ETFs / `CASH:CCY`)
- gross exposure within the configured cap

Optional fields: raw model response (provenance textarea), note. A rebalance form pre-fills the previous allocation's *target* weights as the starting point.

## API (FastAPI, /api prefix)

Public (no auth, rate-limited in-memory like market-deck):
- `GET /api/leaderboard` — all portfolios with metrics, benchmark rows included, plus flags (`too_early`, `stale_data`, `busted`)
- `GET /api/portfolios/{slug}` — detail: metadata, agent, prompt, metrics, base-100 NAV series, SPY series over the same window, current drifted weights + exposures, allocation history (dates, turnover, cost, raw text)
- `GET /api/prompts` / `GET /api/prompts/{slug}` — prompt text + portfolios using it
- `GET /api/agents` — agents + their portfolios
- `GET /api/compare?slugs=a,b,c` — overlaid base-100 series for the chart

Admin (JWT bearer, port market-deck auth minus demo):
- `POST /api/auth/login`, `GET /api/auth/me`, `PUT /api/auth/password`
- `POST /api/agents`, `PATCH /api/agents/{id}` (name/notes only)
- `POST /api/prompts` (create only — no update route exists)
- `POST /api/portfolios` (agent + prompt + name; first allocation attached in the same flow)
- `GET /api/symbols/{symbol}` — validation/resolution for the entry form
- `POST /api/portfolios/{id}/allocations` — positions + optional raw text
- `PUT /api/allocations/{id}` / `DELETE /api/allocations/{id}` — **403 once locked** (effective close passed)
- `PATCH /api/portfolios/{id}` — archive/unarchive, rename
- `DELETE /api/prices/cache`

## Frontend (Svelte 5 + Vite + TS, SPA)

Pages:
1. **Leaderboard `/`** — the product. Table: rank, portfolio, agent, prompt, inception, ITD return, **vs SPY** (headline column, default sort), max DD, Sharpe, gross exposure, turnover, sparkline. Benchmark rows pinned and visually distinct; BUSTED rows styled as such and kept visible. Filter by agent/prompt; archived behind a toggle. "Too early" badge under 6 months. Checkbox-select rows → overlay comparison chart (`/api/compare`).
2. **Portfolio detail `/p/{slug}`** — base-100 NAV vs SPY chart with allocation-date markers; metrics row; current drifted holdings table (weight, side, drift since last rebalance) with long/short/cash grouping; allocation timeline, each entry expandable to positions + raw response; stale-data/delisting warnings.
3. **Prompt detail `/prompt/{slug}`** — full immutable text, portfolios using it with mini-metrics (does this prompt work across models?).
4. **Agent detail `/agent/{slug}`** — portfolios by this model (does this model work across prompts?).
5. **Admin `/admin`** — login; "New allocation" row-entry form (see above); "New portfolio" wizard (pick/create agent, pick/create prompt, then the same form); agents/prompts management; settings (default cost bps, gross exposure cap).

Charting: lightweight SVG/canvas line charts, self-written or a tiny dependency — match market-deck's frontend approach; no heavyweight chart library. Long tables and charts scroll within their own containers. Dark/light theme like market-deck.

## Deployment (Coolify + Nixpacks)

Copy market-deck's pattern verbatim: `nixpacks.toml` (pip install + `cd frontend && npm ci && npm run build`; start = `python -m app.migrate && uvicorn app.main:app`), `.python-version`, `.nvmrc`, `requirements.txt`. Env vars: `DATABASE_URL`, `ARENA_JWT_SECRET`, `ARENA_ADMIN_EMAIL`, `ARENA_ADMIN_PASSWORD`, optional cache TTLs, `ARENA_DEFAULT_COST_BPS` (default 10), `ARENA_GROSS_EXPOSURE_CAP` (default 200). Health check: a public endpoint that touches the DB (e.g. `GET /api/leaderboard`). Single instance assumed (in-memory rate limits), same as market-deck.

## Testing

- **Valuation engine (highest priority):** pytest with synthetic price/FX fixtures — initial allocation math; drift; rebalance turnover + two-sided costs; shorts (negative shares, funding convention, losses beyond −100%); blow-up freeze; multi-currency cash (FX drift, conversion at effective close); next-close effectiveness (weekend/holiday entry); missing-price carry-forward and flags; base-100 SPY comparison over identical windows; determinism.
- **Validation:** sum-to-100 rule, gross exposure cap, negative cash rejection, `^`/`=X`/`=F` symbol rejection, symbol-type derivation from syntax.
- **API:** auth boundaries (public reads open, writes 401/403), allocation lock enforcement (edit after effective close → 403), prompt immutability (no update route).
- **Frontend:** `svelte-check` + production build.

## Build order

1. **Scaffold** — repo layout mirroring market-deck (`backend/` package, `frontend/` Vite app, Alembic baseline migration with full schema, auth port, settings).
2. **Yahoo service + price cache** — port and strip from market-deck; symbol validation endpoint (incl. FX pairs for cash; rejects `^`, `=X`, and `=F` symbols).
3. **Valuation engine + tests** — pure functions, fixtures first. *Nothing else matters if this is wrong.*
4. **API routes** — public reads, admin writes, validation rules, lock enforcement, benchmark auto-seeding.
5. **Frontend** — leaderboard → portfolio detail → admin entry flow → prompt/agent pages → compare chart.
6. **Deploy** — nixpacks/Coolify files, README (setup, env vars, integrity rules, known simplifications), first deploy, seed benchmarks, enter the first real portfolios.

## Explicit non-goals (v1)

No LLM API calls, no automation of weekly runs, no response parsing, no IBKR or any broker, no options, no futures (deferred — Yahoo continuous contracts have roll artifacts; ETF equivalents like SSO/SH/GLD/TLT cover the use cases), no intraday prices, no borrow fees / cash interest / margin interest (documented simplifications), no margin-call modeling beyond the NAV ≤ 0 blow-up rule, no multi-user accounts, no email/notifications, no historical backtesting, no statistical-significance machinery beyond the age badge (revisit after ~1 year of data).
