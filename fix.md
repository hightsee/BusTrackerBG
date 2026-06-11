# Fix Plan: BG Prevoz Route-Stop Corrections

Created: 2026-05-13

## Problem

Lines `37` and `58` are mapped to `Glavna posta` in local schedule data, but BG Prevoz currently shows `Pionirski park`.

Local `gtfs.db` schedule rows currently contain:

- Line `37`: `20266` / `20267` (`Glavna posta`)
- Line `58`: `20266` / `20267` (`Glavna posta`)
- Line `37`: zero `20703` / `20704` (`Pionirski park`) stop-time rows
- Line `58`: zero `20703` / `20704` (`Pionirski park`) stop-time rows

BG Prevoz currently shows:

- Line `37`, smer A: `#703 Pionirski park`
- Line `37`, smer B: no `Glavna posta`; route uses `#196 Terazije`, `#702 Trg Republike`, etc.
- Line `58`, smer A: `#703 Pionirski park`
- Line `58`, smer B: `#704 Pionirski park`

Root cause: predictions/departures/routing are based on stale official GTFS `stop_times`, while BG Prevoz has newer route-stop patterns. The local DB also shows no `bgprevoz_line_hash:*` metadata, so BG Prevoz imports have not been applied locally.

## Goal

Use BG Prevoz as the correction layer for line route-stop patterns and generated schedule stop mappings when BG Prevoz has fresher data than GTFS.

The fix is successful when:

- `/api/route?line=37` shows BG Prevoz stops, including `Pionirski park`.
- `/api/route?line=58` shows BG Prevoz stops with correct direction/station IDs.
- `/api/predict/stop?station_id=703` or `704` can surface lines that BG Prevoz maps there.
- Lines `37` and `58` no longer depend on `Glavna posta` stop IDs for corrected patterns.
- Regression tests lock this behavior.

## Implementation Order

### 1. Verify BG Prevoz importer can discover and parse lines

Run the importer in dry-run or limited mode for:

- `37`
- `58`

Confirm it fetches:

- line list pages
- direction stop pages
- timetable pages

Expected BG Prevoz stop mappings:

- `37` smer A includes public station `703`
- `37` smer B does not include `266` or `267`
- `58` smer A includes public station `703`
- `58` smer B includes public station `704`

### 2. Make public station ID mapping authoritative

In `bgprevoz_importer.py`, ensure BG Prevoz station IDs map directly before any fuzzy/nearest matching:

- `703 -> 20703`
- `704 -> 20704`
- `266 -> 20266`
- `267 -> 20267`

Expected rule:

1. Try `raw_stop_id(public_id)`.
2. Try exact provided ID only if needed.
3. Try nearest/name fallback only if direct mapping does not exist.

Do not let nearest matching override an existing direct public-to-raw stop ID.

### 3. Import BG Prevoz lines into `gtfs.db`

For lines imported from BG Prevoz:

- Replace that line's route-specific generated trips/stop_times.
- Store `bgprevoz_line_hash:<line>` metadata.
- Leave unrelated GTFS lines untouched.

Confirm after import:

```sql
SELECT key, value
FROM metadata
WHERE key IN ('bgprevoz_line_hash:37', 'bgprevoz_line_hash:58');
```

### 4. Ensure schedule lookups use corrected stop_times

After import, verify:

```sql
-- Should be > 0 where BG Prevoz says the line serves Pionirski park.
SELECT COUNT(*)
FROM routes r
JOIN trips t ON t.route_id = r.route_id
JOIN stop_times st ON st.trip_id = t.trip_id
WHERE r.route_short_name = '58'
  AND st.stop_id IN ('20703', '20704');

-- Should no longer be the source for corrected line patterns.
SELECT COUNT(*)
FROM routes r
JOIN trips t ON t.route_id = r.route_id
JOIN stop_times st ON st.trip_id = t.trip_id
WHERE r.route_short_name = '58'
  AND st.stop_id IN ('20266', '20267');
```

Repeat for line `37`.

### 5. Replace or demote static `gsp_overrides.py`

Line `58` currently has a manual override. Once BG Prevoz import is reliable:

- Prefer imported BG Prevoz route data over `GSP_ROUTE_OVERRIDES`.
- Remove line `58` override if it becomes redundant.
- At minimum, correct direction IDs/station IDs if the override remains.

Important: current manual `58` override appears direction-flipped compared with live BG Prevoz:

- Current override direction A uses `704`
- Live BG Prevoz direction A uses `703`
- Current override direction B uses `703`
- Live BG Prevoz direction B uses `704`

### 6. Add regression tests

Add importer/route tests that prove:

- Public ID `703` maps to raw stop `20703`.
- Public ID `704` maps to raw stop `20704`.
- Direct public/raw mapping wins before nearest-name matching.
- Imported line `37` does not map the central segment to `Glavna posta`.
- Imported line `58` does not map the central segment to `Glavna posta`.
- `/api/route?line=58` and stop-time data agree on the same stop IDs.

### 7. Manual verification

After implementation, run:

```bash
python3 -m unittest discover -s tests
python3 -c 'import ast, pathlib; files=["api.py","config.py","db_manager.py","gtfs_manager.py","bgprevoz_importer.py","gsp_overrides.py","wsgi.py"]; [ast.parse(pathlib.Path(f).read_text(), filename=f) for f in files]; print("python ast ok", len(files))'
node --check frontend/app.js
```

Then inspect DB rows:

```bash
sqlite3 gtfs.db "
SELECT r.route_short_name, s.stop_id, s.stop_name, COUNT(*)
FROM routes r
JOIN trips t ON t.route_id = r.route_id
JOIN stop_times st ON st.trip_id = t.trip_id
JOIN stops s ON s.stop_id = st.stop_id
WHERE r.route_short_name IN ('37', '58')
  AND s.stop_id IN ('20266', '20267', '20703', '20704')
GROUP BY r.route_short_name, s.stop_id, s.stop_name
ORDER BY r.route_short_name, s.stop_id;
"
```

Expected: corrected BG Prevoz-imported rows should use `Pionirski park` where BG Prevoz says so, not stale `Glavna posta`.

