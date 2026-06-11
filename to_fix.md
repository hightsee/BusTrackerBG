# BusTrackerBG Findings To Fix

Audit date: 2026-05-12

Scope: static repo review, read-only SQLite inspection, Python AST parsing, and `node --check frontend/app.js`. I did not run the Flask server, import GTFS, run the BG Prevoz scraper, or build the frontend because those commands create or rewrite runtime files. The only file created by this audit is this report.

## Verification Performed

- Python syntax parse passed for `api.py`, `config.py`, `db_manager.py`, `gtfs_manager.py`, `bgprevoz_importer.py`, `gsp_overrides.py`, and `wsgi.py`.
- JavaScript syntax check passed for `frontend/app.js`.
- Local `gtfs.db` inspected read-only: 241 routes, 87,474 trips, 2,300,609 stop_times, 3,266 stops, and 3 calendar rows.
- Local `app_data.db` inspected read-only: 2 API users and 3 favorites.

## High Priority

### 1. Route planning ignores service calendars and date exceptions

Files: `gtfs_manager.py`

`find_routes_between_stops`, `find_direct_routes_between_stop_sets`, and `get_connected_stops` join `stop_times`, `trips`, and `routes` without restricting trips to active service IDs for the requested day. They only filter night-line visibility with `should_show_line_now`.

Relevant areas:

- `gtfs_manager.py:779-999`
- `gtfs_manager.py:1001-1050`
- `gtfs_manager.py:1052-1157`

Impact: `/api/routing`, `/api/routing/batch`, `/api/journey`, and `/api/stops/connected` can suggest lines that do not run today, routes that are only valid for a different service day, or transfer options that have no usable departures. This is especially visible when the UI later calls `/api/predict/stop` and shows no upcoming departures for a suggested route.

Fix direction: thread active service IDs into all route-discovery queries, preferably with a helper that accepts an optional `date_obj`. Add regression coverage with a fixture containing weekday-only and weekend-only trips between the same stops.

### 2. GTFS parser does not support `calendar_dates.txt`

Files: `gtfs_manager.py`, schema in `gtfs.db`

The schema only creates `calendar`; `_validate_zip_manifest` requires `calendar.txt` but not `calendar_dates.txt`, `_parse_zip` never imports exceptions, and `_get_active_service_ids` only reads weekly calendar rows.

Relevant areas:

- `gtfs_manager.py:87-92`
- `gtfs_manager.py:137-155`
- `gtfs_manager.py:281-285`
- `gtfs_manager.py:321-336`

Impact: holiday additions/removals, special event service, and one-off cancellations are ignored. Even if the feed contains correct exception data, arrival and route prediction will behave as if only the base weekly calendar exists.

Fix direction: add a `calendar_dates(service_id, date, exception_type)` table, import `calendar_dates.txt` when present, and update active-service resolution to add exception type 1 and remove exception type 2 for the target date.

### 3. Public `/api/stops/nearby` has no radius or coordinate bounds

Files: `api.py`, `gtfs_manager.py`

`/api/stops/nearby` accepts any numeric `lat`, `lon`, and `radius`, then enriches every returned stop by calling `get_stop_lines`. `get_stops_nearby` calculates a bounding box directly from radius.

Relevant areas:

- `api.py:1042-1060`
- `gtfs_manager.py:1159-1197`
- `api.py:249-282`

Impact: a very large radius can scan most or all stops and trigger many additional stop-line queries. With the local DB size of 3,266 stops and 2.3M stop_times, this is an easy unauthenticated CPU/SQLite amplification path.

Fix direction: validate Belgrade-ish coordinate bounds, cap radius consistently with other endpoints, and add a hard result limit before enrichment. Consider batch-fetching stop lines for nearby results instead of one query per stop.

### 4. GTFS refresh rebuilds the live database in place

Files: `gtfs_manager.py`, `api.py`

Startup and scheduled refresh call `gtfs_manager.update_gtfs()` in the API process. `_parse_zip` deletes core tables from the live DB and repopulates them before committing.

Relevant areas:

- `api.py:155-190`
- `gtfs_manager.py:157-226`
- `gtfs_manager.py:228-289`

Impact: during refresh, API reads can observe lock contention, stale reads, or a partially rebuilt data set depending on SQLite transaction timing. If parsing fails after deletes, SQLite should roll back on connection close, but the API still shares the same mutable DB file during a heavy import.

Fix direction: build into a temporary database, validate row counts and metadata, then atomically swap or promote it. Keep API reads on the previous known-good DB until the new one is complete.

## Medium Priority

### 5. Address geocoding can make three slow external calls per cache miss

Files: `api.py`

`geocode_address` performs up to three Nominatim requests with 8-second timeouts on a single `/api/search/address` cache miss. That endpoint is public and only protected by the global limiter.

Relevant areas:

- `api.py:377-423`
- `api.py:626-688`

Impact: a small number of distinct address queries can tie up Waitress worker threads and generate bursts against Nominatim. The current in-memory cache only helps repeated exact normalized queries on one process.

Fix direction: add an endpoint-specific rate limit, reduce external attempts, cache negative results briefly, and consider a queue/background resolver if address lookup becomes central.

### 6. Route and stop caches are not invalidated after GTFS/BG Prevoz imports

