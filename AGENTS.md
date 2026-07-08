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
- Running the backend needs `DATABASE_URL`, `ARENA_JWT_SECRET`, `ARENA_ADMIN_EMAIL`,
  `ARENA_ADMIN_PASSWORD` (see `README.md` Development).

## Project Structure

- `backend/app/services/valuation.py` is the deterministic correctness core: pure functions,
  no wall-clock reads (callers pass every date), same inputs → identical output. NAVs are
  never stored; every request recomputes from allocations + cached price series. Keep it pure.
- API routers: `backend/app/api/public.py` (read-only, no auth, rate-limited),
  `backend/app/api/admin.py` (writes, guarded by `Depends(require_admin)`), `auth.py`.
  Response shaping is shared in `backend/app/api/serialize.py`.
- Admin-only fields (per-position `note`, holding `entry_price`/`current_price`) are gated
  behind the `admin=True` flag in `serialize.py`. Never expose them from `public.py`.

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
- Use the `$state`/`$derived`/`$props` runes and the existing `apiJson`/`postJson`/`patchJson`/`del`
  helpers in `frontend/src/lib/api/client.ts`.

## Git

- Commit subjects follow Conventional Commits (`feat(scope): …`, `fix`, `refactor`, `chore`, `docs`).
