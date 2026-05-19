# BusTrackerBG Development And Deployment

Web app and Flask API for Belgrade public transport stop search, favorites, route lookup, navigation, and scheduled departures.

The backend uses a local SQLite GTFS database (`gtfs.db`) and optional imported BG Prevoz route/timetable data. The frontend is a Vite app in `frontend/`.

Current deployment:

- `https://bustracker.gifted3.com`
- Oracle Cloud Always Free A1 VM
- Nginx serves `frontend/dist` from `/var/www/bustracker`
- Nginx proxies `/api` to Waitress on `127.0.0.1:5000`

## Features

- Search stops by station name, station ID, or address.
- Show nearby stops around an address, map click, or browser location.
- Save favorite stops and optional line presets.
- View scheduled departures for a stop in the next 60 minutes.
- Navigate between two stops, including routes with walking links and transfers.
- Display route geometry and stop sequences on the map.
- Password reset token flow for web accounts.

## Requirements

- Python 3.10+
- Node.js 18+
- SQLite

## Backend Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 api.py
```

The API defaults to `http://127.0.0.1:5000`.

Important environment variables:

- `JWT_SECRET`: required for stable login sessions across restarts.
- `APP_ENV`: use `local` for loopback development and `production` for public deployments.
- `ALLOWED_ORIGINS`: comma-separated frontend origins allowed by CORS.
- `API_HOST`, `API_PORT`: backend bind address and port.
- `DATA_DIR`: optional directory for persistent runtime data. Prefer setting this outside the source checkout, for example `../BusTrackerBG-data`; when set, default DB files are stored there.
- `APP_DATA_DB`: SQLite DB for web accounts and favorites. Defaults to `app_data.db`, or `$DATA_DIR/app_data.db` when `DATA_DIR` is set.
- `GTFS_DB`: SQLite DB for GTFS data. Defaults to `gtfs.db`, or `$DATA_DIR/gtfs.db` when `DATA_DIR` is set.
- `RATE_LIMIT_STORAGE_URI`: use `memory://` for local development only.
- `TRUST_PROXY`: set to `true` only when the API is behind a trusted proxy that strips inbound forwarded headers.
- `GTFS_UPDATE_INTERVAL_DAYS`, `GTFS_UPDATE_HOUR`, `GTFS_UPDATE_MINUTE`: scheduled GTFS refresh timing.
- `BGPREVOZ_UPDATE_ENABLED`: enables the separate scheduled BG Prevoz scraper/import job. The job checks daily by default and only rewrites DB rows for lines whose scraped content hash changed.
- `BGPREVOZ_UPDATE_INTERVAL_DAYS`, `BGPREVOZ_UPDATE_HOUR`, `BGPREVOZ_UPDATE_MINUTE`: scheduled BG Prevoz import timing.
- `BGPREVOZ_IMPORT_DELAY_SECONDS`: polite delay between BG Prevoz scraper requests.
- `GTFS_MAX_DOWNLOAD_BYTES`, `GTFS_MAX_UNCOMPRESSED_BYTES`, `GTFS_MAX_FILE_BYTES`: safety limits for GTFS feed downloads.

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

The frontend defaults to `http://127.0.0.1:5173` and proxies `/api` requests to the Flask backend.

For a production build:

```bash
cd frontend
npm run build
```

## Data

- `gtfs.db` stores transit routes, stops, trips, and stop times.
- `app_data.db` stores web app users and favorites.
- Database files are intentionally ignored by git.
- Prefer setting `DATA_DIR` outside the repo so local secrets and runtime SQLite state are not colocated with source files.
- Back up `app_data.db` before changing auth/favorites schema or ownership keys.

## Production Notes

The Flask API is served with Waitress. It does not terminate HTTPS itself. Put it behind a reverse proxy such as Caddy or Nginx for TLS.

For public deployments:

- Set a persistent `JWT_SECRET`.
- Set `ALLOWED_ORIGINS` to trusted frontend domains only.
- Bind `API_HOST=127.0.0.1` behind a reverse proxy.
- Set `TRUST_PROXY=true` only for a trusted reverse proxy path.
- Use a shared rate-limit backend instead of `RATE_LIMIT_STORAGE_URI=memory://` for multi-process or multi-server deployments. A single Oracle VM can use `memory://` while the API stays bound to `127.0.0.1`.

### Oracle VM Deploy

The production VM uses:

- `bustracker.service` running `waitress-serve --host=127.0.0.1 --port=5000 api:app`
- Nginx virtual host for `bustracker.gifted3.com`
- Let's Encrypt certificate managed by Certbot
- Built frontend copied to `/var/www/bustracker`

Typical update flow on the VM:

```bash
cd ~/BusTrackerBG
git pull
source .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm ci
npm run build
sudo rm -rf /var/www/bustracker/*
sudo cp -a dist/. /var/www/bustracker/
sudo chown -R www-data:www-data /var/www/bustracker

sudo systemctl restart bustracker
sudo systemctl reload nginx
```

If only frontend files changed, the backend restart is not required. If only backend files changed, the frontend build/copy is not required.

Manual health checks:

```bash
curl https://bustracker.gifted3.com/api/health
curl "https://bustracker.gifted3.com/api/search?q=Kalemegdan"
```

## Credits

- Static GTFS timetable data provided by [data.gov.rs](https://data.gov.rs/).
- Original public transport API reverse-engineering context from [MikMik1011/bgpp](https://github.com/MikMik1011/bgpp).