Files: `api.py`, `gtfs_manager.py`, `bgprevoz_importer.py`

The API has a module-level TTL cache for search, stop lines, geocoding, address search, and connected stops. Scheduled GTFS/BG Prevoz imports mutate the DB but do not clear or version these cache entries.

Relevant areas:

- `api.py:65-96`
- `api.py:249-282`
- `api.py:607-624`
- `api.py:1143-1167`
- `gtfs_manager.py:201-214`
- `bgprevoz_importer.py:270-285`

Impact: after an import, users can see stale line lists or connected destinations for up to 5-10 minutes. That is probably acceptable for some views, but confusing for scheduled update workflows and hard to reason about.

Fix direction: expose a `cache_clear()` helper and call it after successful imports, or include a GTFS metadata version in cache keys.

### 7. Favorites allow blank names after trimming

Files: `api.py`, `db_manager.py`

The API checks `data.get('name')`, but `db_manager.save_favorite` trims the name afterward. A name containing only spaces passes the API check and becomes an empty favorite name. The same applies to update.

Relevant areas:

- `api.py:1189-1211`
- `api.py:1213-1241`
- `db_manager.py:74-119`
- `db_manager.py:140-177`

Impact: users can create or update favorites with an empty visible label, causing awkward UI behavior and uniqueness collisions.

Fix direction: trim and validate favorite names in the route handler before calling `AppDataManager`, and enforce a reasonable max length.

### 8. Exact stop ID resolution can prefer the wrong row if raw and public IDs both exist

Files: `api.py`, `gtfs_manager.py`

`resolve_station_id` and `resolve_stop_name` search both the requested numeric ID and `ID + 20000`, then order by `stop_id`. If both forms exist, lexicographic ordering can select the direct raw ID rather than the intended public-to-GTFS mapping.

Relevant areas:

- `api.py:197-231`
- `gtfs_manager.py:359-374`

Impact: predictions, favorites, and routing can bind to the wrong stop if a feed/import ever contains both representations.

Fix direction: make precedence explicit: public IDs below 20000 should prefer `id + 20000`; raw IDs above 20000 should prefer exact; direct low IDs should only be fallback.

## Low Priority / Cleanup

### 9. `/api/predict/line` returns future not-started trips as `active_buses`

Files: `api.py`, `gtfs_manager.py`

`predict_bus_position` appends every future trip as `status: not_started`, and `/api/predict/line` returns that list under `active_buses`.

Relevant areas:

- `api.py:731-741`
- `gtfs_manager.py:399-525`

Impact: for frequent lines this endpoint can return a large payload that is not actually active vehicle position data. The name suggests real-time vehicles, but the data is schedule-derived.

Fix direction: rename the response field or cap future trips to the next few departures. If real-time data is not available, make that explicit in the API contract.

### 10. Local runtime files exist and should stay untracked

Files present locally: `.env`, `gtfs.db`, `app_data.db`, and `backups/*.db`

`.gitignore` already excludes these, which is good. The risk is operational rather than code-level: the repo directory currently contains real local runtime state next to source code.

Relevant areas:

- `.gitignore:1-22`
- `config.py:37-48`

Impact: accidental manual packaging, copying, or deployment from the repo root can include secrets or data if tooling bypasses git.

Fix direction: prefer `DATA_DIR` outside the repo for local and production runtime files. Keep source checkout disposable.

### 11. Frontend stores JWT in `localStorage`

Files: `frontend/app.js`

The frontend stores the bearer token in `localStorage`.

Relevant areas:

- `frontend/app.js:662-670`
- `frontend/app.js:897-930`

Impact: this is common for small SPAs, but any future XSS gives token persistence. The rendering code escapes user-visible HTML in most places, so this is not urgent, but it is worth tracking.

Fix direction: for a harder auth posture, use secure, HttpOnly, SameSite cookies and CSRF protection, or keep the token in memory with a refresh strategy.

## Test Gaps To Add

- Route-planning fixture tests for weekday/weekend/exception services.
- Arrival tests for public ID vs raw GTFS ID resolution.
- API tests for `/api/stops/nearby` radius and coordinate validation.
- Favorite create/update tests for blank names, duplicate names, and line presets.
- Import tests that prove failed GTFS rebuilds do not affect the currently served DB.
- Frontend smoke tests for search, favorites, navigation, and map interactions once a dev server can be run.

## Commands Run

```bash
python3 -c 'import ast, pathlib; files=["api.py","config.py","db_manager.py","gtfs_manager.py","bgprevoz_importer.py","gsp_overrides.py","wsgi.py"]; [ast.parse(pathlib.Path(f).read_text(), filename=f) for f in files]; print("python ast ok", len(files))'
node --check frontend/app.js
sqlite3 gtfs.db 'select "routes", count(*) from routes union all select "trips", count(*) from trips union all select "stop_times", count(*) from stop_times union all select "stops", count(*) from stops union all select "calendar", count(*) from calendar;'
sqlite3 gtfs.db 'select min(start_date), max(end_date) from calendar; select service_id,start_date,end_date from calendar order by service_id limit 10;'
sqlite3 app_data.db 'select "users", count(*) from api_users union all select "favorites", count(*) from favorites;'
```
