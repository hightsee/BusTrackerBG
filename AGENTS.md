# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Belgrade public transport tracker with a Python API and a Vite frontend.

- `api.py` is the Flask entry point for routes, auth, rate limiting, scheduled imports, and caching.
- `config.py` centralizes environment parsing and safety checks.
- `db_manager.py`, `gtfs_manager.py`, `bgprevoz_importer.py`, and `gsp_overrides.py` hold data access, GTFS lookup/update logic, importer behavior, and service overrides.
- `wsgi.py` is the production WSGI entry.
- `frontend/` contains the browser app: `app.js`, `style.css`, `index.html`, Vite config, and `frontend/assets/`.
- `deploy/oracle/` contains Oracle VM deployment examples.
- Runtime databases such as `gtfs.db` and `app_data.db` are local data files and should stay out of git.

## Build, Test, and Development Commands

Backend setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 api.py
```

The API defaults to `http://127.0.0.1:5000`.

Frontend setup:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server defaults to `http://127.0.0.1:5173` and proxies `/api` to Flask. Use `npm run build` for production assets and `npm run preview` to inspect them.

## Coding Style & Naming Conventions

Use 4-space indentation for Python and 2-space indentation for frontend JavaScript/CSS. Keep Python names `snake_case`; use JavaScript `camelCase` for functions, state fields, and local variables. Prefer existing module-level helpers over adding new framework abstractions. Keep environment access in `config.py`, not scattered through route handlers.

## Testing Guidelines

There is no committed automated test suite yet. For backend changes, at minimum run the API locally and exercise affected endpoints, for example `/api/health`, `/api/search?q=Kalemegdan`, and stop prediction routes when GTFS data is available. For frontend changes, run `npm run build` before submitting and manually verify search, favorites, navigation, and map interactions in the dev server.

## Commit & Pull Request Guidelines

Git history uses short, direct commit subjects such as `fix`, `readme update`, and `database bug fix`. Prefer clearer imperative subjects, for example `Fix stop lookup fallback` or `Update Oracle deploy docs`. Pull requests should include a summary, affected areas, environment or data migration notes, manual test results, and screenshots for UI changes.

## Security & Configuration Tips

Never commit `.env`, database files, secrets, or downloaded GTFS archives. Set a persistent `JWT_SECRET` outside local development. Keep `ALLOWED_ORIGINS` restricted in production, bind the API to loopback behind a reverse proxy, and only set `TRUST_PROXY=true` when the proxy path is trusted.
