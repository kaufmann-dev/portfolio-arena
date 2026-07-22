# Repository Instructions

Portfolio Arena: a FastAPI + SQLAlchemy backend (`backend/`, PostgreSQL) serving a Svelte 5
(runes) + Vite + TS SPA (`frontend/`). Python 3.12 (`.python-version`), Node 20 (`.nvmrc`).

## Build and Verification

- One venv at the repo root: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.
- Backend tests: `cd backend && ../.venv/bin/python -m pytest`. Tests spin up a throwaway
  Postgres via testcontainers over the podman socket (`systemctl --user start podman.socket`),
  or set `TEST_DATABASE_URL` to reuse a database. Yahoo is stubbed — no test hits the network.
- Lint/format (ruff, config in `backend/pyproject.toml`, line length 110):
  `cd backend && ../.venv/bin/ruff check . && ../.venv/bin/ruff format .`.
- Frontend: `cd frontend && npm run check && npm run build` (svelte-check + production build).
  There are no frontend unit tests; `check` + `build` is the verification.
- Running the backend needs `DATABASE_URL`, `ARENA_PUBLIC_URL`, and the four required
  `ARENA_OIDC_*` variables (see `README.md` Development).

## Project Structure

- `backend/app/services/valuation.py` is the deterministic correctness core: pure functions,
  no wall-clock reads (callers pass every date), same inputs → identical output. NAVs are
  never stored; every request recomputes from allocations + cached price series. Keep it pure.
- API routers: `backend/app/api/public.py` (read-only, no auth, rate-limited),
  `backend/app/api/admin.py` (writes, guarded by `Depends(require_admin)`), `auth.py`,
  `keys.py` (API-key management, browser-admin-session only). Response shaping is shared in
  `backend/app/services/serialize.py`.
- All write/integrity logic lives once in `backend/app/services/admin_ops.py` (raising
  `AdminOpError`); the admin router and the MCP tools are thin callers of it. Put new write
  rules there, not in a router.
- Admin-only fields (per-position `note`, holding `entry_price`/`current_price`) are gated
  behind the `admin=True` flag in `services/serialize.py`. Never expose them from `public.py`.
- `backend/app/services/evaluation_schedule.py` is Arena's evaluator-facing scheduling authority.
  Keep the MCP schedule response and server-side evaluation cutoff enforcement on this shared
  service; external evaluator projects must not duplicate its calendar logic.
- MCP server: `backend/app/mcp_server/` (FastMCP, mounted at `/mcp` in `main.py`). It exposes
  the full app surface as tools — everything an admin/visitor can do **except** API-key
  management — authenticated by an API key (`Authorization: Bearer <key>` or `X-API-Key`, no
  anonymous access). Tools always serialize with `admin=True` since the endpoint is key-gated.
  Keys are stored as SHA-256 hashes in the `api_keys` table (`security.py` helpers).

## Database and Migrations

- `backend/app/models.py` mirrors the Alembic migrations in `backend/alembic/versions/`.
  A schema change requires **both** a model edit and a new numbered migration
  (e.g. `0004_*.py`, with `down_revision` = the previous revision id). Migrations run on
  startup via `python -m app.migrate`; do not rely on model changes alone.

## Testing

- Tests live in `backend/tests/` (pytest). Shared fixtures in `conftest.py`: `client`,
  `admin_headers`, `sample_agent`, `sample_prompt`, `sample_portfolio`; each test truncates
  and reseeds all tables. `stub_yahoo` provides a fixed symbol universe (SPY, RSP, AAPL,
  MSFT, EURUSD=X, GC=F, BTC-USD) with deterministic prices — use those symbols.
- Use `backdate_allocation` (`backend/tests/util.py`) to make an allocation locked/valued,
  since real allocations can never be backdated through the API.

## Frontend Conventions

- API response types in `frontend/src/lib/api/types.ts` are hand-maintained to mirror the
  backend serializers; update them whenever a serializer's shape changes.
- Use the existing `apiJson`/`postJson`/`patchJson`/`del` helpers in
  `frontend/src/lib/api/client.ts`; format with `lib/format.ts` helpers.
- Format Svelte/TS with Prettier: `cd frontend && npm run format` (check-only: `npm run format:check`).
- When writing or refactoring `.svelte`/`.svelte.ts` files, use the `svelte-code-writer` and
  `svelte-core-bestpractices` skills and the `svelte` MCP (`list-sections`, `get-documentation`,
  and `svelte-autofixer` — run the autofixer until clean before finishing).

### Svelte 5 rune conventions

This is a Svelte 5 runes project. Prefer fine-grained reactivity over effects.

- `$props()` for inputs; `$state` only for values that drive the template/a `$derived`/an effect.
  Use `$state.raw` for large objects or API responses that are reassigned wholesale, not mutated.
- `$derived` (or `$derived.by` for multi-line) for anything computed — never an `$effect` that
  writes derived state.
- Avoid `$effect`. Reach for it only to sync with a genuinely external, non-Svelte concern
  (e.g. writing `data-theme` to `document`). React to changes at the event boundary
  (`onclick`, `onValueChange`) or with getter/setter `bind:value={() => ..., (v) => ...}` instead.
- Reusable markup: snippets + `{@render ...}`. Keyed `{#each}` with stable ids — never the index.
- New code only: no `export let`, `$:`, `<slot>`, `<svelte:component>`/`<svelte:self>`,
  `use:action`, or `on:` event directives. Use `$props`, `$derived`, snippets, `{@render}`,
  `<Self>` imports, `{@attach}`, and `onclick`-style handlers.

## Git

- Commit subjects follow Conventional Commits (`feat(scope): …`, `fix`, `refactor`, `chore`, `docs`).
