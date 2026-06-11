import time
import datetime
import hashlib
import json
import logging
import copy
import math
import re
import secrets
import threading
import urllib.parse
import urllib.request
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
import jwt
import bcrypt
from apscheduler.schedulers.background import BackgroundScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    ALLOWED_ORIGINS,
    API_HOST,
    API_PORT,
    BGPREVOZ_IMPORT_DELAY_SECONDS,
    BGPREVOZ_UPDATE_ENABLED,
    BGPREVOZ_UPDATE_HOUR,
    BGPREVOZ_UPDATE_INTERVAL_DAYS,
    BGPREVOZ_UPDATE_MINUTE,
    GTFS_UPDATE_HOUR,
    GTFS_UPDATE_INTERVAL_DAYS,
    GTFS_UPDATE_MINUTE,
    IS_LOCAL_DEV,
    JWT_SECRET,
    RATE_LIMIT_STORAGE_URI,
    TRUST_PROXY,
)
from bgprevoz_importer import BgPrevozImporter
from db_manager import app_data_manager
from gsp_overrides import override_lines_for_stop
from gtfs_manager import gtfs_manager, normalize_text, should_show_line_now

app = Flask(__name__)
if TRUST_PROXY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# Restrict CORS to specific origins from configuration
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

# Initialize Rate Limiter
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["300 per day", "100 per hour"],
    storage_uri=RATE_LIMIT_STORAGE_URI,
)

if RATE_LIMIT_STORAGE_URI == "memory://":
    logging.warning("Using in-memory rate limiting. Configure RATE_LIMIT_STORAGE_URI for shared production limits.")

# Use the secure JWT secret from config
app.config['SECRET_KEY'] = JWT_SECRET
START_TIME = time.time()
scheduler = BackgroundScheduler(daemon=True)
transit_data_update_lock = threading.Lock()

GEOCODING_VIEWBOX = "20.0908,45.0770,20.7277,44.3691"
BELGRADE_LAT_MIN = 44.35
BELGRADE_LAT_MAX = 45.10
BELGRADE_LON_MIN = 20.05
BELGRADE_LON_MAX = 20.75
PUBLIC_NEARBY_MAX_RADIUS_M = 1500
PUBLIC_NEARBY_MAX_RESULTS = 80
ADDRESS_SEARCH_MAX_RADIUS_M = 1200
ADDRESS_SEARCH_MAX_RESULTS = 20
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_ITEMS = 512
_TTL_CACHE = {}
TRANSIT_DATA_CACHE_NAMESPACES = {
    "stop_lines",
    "stop_search",
    "address_search",
    "connected_stops",
    "routing",
}
PASSWORD_RESET_TOKEN_TTL_MINUTES = 30
ROUTING_CACHE_TTL_SECONDS = 180
MAX_SEARCH_QUERY_LENGTH = 120
MAX_ROUTING_BATCH_PAIRS = 40

def hash_reset_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def validate_new_password(password):
    if len(password) < 8 or len(password) > 256:
        return "Password must be between 8 and 256 characters"
    return None

def cache_get(namespace, key, ttl_seconds):
    now = time.monotonic()
    cache_key = (namespace, key)
    with _CACHE_LOCK:
        item = _TTL_CACHE.get(cache_key)
        if not item:
            return None
        expires_at, value = item
        if expires_at <= now:
            _TTL_CACHE.pop(cache_key, None)
            return None
        return copy.deepcopy(value)

def cache_set(namespace, key, value, ttl_seconds):
    now = time.monotonic()
    with _CACHE_LOCK:
        if len(_TTL_CACHE) >= _CACHE_MAX_ITEMS:
            expired_keys = [
                cache_key for cache_key, (expires_at, _) in _TTL_CACHE.items()
                if expires_at <= now
            ]
            for expired_key in expired_keys:
                _TTL_CACHE.pop(expired_key, None)
            if len(_TTL_CACHE) >= _CACHE_MAX_ITEMS:
                oldest_key = min(_TTL_CACHE, key=lambda cache_key: _TTL_CACHE[cache_key][0])
                _TTL_CACHE.pop(oldest_key, None)
        _TTL_CACHE[(namespace, key)] = (now + ttl_seconds, copy.deepcopy(value))

def routing_cache_key(origin_sid, dest_sid, strict_stops):
    return json.dumps(
        {
            "from": str(origin_sid),
            "to": str(dest_sid),
            "strict": bool(strict_stops),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

def cache_clear(namespaces=None):
    namespace_set = set(namespaces) if namespaces is not None else None
    with _CACHE_LOCK:
        if namespace_set is None:
            _TTL_CACHE.clear()
            return
        for cache_key in list(_TTL_CACHE):
            if cache_key[0] in namespace_set:
                _TTL_CACHE.pop(cache_key, None)

def clear_transit_data_cache(reason):
    cache_clear(TRANSIT_DATA_CACHE_NAMESPACES)
    logging.info("Cleared transit data cache after %s.", reason)

def is_in_belgrade_bounds(lat, lon):
    return (
        BELGRADE_LAT_MIN <= lat <= BELGRADE_LAT_MAX
        and BELGRADE_LON_MIN <= lon <= BELGRADE_LON_MAX
    )

def _run_bgprevoz_update_unlocked():
    if not BGPREVOZ_UPDATE_ENABLED:
        logging.info("BG Prevoz update skipped because BGPREVOZ_UPDATE_ENABLED=false.")
        return False

    logging.info("Running scheduled BG Prevoz import from API service...")
    summary = BgPrevozImporter(delay_seconds=BGPREVOZ_IMPORT_DELAY_SECONDS).apply()
    logging.info("BG Prevoz import completed: %s", summary)
    if int(summary.get("imported_lines") or 0) > 0:
        clear_transit_data_cache("BG Prevoz import")
    return True

def scheduled_gtfs_update(force=False):
    if not transit_data_update_lock.acquire(blocking=False):
        logging.info("GTFS update skipped because another transit data update is already in progress.")
        return False

    try:
        logging.info("Running scheduled GTFS update from API service...")
        updated = gtfs_manager.update_gtfs(force=force)
        if updated:
            if BGPREVOZ_UPDATE_ENABLED:
                logging.info("Applying BG Prevoz import after GTFS refresh because it is the source of truth.")
                _run_bgprevoz_update_unlocked()
            clear_transit_data_cache("GTFS update")
        return updated
    except Exception:
        logging.exception("Scheduled GTFS update failed.")
        return False
    finally:
        transit_data_update_lock.release()

def scheduled_bgprevoz_update():
    if not transit_data_update_lock.acquire(blocking=False):
        logging.info("BG Prevoz update skipped because another transit data update is already in progress.")
        return False

    try:
        return _run_bgprevoz_update_unlocked()
    except Exception:
        logging.exception("Scheduled BG Prevoz import failed.")
        return False
    finally:
        transit_data_update_lock.release()

def compute_next_gtfs_run() -> datetime.datetime:
    now = datetime.datetime.now()
    next_run = datetime.datetime.combine(
        now.date(),
        datetime.time(GTFS_UPDATE_HOUR, GTFS_UPDATE_MINUTE),
    )
    if next_run <= now:
        next_run += datetime.timedelta(days=1)
    return next_run

def compute_next_bgprevoz_run() -> datetime.datetime:
    now = datetime.datetime.now()
    next_run = datetime.datetime.combine(
        now.date(),
        datetime.time(BGPREVOZ_UPDATE_HOUR, BGPREVOZ_UPDATE_MINUTE),
    )
    if next_run <= now:
        next_run += datetime.timedelta(days=1)
    return next_run

def should_refresh_gtfs_on_start() -> bool:
    last_update = gtfs_manager.get_last_update()
    if not last_update:
        return True

    try:
        last_update_dt = datetime.datetime.fromisoformat(last_update)
    except ValueError:
        return True

    now = datetime.datetime.now(last_update_dt.tzinfo) if last_update_dt.tzinfo else datetime.datetime.now()
    return (now - last_update_dt) >= datetime.timedelta(days=GTFS_UPDATE_INTERVAL_DAYS)

def should_refresh_bgprevoz_on_start() -> bool:
    if not BGPREVOZ_UPDATE_ENABLED:
        return False

    last_update = gtfs_manager.get_metadata("bgprevoz_last_update")
    if not last_update:
        return True

    try:
        last_update_dt = datetime.datetime.fromisoformat(last_update)
    except ValueError:
        return True

    now = datetime.datetime.now(last_update_dt.tzinfo) if last_update_dt.tzinfo else datetime.datetime.now()
    return (now - last_update_dt) >= datetime.timedelta(days=BGPREVOZ_UPDATE_INTERVAL_DAYS)

def initialize_services():
    gtfs_refresh_on_start = should_refresh_gtfs_on_start()
    if gtfs_refresh_on_start:
        logging.info("GTFS data missing or stale; starting background refresh...")
        threading.Thread(target=scheduled_gtfs_update, kwargs={"force": True}, daemon=True).start()
    elif should_refresh_bgprevoz_on_start():
        logging.info("BG Prevoz data missing or stale; starting background import...")
        threading.Thread(target=scheduled_bgprevoz_update, daemon=True).start()

    if not scheduler.running:
        next_gtfs_run = compute_next_gtfs_run()
        scheduler.add_job(
            scheduled_gtfs_update,
            id="gtfs_update",
            trigger='interval',
            days=GTFS_UPDATE_INTERVAL_DAYS,
            next_run_time=next_gtfs_run,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logging.info(
            "Scheduled GTFS refresh every %s days; next run at %s",
            GTFS_UPDATE_INTERVAL_DAYS,
            next_gtfs_run.isoformat(),
        )

        if BGPREVOZ_UPDATE_ENABLED:
            next_bgprevoz_run = compute_next_bgprevoz_run()
            scheduler.add_job(
                scheduled_bgprevoz_update,
                id="bgprevoz_update",
                trigger='interval',
                days=BGPREVOZ_UPDATE_INTERVAL_DAYS,
                next_run_time=next_bgprevoz_run,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
            logging.info(
                "Scheduled BG Prevoz import every %s days; next run at %s",
                BGPREVOZ_UPDATE_INTERVAL_DAYS,
                next_bgprevoz_run.isoformat(),
            )
        else:
            logging.info("BG Prevoz scheduled import disabled.")

        scheduler.start()

def resolve_station_id(station_id):
    import sqlite3

    requested_station_id = str(station_id or '').strip()
    if not requested_station_id:
        return None

    lookup_ids = []
    if requested_station_id.isdigit() and int(requested_station_id) < 20000:
        lookup_ids.append(str(int(requested_station_id) + 20000))
    lookup_ids.append(requested_station_id)

    conn = sqlite3.connect(gtfs_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    placeholders = ', '.join(['?'] * len(lookup_ids))
    cursor.execute(
        f"""
        SELECT stop_id
        FROM stops
        WHERE stop_id IN ({placeholders})
        ORDER BY CASE stop_id {' '.join(f'WHEN ? THEN {index}' for index, _ in enumerate(lookup_ids))} ELSE {len(lookup_ids)} END
        LIMIT 1
        """,
        lookup_ids + lookup_ids,
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    raw_stop_id = str(row['stop_id'])
    public_stop_id = str(int(raw_stop_id) - 20000) if raw_stop_id.isdigit() and int(raw_stop_id) >= 20000 else raw_stop_id
    return {
        'uid': public_stop_id,
        'sid': public_stop_id,
        'raw_stop_id': raw_stop_id,
    }

def line_exists(line):
    import sqlite3

    requested_lines = [value.strip() for value in str(line or '').split(',') if value.strip()]
    if not requested_lines:
        return True

    conn = sqlite3.connect(gtfs_manager.db_path)
    cursor = conn.cursor()
    placeholders = ', '.join(['?'] * len(requested_lines))
    cursor.execute(
        f"SELECT route_short_name FROM routes WHERE route_short_name IN ({placeholders})",
        requested_lines,
    )
    found_lines = {row[0] for row in cursor.fetchall()}
    conn.close()
    return all(line_value in found_lines for line_value in requested_lines)

def normalize_favorite_name(name):
    normalized_name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not normalized_name:
        return None
    if len(normalized_name) > 80:
        return None
    return normalized_name

def get_stop_lines(raw_stop_id):
    cached = cache_get("stop_lines", str(raw_stop_id), 600)
    if cached is not None:
        return cached

    import sqlite3

    conn = sqlite3.connect(gtfs_manager.db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT r.route_short_name
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        WHERE st.stop_id = ?
        ORDER BY r.route_short_name
        """,
        (raw_stop_id,),
    )
    lines = [row[0] for row in cursor.fetchall() if should_show_line_now(row[0])]
    conn.close()
    override_lines = [
        line
        for line in override_lines_for_stop(str(raw_stop_id))
        if should_show_line_now(line)
    ]
    result = sorted(set([*lines, *override_lines]), key=lambda line: normalize_text(str(line)))
    cache_set("stop_lines", str(raw_stop_id), result, 600)
    return result

TRAM_LINES = {"2", "3", "5", "6", "7", "9", "10", "11", "12", "13", "14"}

def get_station_mode(lines):
    normalized_lines = {str(line or "").strip().upper() for line in (lines or []) if str(line or "").strip()}
    if not normalized_lines:
        return "bus"

    line_bases = {re.match(r"^\d+", line).group(0) if re.match(r"^\d+", line) else line for line in normalized_lines}
    has_tram = any(line in TRAM_LINES for line in line_bases)
    has_bus = any(line not in TRAM_LINES for line in line_bases)

    if has_tram and has_bus:
        return "mixed"
    return "tram" if has_tram else "bus"

def enrich_stop_modes(stop):
    lines = stop.get("lines")
    if lines is None:
        lines = get_stop_lines(str(stop["stop_id"]))

    return {
        **stop,
        "lines": lines,
        "station_mode": get_station_mode(lines),
    }

def public_station_id(stop_id):
    raw_stop_id_value = str(stop_id or '').strip()
    if raw_stop_id_value.isdigit() and int(raw_stop_id_value) >= 20000:
        return str(int(raw_stop_id_value) - 20000)
    return raw_stop_id_value

def get_stop_record(raw_stop_id):
    import sqlite3

    conn = sqlite3.connect(gtfs_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE stop_id = ?",
        (str(raw_stop_id),),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None

    return {
        "stop_id": str(row["stop_id"]),
        "station_id": public_station_id(row["stop_id"]),
        "stop_name": row["stop_name"],
        "stop_lat": row["stop_lat"],
        "stop_lon": row["stop_lon"],
    }

def distance_meters(lat1, lon1, lat2, lon2):
    radius = 6371000
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return round(radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

def normalize_journey_stop(stop, distance=None):
    payload = {
        **stop,
        "stop_id": str(stop["stop_id"]),
        "station_id": public_station_id(stop["stop_id"]),
        "name": stop.get("stop_name"),
    }
    if distance is not None:
        payload["distance"] = distance
    return enrich_stop_modes(payload)

def find_walk_expanded_routes_between_stations(origin_station, dest_station, origin_radius=1250, destination_radius=350):
    origin_stop = get_stop_record(origin_station['raw_stop_id'])
    destination_stop = get_stop_record(dest_station['raw_stop_id'])
    if not origin_stop or not destination_stop:
        return []

    origin_lat = float(origin_stop["stop_lat"])
    origin_lon = float(origin_stop["stop_lon"])
    destination_lat = float(destination_stop["stop_lat"])
    destination_lon = float(destination_stop["stop_lon"])

    origin_candidates = [
        normalize_journey_stop(stop, distance=stop.get("distance"))
        for stop in gtfs_manager.get_stops_nearby(origin_lat, origin_lon, origin_radius)
    ]
    origin_candidates = [
        stop for stop in origin_candidates
        if stop.get("lines")
    ][:24]

    destination_candidates = []
    seen_destination_ids = set()

    def add_destination_candidate(stop):
        stop_id = str(stop["stop_id"])
        if stop_id in seen_destination_ids:
            return
        seen_destination_ids.add(stop_id)
        walk_distance = distance_meters(destination_lat, destination_lon, stop["stop_lat"], stop["stop_lon"])
        destination_candidates.append(normalize_journey_stop(stop, distance=walk_distance))

    add_destination_candidate(destination_stop)
    for stop in gtfs_manager.get_stops_nearby(destination_lat, destination_lon, destination_radius):
        add_destination_candidate(stop)
    destination_candidates = sorted(
        destination_candidates,
        key=lambda stop: (
            float(stop["distance"]) if stop.get("distance") is not None else 9999,
            len(stop.get("lines") or []) * -1,
        ),
    )[:10]

    if not origin_candidates or not destination_candidates:
        return []

    origin_by_stop_id = {str(stop["stop_id"]): stop for stop in origin_candidates}
    destination_by_stop_id = {str(stop["stop_id"]): stop for stop in destination_candidates}

    expanded_routes = []

    def append_expanded_route(route, origin_candidate, destination_candidate):
        origin_walk = float(origin_candidate.get("distance") or 0)
        destination_walk = float(destination_candidate.get("distance") or 0)
        reaches_requested_destination = str(route.get("dest_station_id") or "") == str(dest_station["sid"])
        starts_at_requested_origin = str(route.get("origin_station_id") or "") == str(origin_station["sid"])
        reaches_matching_destination_name = (
            normalize_text(destination_candidate.get("stop_name") or "")
            == normalize_text(destination_stop.get("stop_name") or "")
        )
        if route.get("type") == "direct" and reaches_requested_destination:
            priority_bucket = 0
        elif route.get("type") == "direct":
            priority_bucket = 1
        elif route.get("type") == "multi_transfer" and starts_at_requested_origin and reaches_requested_destination:
            priority_bucket = 2
        elif reaches_requested_destination:
            priority_bucket = 3
        elif reaches_matching_destination_name:
            priority_bucket = 4
        else:
            priority_bucket = 5
        stops_count = int(route.get("stops_count") or 0)
        transfer_penalty = 10000 if route.get("type") == "transfer" else 16000 if route.get("type") == "multi_transfer" else 0
        route_score = transfer_penalty + (stops_count * 10) + ((origin_walk + destination_walk) / 8)
        expanded_routes.append({
            **route,
            "from_station_name": origin_candidate["stop_name"],
            "from_station_id": origin_candidate["station_id"],
            "from_stop_lat": origin_candidate["stop_lat"],
            "from_stop_lon": origin_candidate["stop_lon"],
            "from_stop_distance": origin_walk,
            "from_stop_line_count": len(origin_candidate.get("lines") or []),
            "to_station_name": destination_candidate["stop_name"],
            "to_station_id": destination_candidate["station_id"],
            "to_stop_lat": destination_candidate["stop_lat"],
            "to_stop_lon": destination_candidate["stop_lon"],
            "to_stop_distance": destination_walk,
            "to_stop_line_count": len(destination_candidate.get("lines") or []),
            "origin_walk_m": origin_walk,
            "destination_walk_m": destination_walk,
            "priority_bucket": priority_bucket,
            "route_score": route_score,
            "journey_score": route_score,
            "source": route.get("source") or "walk_expanded",
            "requested_origin_station_id": origin_station["sid"],
            "requested_dest_station_id": dest_station["sid"],
        })

    direct_routes = gtfs_manager.find_direct_routes_between_stop_sets(
        [stop["stop_id"] for stop in origin_candidates],
        [stop["stop_id"] for stop in destination_candidates],
    )
    for route in direct_routes:
        origin_candidate = origin_by_stop_id.get(str(route.get("origin_stop_id")))
        destination_candidate = destination_by_stop_id.get(str(route.get("dest_stop_id")))
        if origin_candidate and destination_candidate:
            append_expanded_route(route, origin_candidate, destination_candidate)

    multi_transfer_routes = gtfs_manager.find_two_transfer_routes_between_stops(
        origin_station["raw_stop_id"],
        dest_station["raw_stop_id"],
        expand_nearby=False,
    )
    for route in multi_transfer_routes:
        origin_candidate = origin_by_stop_id.get(str(route.get("origin_stop_id")))
        destination_candidate = destination_by_stop_id.get(str(route.get("dest_stop_id")))
        if origin_candidate and destination_candidate:
            append_expanded_route(route, origin_candidate, destination_candidate)

    if len(expanded_routes) < 4:
        route_cache = {}
        for origin_candidate in origin_candidates[:16]:
            for destination_candidate in destination_candidates[:4]:
                if origin_candidate["station_id"] == destination_candidate["station_id"]:
                    continue
                route_key = (origin_candidate["stop_id"], destination_candidate["stop_id"])
                routes = route_cache.get(route_key)
                if routes is None:
                    routes = gtfs_manager.find_routes_between_stops(*route_key, expand_nearby=False)
                    route_cache[route_key] = routes
                for route in routes:
                    if route.get("type") != "transfer":
                        continue
                    append_expanded_route(route, origin_candidate, destination_candidate)

    seen_routes = set()
    deduped_routes = []
    for route in sorted(expanded_routes, key=lambda item: (
        int(item.get("priority_bucket") or 0),
        float(item.get("route_score") or 0),
        str(item.get("line") or item.get("line1") or ""),
    )):
        key = (
            route.get("type"),
            route.get("line") if route.get("type") == "direct" else (route.get("line1"), route.get("line2")),
            route.get("origin_station_id"),
            route.get("transfer_station_id"),
            route.get("dest_station_id"),
        )
        if key in seen_routes:
            continue
        seen_routes.add(key)
        deduped_routes.append(route)
        if len(deduped_routes) >= 12:
            break

    return deduped_routes

def strip_address_number(query):
    value = str(query or '').strip()
    without_numbers = re.sub(r"\b\d+[a-zA-Z]?\b", " ", value)
    return re.sub(r"\s+", " ", without_numbers).strip(" ,.-")

def address_fallback_queries(query):
    candidates = []
    for candidate in (
        strip_address_number(query),
        re.sub(r"\b(ulica|street|bulevar|boulevard|avenija|avenue)\b", " ", str(query or ""), flags=re.IGNORECASE),
        re.sub(r"\b\d+[a-zA-Z]?\b", " ", str(query or "")),
    ):
        normalized = re.sub(r"\s+", " ", str(candidate or "")).strip(" ,.-")
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates

def geocode_address(address):
    query = str(address or '').strip()
    if not query:
        return None
    if not re.search(r"\d+", query):
        return None

    cache_key = normalize_text(query)
    cached = cache_get("geocode_address", cache_key, 3600)
    if cached is not None:
        if cached.get("not_found"):
            return None
        return cached

    localized_query = query
    query_lower = query.lower()
    if not any(token in query_lower for token in ("beograd", "belgrade", "srbija", "serbia")):
        localized_query = f"{query}, Beograd, Srbija"

    def fetch_nominatim(params):
        encoded_params = urllib.parse.urlencode({
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
            "bounded": 1,
            "viewbox": GEOCODING_VIEWBOX,
            **params,
        })
        request_url = f"https://nominatim.openstreetmap.org/search?{encoded_params}"
        req = urllib.request.Request(
            request_url,
            headers={"User-Agent": "BusTrackerBG/1.0 (address search)"},
        )

        with urllib.request.urlopen(req, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    payload = []
    geocode_attempts = [
        {"q": localized_query},
        {"street": query, "city": "Beograd", "country": "Srbija"},
    ]

    for params in geocode_attempts:
        try:
            payload.extend(fetch_nominatim(params))
        except Exception as error:
            logging.warning("Address geocoding failed for %r with %s: %s", query, params, error)

    if not payload:
        cache_set("geocode_address", cache_key, {"not_found": True}, 900)
        return None

    has_number = bool(re.search(r"\d+", query))
    requested_numbers = set(re.findall(r"\d+[a-zA-Z]?", query))
    query_tokens = [
        token for token in re.split(r"[\s,]+", normalize_text(query))
        if len(token) >= 3 and not token.isdigit()
    ]

    def result_score(item):
        label = normalize_text(item.get("display_name") or "")
        item_type = item.get("type") or ""
        item_class = item.get("class") or ""
        address = item.get("address") or {}
        city = normalize_text(
            address.get("city")
            or address.get("town")
            or address.get("municipality")
            or address.get("village")
            or ""
        )
        suburb = normalize_text(address.get("suburb") or "")
        road = normalize_text(
            address.get("road")
            or address.get("pedestrian")
            or address.get("footway")
            or address.get("path")
            or address.get("neighbourhood")
            or ""
        )
        house_number = str(address.get("house_number") or "")
        token_matches = sum(1 for token in query_tokens if token in label or token in road)

        if query_tokens and token_matches == 0:
            return -1
        if not has_number and item_class not in {"highway", "place"} and item_type not in {
            "road", "street", "pedestrian", "footway", "path", "residential", "tertiary",
            "secondary", "primary", "unclassified", "neighbourhood",
        }:
            return -1

        house_number_matches = house_number and normalize_text(house_number) in {
            normalize_text(number) for number in requested_numbers
        }

        return (
            token_matches * 10
            + (12 if house_number_matches else 0)
            + (4 if house_number else 0)
            + (8 if city == "beograd" else 0)
            + (4 if "beograd" in suburb else 0)
            - (4 if address.get("village") else 0)
            - (3 if address.get("town") else 0)
            + (4 if item_class == "highway" else 0)
            + (2 if road else 0)
            + (float(item.get("importance") or 0) * 100)
            - len(label) / 1000
        )

    scored_results = sorted(
        ((result_score(item), item) for item in payload),
        key=lambda entry: entry[0],
        reverse=True,
    )
    result = scored_results[0][1] if scored_results and scored_results[0][0] >= 0 else None
    if not result:
        cache_set("geocode_address", cache_key, {"not_found": True}, 900)
        return None

    try:
        lat = float(result["lat"])
        lon = float(result["lon"])
    except (KeyError, TypeError, ValueError):
        cache_set("geocode_address", cache_key, {"not_found": True}, 900)
        return None
    if not is_in_belgrade_bounds(lat, lon):
        cache_set("geocode_address", cache_key, {"not_found": True}, 900)
        return None

    resolved = {
        "lat": lat,
        "lon": lon,
        "label": result.get("display_name") or localized_query,
        "type": result.get("type"),
    }
    cache_set("geocode_address", cache_key, resolved, 3600)
    return resolved

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Token is missing!'}), 401
            
        token = auth_header.split(' ')[1]
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            # Validate user exists
            current_user = app_data_manager.get_api_user(data['username'])
            if not current_user:
                return jsonify({'error': 'Invalid Token!'}), 401
            password_changed_at = current_user.get('password_changed_at') or ''
            if password_changed_at and data.get('pwd_changed_at') != password_changed_at:
                return jsonify({'error': 'Invalid Token!'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid Token!'}), 401
        except Exception:
            # Avoid leaking raw exception strings to the client
            return jsonify({'error': 'Authentication failed!'}), 401

        return f(current_user, *args, **kwargs)

    return decorated

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/health', methods=['GET'])
def health_check():
    uptime = time.time() - START_TIME
    return jsonify({
        'status': 'ok',
        'uptime_seconds': uptime,
        'server_time': datetime.datetime.now().isoformat()
    }), 200

@app.route('/api/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Username and password required'}), 400

    username = str(data['username']).strip()
    password = str(data['password'])
    if len(username) < 3 or len(username) > 64:
        return jsonify({'error': 'Username must be between 3 and 64 characters'}), 400
    password_error = validate_new_password(password)
    if password_error:
        return jsonify({'error': password_error}), 400
    
    # Hash password
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    
    success = app_data_manager.register_api_user(username, hashed.decode('utf-8'))
    if success:
        return jsonify({'message': 'User registered successfully'}), 201
    else:
        return jsonify({'error': 'Username already taken'}), 409

@app.route('/api/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Missing credentials'}), 400

    username = str(data['username']).strip()
    password = str(data['password'])
    
    user = app_data_manager.get_api_user(username)
    if not user:
        return jsonify({'error': 'Invalid username or password'}), 401
        
    if bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
        # Generate JWT
        token = jwt.encode({
            'user_id': user['id'],
            'username': user['username'],
            'pwd_changed_at': user.get('password_changed_at') or '',
            'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        
        return jsonify({'token': token})
    
    return jsonify({'error': 'Invalid username or password'}), 401

@app.route('/api/password-reset/request', methods=['POST'])
@limiter.limit("5 per hour")
def request_password_reset():
    data = request.get_json()
    if not data or not data.get('username'):
        return jsonify({'error': 'Username required'}), 400

    username = str(data['username']).strip()
    user = app_data_manager.get_api_user(username)
    response = {
        'message': 'If that account exists, a password reset token was generated.'
    }

    if not user:
        return jsonify(response), 200

    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.datetime.now() + datetime.timedelta(minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES)
    ).isoformat()
    app_data_manager.create_password_reset_token(user['id'], hash_reset_token(token), expires_at)

    if IS_LOCAL_DEV:
        logging.warning(
            "Password reset token for username '%s' expires in %s minutes: %s",
            username,
            PASSWORD_RESET_TOKEN_TTL_MINUTES,
            token,
        )
        response['reset_token'] = token
    else:
        logging.info("Password reset token generated for user id %s.", user['id'])

    return jsonify(response), 200

@app.route('/api/password-reset/confirm', methods=['POST'])
@limiter.limit("5 per hour")
def confirm_password_reset():
    data = request.get_json()
    if not data or not data.get('token') or not data.get('password'):
        return jsonify({'error': 'Reset token and password required'}), 400

    token = str(data['token']).strip()
    password = str(data['password'])
    password_error = validate_new_password(password)
    if password_error:
        return jsonify({'error': password_error}), 400

    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    reset_record = app_data_manager.reset_password_with_token(
        hash_reset_token(token),
        datetime.datetime.now().isoformat(),
        hashed.decode('utf-8'),
    )
    if not reset_record:
        return jsonify({'error': 'Reset token is invalid or expired'}), 400

    return jsonify({'message': 'Password updated successfully'}), 200

@app.route('/api/search', methods=['GET'])
@limiter.limit("60 per minute")
def search():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'Query parameter q is required'}), 400
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        return jsonify({'error': f'Query must be at most {MAX_SEARCH_QUERY_LENGTH} characters'}), 400

    cache_key = normalize_text(query)
    cached = cache_get("stop_search", cache_key, 300)
    if cached is not None:
        return jsonify(cached)

    matches = [
        enrich_stop_modes({
            'id': stop['stop_id'],
            'station_id': str(int(stop['stop_id']) - 20000) if str(stop['stop_id']).isdigit() and int(stop['stop_id']) >= 20000 else str(stop['stop_id']),
            'name': stop['stop_name'],
            'stop_id': stop['stop_id'],
            'stop_name': stop['stop_name'],
            'stop_lat': stop.get('stop_lat'),
            'stop_lon': stop.get('stop_lon'),
        })
        for stop in gtfs_manager.resolve_stop_name(query)
    ]

    payload = {'matches': matches}
    cache_set("stop_search", cache_key, payload, 300)
    return jsonify(payload)

@app.route('/api/search/address', methods=['GET'])
@limiter.limit("20 per minute")
def search_by_address():
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'Query parameter q is required'}), 400
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        return jsonify({'error': f'Query must be at most {MAX_SEARCH_QUERY_LENGTH} characters'}), 400

    try:
        radius = min(max(float(request.args.get('radius', 650)), 100), ADDRESS_SEARCH_MAX_RADIUS_M)
    except ValueError:
        return jsonify({'error': 'radius must be a number'}), 400
    if not math.isfinite(radius):
        return jsonify({'error': 'radius must be a number'}), 400

    response_cache_key = (normalize_text(query), radius)
    cached = cache_get("address_search", response_cache_key, 300)
    if cached is not None:
        return jsonify(cached)

    location = geocode_address(query)
    if not location:
        fallback_stops = []
        fallback_query = ""
        for candidate in address_fallback_queries(query):
            fallback_stops = gtfs_manager.resolve_stop_name(candidate)
            if fallback_stops:
                fallback_query = candidate
                break
        if not fallback_stops:
            return jsonify({'error': 'Address was not found'}), 404

        stops = [
            enrich_stop_modes({
                **stop,
                "distance": None,
            })
            for stop in fallback_stops[:8]
        ]
        payload = {
            'address': query,
            'resolved_address': fallback_query,
            'lat': None,
            'lon': None,
            'radius': radius,
            'stops': stops,
            'source': 'stop_name_fallback',
        }
        cache_set("address_search", response_cache_key, payload, 300)
        return jsonify(payload)

    stops = [
        enrich_stop_modes(stop)
        for stop in gtfs_manager.get_stops_nearby(
            location["lat"],
            location["lon"],
            radius,
            limit=ADDRESS_SEARCH_MAX_RESULTS,
        )
    ]
    if not stops:
        return jsonify({'error': 'No stops were found near that address'}), 404

    payload = {
        'address': query,
        'resolved_address': location["label"],
        'lat': location["lat"],
        'lon': location["lon"],
        'radius': radius,
        'stops': stops,
    }
    cache_set("address_search", response_cache_key, payload, 300)
    return jsonify(payload)

@app.route('/api/arrivals', methods=['GET'])
def arrivals():
    station_id = request.args.get('station_id')
    lines_param = request.args.get('lines')
    
    if not station_id:
        return jsonify({'error': 'station_id is required'}), 400
        
    target_lines = None
    if lines_param:
        target_lines = [line.strip() for line in lines_param.split(',')]
        
    predicted_arrivals = gtfs_manager.predict_arrivals_at_stop(station_id, target_lines)
    if predicted_arrivals and 'error' in predicted_arrivals[0]:
        return jsonify({'error': predicted_arrivals[0]['error']}), 404

    return jsonify({
        'station_id': station_id,
        'predicted_arrivals': predicted_arrivals
    })

@app.route('/api/timetable', methods=['GET'])
def timetable():
    line = request.args.get('line')
    if not line:
        return jsonify({'error': 'line parameter is required'}), 400
        
    result_text = gtfs_manager.get_timetable(line)
    return jsonify({
        'line': line,
        'timetable_text': result_text
    })

@app.route('/api/predict/line', methods=['GET'])
def predict_line():
    line = request.args.get('line')
    if not line:
        return jsonify({'error': 'line parameter is required'}), 400
        
    predictions = gtfs_manager.predict_bus_position(line)
    active_buses = [
        prediction for prediction in predictions
        if prediction.get("status") == "in_transit"
    ]
    scheduled_trips = [
        prediction for prediction in predictions
        if prediction.get("status") == "not_started"
    ][:20]
    return jsonify({
        'line': line,
        'active_buses': active_buses,
        'scheduled_trips': scheduled_trips,
    })

@app.route('/api/predict/stop', methods=['GET'])
def predict_stop():
    station_id = request.args.get('station_id')
    lines_param = request.args.get('lines')
    
    if not station_id:
        return jsonify({'error': 'station_id is required'}), 400
        
    target_lines = None
    if lines_param:
        target_lines = [line.strip() for line in lines_param.split(',')]
        
    arrivals = gtfs_manager.predict_arrivals_at_stop(station_id, target_lines)
    return jsonify({
        'station_id': station_id,
        'predicted_arrivals': arrivals
    })

@app.route('/api/route', methods=['GET'])
def get_route():
    line = request.args.get('line')
    station_id = (request.args.get('station_id') or request.args.get('stop_id') or '').strip()
    if not line:
        return jsonify({'error': 'line parameter is required'}), 400

    stop_id = None
    if station_id:
        station = resolve_station_id(station_id)
        if not station:
            return jsonify({'error': 'Station does not exist'}), 404
        stop_id = station['raw_stop_id']

    route_data = gtfs_manager.get_line_route(line, stop_id=stop_id)
    return jsonify({
        'line': line,
        'station_id': station_id or None,
        'directions': route_data
    })

@app.route('/api/routing', methods=['GET'])
@limiter.limit("60 per minute")
def find_routing():
    origin = request.args.get('from')
    dest = request.args.get('to')
    
    if not origin or not dest:
        return jsonify({'error': 'from and to parameters are required'}), 400

    origin_station = resolve_station_id(origin)
    dest_station = resolve_station_id(dest)
    if not origin_station or not dest_station:
        return jsonify({'error': 'Station does not exist'}), 404

    strict_stops = str(request.args.get('strict_stops') or '').lower() in {'1', 'true', 'yes'}
    cache_key = routing_cache_key(origin_station['sid'], dest_station['sid'], strict_stops)
    cached_payload = cache_get("routing", cache_key, ROUTING_CACHE_TTL_SECONDS)
    if cached_payload is not None:
        return jsonify(cached_payload)

    routes = gtfs_manager.find_routes_between_stops(
        origin_station['raw_stop_id'],
        dest_station['raw_stop_id'],
        expand_nearby=not strict_stops,
    )
    if not routes and not strict_stops:
        routes = find_walk_expanded_routes_between_stations(origin_station, dest_station)
    payload = {
        'from': origin_station['sid'],
        'to': dest_station['sid'],
        'possible_routes': routes
    }
    cache_set("routing", cache_key, payload, ROUTING_CACHE_TTL_SECONDS)
    return jsonify(payload)

@app.route('/api/routing/batch', methods=['POST'])
@limiter.limit("20 per minute")
def find_routing_batch():
    data = request.get_json(silent=True) or {}
    pairs = data.get('pairs') or []
    if not isinstance(pairs, list):
        return jsonify({'error': 'pairs must be a list'}), 400

    strict_stops = bool(data.get('strict_stops'))
    pairs = pairs[:MAX_ROUTING_BATCH_PAIRS]
    resolved_cache = {}
    route_cache = {}
    results = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue

        origin = str(pair.get('from') or '').strip()
        dest = str(pair.get('to') or '').strip()
        if not origin or not dest or origin == dest:
            continue

        origin_station = resolved_cache.get(origin)
        if origin_station is None:
            origin_station = resolve_station_id(origin)
            resolved_cache[origin] = origin_station

        dest_station = resolved_cache.get(dest)
        if dest_station is None:
            dest_station = resolve_station_id(dest)
            resolved_cache[dest] = dest_station

        if not origin_station or not dest_station:
            results.append({
                'from': origin,
                'to': dest,
                'possible_routes': [],
                'error': 'Station does not exist',
            })
            continue

        route_key = (origin_station['sid'], dest_station['sid'], strict_stops)
        routes = route_cache.get(route_key)
        if routes is None:
            cache_key = routing_cache_key(origin_station['sid'], dest_station['sid'], strict_stops)
            cached_payload = cache_get("routing", cache_key, ROUTING_CACHE_TTL_SECONDS)
            if cached_payload is not None:
                routes = cached_payload.get('possible_routes', [])
                route_cache[route_key] = routes
            else:
                routes = gtfs_manager.find_routes_between_stops(
                    origin_station['raw_stop_id'],
                    dest_station['raw_stop_id'],
                    expand_nearby=not strict_stops,
                )
                if not routes and not strict_stops:
                    routes = find_walk_expanded_routes_between_stations(origin_station, dest_station)
                cache_set("routing", cache_key, {
                    'from': origin_station['sid'],
                    'to': dest_station['sid'],
                    'possible_routes': routes,
                }, ROUTING_CACHE_TTL_SECONDS)
            route_cache[route_key] = routes

        results.append({
            'from': origin_station['sid'],
            'to': dest_station['sid'],
            'possible_routes': routes,
        })

    return jsonify({'results': results})

@app.route('/api/journey', methods=['POST'])
@limiter.limit("20 per minute")
def find_journey():
    data = request.get_json(silent=True) or {}
    origin = data.get('origin') or {}
    destination = data.get('destination') or {}

    try:
        origin_lat = float(origin.get('lat'))
        origin_lon = float(origin.get('lon'))
        origin_radius = min(max(float(data.get('origin_radius', 1250)), 100), 1500)
        destination_radius = min(max(float(data.get('destination_radius', 350)), 100), 1000)
    except (TypeError, ValueError):
        return jsonify({'error': 'origin.lat, origin.lon, origin_radius, and destination_radius must be valid numbers'}), 400

    if not all(math.isfinite(value) for value in (origin_lat, origin_lon, origin_radius, destination_radius)):
        return jsonify({'error': 'origin.lat, origin.lon, origin_radius, and destination_radius must be finite numbers'}), 400
    if not is_in_belgrade_bounds(origin_lat, origin_lon):
        return jsonify({'error': 'origin coordinates are outside the supported Belgrade area'}), 400

    destination_station_id = str(destination.get('station_id') or '').strip()
    if not destination_station_id:
        return jsonify({'error': 'destination.station_id is required'}), 400

    destination_station = resolve_station_id(destination_station_id)
    if not destination_station:
        return jsonify({'error': 'Destination station does not exist'}), 404

    destination_stop = get_stop_record(destination_station['raw_stop_id'])
    if not destination_stop:
        return jsonify({'error': 'Destination station does not exist'}), 404

    origin_candidates = [
        normalize_journey_stop(stop, distance=stop.get("distance"))
        for stop in gtfs_manager.get_stops_nearby(origin_lat, origin_lon, origin_radius)
    ]
    origin_candidates = [
        stop for stop in origin_candidates
        if stop.get("lines")
    ][:60]
    if not origin_candidates:
        return jsonify({'journeys': [], 'message': 'No nearby origin stops found'})

    destination_lat = destination.get('lat')
    destination_lon = destination.get('lon')
    try:
        destination_lat = float(destination_lat) if destination_lat is not None else float(destination_stop["stop_lat"])
        destination_lon = float(destination_lon) if destination_lon is not None else float(destination_stop["stop_lon"])
    except (TypeError, ValueError):
        destination_lat = float(destination_stop["stop_lat"])
        destination_lon = float(destination_stop["stop_lon"])

    if not math.isfinite(destination_lat) or not math.isfinite(destination_lon):
        return jsonify({'error': 'destination coordinates must be finite numbers'}), 400
    if not is_in_belgrade_bounds(destination_lat, destination_lon):
        return jsonify({'error': 'destination coordinates are outside the supported Belgrade area'}), 400

    destination_candidates = []
    seen_destination_ids = set()

    def add_destination_candidate(stop):
        stop_id = str(stop["stop_id"])
        if stop_id in seen_destination_ids:
            return
        seen_destination_ids.add(stop_id)
        walk_distance = distance_meters(destination_lat, destination_lon, stop["stop_lat"], stop["stop_lon"])
        destination_candidates.append(normalize_journey_stop(stop, distance=walk_distance))

    add_destination_candidate(destination_stop)
    for stop in gtfs_manager.get_stops_nearby(destination_lat, destination_lon, destination_radius):
        add_destination_candidate(stop)
    destination_candidates = sorted(
        destination_candidates,
        key=lambda stop: (
            float(stop["distance"]) if stop.get("distance") is not None else 9999,
            len(stop.get("lines") or []) * -1,
        ),
    )[:10]

    journeys = []
    origin_by_stop_id = {str(stop["stop_id"]): stop for stop in origin_candidates}
    destination_by_stop_id = {str(stop["stop_id"]): stop for stop in destination_candidates}

    def append_journey(route, origin_stop, destination_candidate):
        stops_count = int(route.get("stops_count") or 0)
        origin_walk = float(origin_stop.get("distance") or 0)
        destination_walk = float(destination_candidate.get("distance") or 0)
        reaches_requested_destination = str(route.get("dest_station_id") or "") == str(destination_station["sid"])
        reaches_matching_destination_name = (
            normalize_text(destination_candidate.get("stop_name") or "")
            == normalize_text(destination_stop.get("stop_name") or "")
        )
        if route.get("type") == "direct" and reaches_requested_destination and origin_walk <= 1250:
            priority_bucket = 0
        elif route.get("type") == "direct" and origin_walk <= 1250:
            priority_bucket = 1
        elif route.get("type") == "direct":
            priority_bucket = 2
        elif reaches_requested_destination:
            priority_bucket = 3
        elif reaches_matching_destination_name:
            priority_bucket = 4
        else:
            priority_bucket = 5
        transfer_penalty = 10000 if route.get("type") == "transfer" else 0
        journey_score = transfer_penalty + (stops_count * 10) + ((origin_walk + destination_walk) / 8)
        journeys.append({
            **route,
            "from_station_name": origin_stop["stop_name"],
            "from_station_id": origin_stop["station_id"],
            "from_stop_lat": origin_stop["stop_lat"],
            "from_stop_lon": origin_stop["stop_lon"],
            "from_stop_distance": origin_walk,
            "from_stop_line_count": len(origin_stop.get("lines") or []),
            "to_station_name": destination_candidate["stop_name"],
            "to_station_id": destination_candidate["station_id"],
            "to_stop_lat": destination_candidate["stop_lat"],
            "to_stop_lon": destination_candidate["stop_lon"],
            "to_stop_distance": destination_walk,
            "to_stop_line_count": len(destination_candidate.get("lines") or []),
            "origin_walk_m": origin_walk,
            "destination_walk_m": destination_walk,
            "priority_bucket": priority_bucket,
            "route_score": journey_score,
            "journey_score": journey_score,
        })

    direct_routes = gtfs_manager.find_direct_routes_between_stop_sets(
        [stop["stop_id"] for stop in origin_candidates],
        [stop["stop_id"] for stop in destination_candidates],
    )
    for route in direct_routes:
        origin_stop = origin_by_stop_id.get(str(route.get("origin_stop_id")))
        destination_candidate = destination_by_stop_id.get(str(route.get("dest_stop_id")))
        if origin_stop and destination_candidate:
            append_journey(route, origin_stop, destination_candidate)

    if len(journeys) < 4:
        route_cache = {}
        transfer_origins = origin_candidates[:20]
        transfer_destinations = destination_candidates[:4]
        for origin_stop in transfer_origins[:16]:
            for destination_candidate in transfer_destinations:
                if origin_stop["station_id"] == destination_candidate["station_id"]:
                    continue

                route_key = (origin_stop["stop_id"], destination_candidate["stop_id"])
                routes = route_cache.get(route_key)
                if routes is None:
                    routes = gtfs_manager.find_routes_between_stops(*route_key, expand_nearby=False)
                    route_cache[route_key] = routes

                for route in routes:
                    if route.get("type") != "transfer":
                        continue
                    append_journey(route, origin_stop, destination_candidate)

    has_direct_journey = any(journey.get("type") == "direct" for journey in journeys)
    if has_direct_journey:
        best_direct_score = min(
            float(journey.get("journey_score") or 0)
            for journey in journeys
            if journey.get("type") == "direct"
        )
        journeys = [
            journey for journey in journeys
            if (
                journey.get("type") == "direct"
                or float(journey.get("journey_score") or 0) <= best_direct_score + 2500
            )
        ]

    seen_journeys = set()
    deduped_journeys = []
    for journey in sorted(journeys, key=lambda item: (
        int(item.get("priority_bucket") or 0),
        float(item.get("journey_score") or 0),
        str(item.get("line") or item.get("line1") or ""),
    )):
        key = (
            journey.get("type"),
            journey.get("line") if journey.get("type") == "direct" else (journey.get("line1"), journey.get("line2")),
            journey.get("from_station_id"),
            journey.get("transfer_station_id"),
            journey.get("to_station_id"),
        )
        if key in seen_journeys:
            continue
        seen_journeys.add(key)
        deduped_journeys.append(journey)
        if len(deduped_journeys) >= 12:
            break

    return jsonify({
        'origin': {'lat': origin_lat, 'lon': origin_lon, 'radius': origin_radius},
        'destination': {
            'station_id': destination_station['sid'],
            'lat': destination_lat,
            'lon': destination_lon,
            'radius': destination_radius,
        },
        'origin_candidates': origin_candidates,
        'destination_candidates': destination_candidates,
        'journeys': deduped_journeys,
    })

@app.route('/api/stops/nearby', methods=['GET'])
@limiter.limit("60 per minute")
def stops_nearby():
    try:
        lat = float(request.args['lat'])
        lon = float(request.args['lon'])
        radius = float(request.args.get('radius', 500))
    except ValueError:
        return jsonify({'error': 'Invalid coordinates or radius'}), 400
    except KeyError:
        return jsonify({'error': 'lat and lon are required'}), 400

    if not math.isfinite(lat) or not math.isfinite(lon) or not math.isfinite(radius):
        return jsonify({'error': 'Invalid coordinates or radius'}), 400
    if not is_in_belgrade_bounds(lat, lon):
        return jsonify({'error': 'Coordinates are outside the supported Belgrade area'}), 400
    if radius < 1:
        return jsonify({'error': 'radius must be greater than 0'}), 400
    radius = min(radius, PUBLIC_NEARBY_MAX_RADIUS_M)

    stops = [
        enrich_stop_modes(stop)
        for stop in gtfs_manager.get_stops_nearby(
            lat,
            lon,
            radius,
            limit=PUBLIC_NEARBY_MAX_RESULTS,
        )
    ]
    return jsonify({
        'lat': lat,
        'lon': lon,
        'radius': radius,
        'stops': stops
    })

@app.route('/api/stops', methods=['GET'])
def get_all_stops():
    """Return stop lookup data from the GTFS database (local gtfs.db only)."""
    import sqlite3

    try:
        limit = min(max(int(request.args.get('limit', 200)), 1), 500)
        offset = max(int(request.args.get('offset', 0)), 0)
    except ValueError:
        return jsonify({'error': 'limit and offset must be integers'}), 400

    requested_stop_id = (request.args.get('stop_id') or request.args.get('station_id') or '').strip()

    conn = sqlite3.connect(gtfs_manager.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if requested_stop_id:
        lookup_ids = [requested_stop_id]
        if requested_stop_id.isdigit() and int(requested_stop_id) < 20000:
            lookup_ids.append(str(int(requested_stop_id) + 20000))

        placeholders = ', '.join(['?'] * len(lookup_ids))
        cursor.execute(
            f"SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE stop_id IN ({placeholders}) ORDER BY stop_id",
            lookup_ids,
        )
        rows = cursor.fetchall()
        conn.close()

        stops = [
            enrich_stop_modes({
                "stop_id": str(row["stop_id"]),
                "stop_name": row["stop_name"],
                "stop_lat": row["stop_lat"],
                "stop_lon": row["stop_lon"],
            })
            for row in rows
        ]
        return jsonify({
            'total': len(stops),
            'stops': stops,
        })

    cursor.execute("SELECT COUNT(*) FROM stops")
    total = cursor.fetchone()[0]
    cursor.execute(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops ORDER BY stop_id LIMIT ? OFFSET ?",
        (limit, offset)
    )
    rows = cursor.fetchall()
    conn.close()

    stops = [
        {
            "stop_id": str(row["stop_id"]),
            "stop_name": row["stop_name"],
            "stop_lat": row["stop_lat"],
            "stop_lon": row["stop_lon"],
        }
        for row in rows
    ]
    return jsonify({
        'total': total,
        'limit': limit,
        'offset': offset,
        'stops': stops,
    })

@app.route('/api/stops/connected', methods=['GET'])
def get_connected_stops():
    origin = (request.args.get('from') or request.args.get('station_id') or '').strip()
    query = (request.args.get('q') or '').strip()

    if not origin:
        return jsonify({'error': 'from parameter is required'}), 400

    origin_station = resolve_station_id(origin)
    if not origin_station:
        return jsonify({'error': 'Station does not exist'}), 404

    cache_key = (origin_station['raw_stop_id'], normalize_text(query))
    cached = cache_get("connected_stops", cache_key, 300)
    if cached is not None:
        return jsonify(cached)

    stops = gtfs_manager.get_connected_stops(origin_station['raw_stop_id'], query=query)
    payload = {
        'from': origin_station['sid'],
        'query': query,
        'stops': [
            {
                'stop_id': stop['stop_id'],
                'station_id': str(int(stop['stop_id']) - 20000) if str(stop['stop_id']).isdigit() and int(stop['stop_id']) >= 20000 else str(stop['stop_id']),
                'stop_name': stop['stop_name'],
                'stop_lat': stop['stop_lat'],
                'stop_lon': stop['stop_lon'],
                'shared_lines': stop['shared_lines'],
                'lines': stop['shared_lines'],
                'station_mode': get_station_mode(stop['shared_lines']),
            }
            for stop in stops
        ]
    }
    cache_set("connected_stops", cache_key, payload, 300)
    return jsonify(payload)

@app.route('/api/favorites', methods=['GET'])
@token_required
def get_favorites(current_user):
    # Preserve the existing owner-key format used by stored favorites.
    api_user_id = f"api_{current_user['id']}"
    
    favs = app_data_manager.get_favorites(api_user_id)
    # Format as list
    fav_list = []
    for fav_name, data in favs.items():
        fav_list.append({
            'name': fav_name,
            'station_uid': data['uid'],
            'station_id': data['sid'],
            'line': data.get('line')
        })
        
    return jsonify({'favorites': fav_list})

@app.route('/api/favorites', methods=['POST'])
@token_required
def add_favorite(current_user):
    data = request.get_json()
    if not data or not data.get('name') or not data.get('station_id'):
        return jsonify({'error': 'name and station_id are required'}), 400

    fav_name = normalize_favorite_name(data['name'])
    if not fav_name:
        return jsonify({'error': 'name must be between 1 and 80 characters'}), 400

    station = resolve_station_id(data['station_id'])
    if not station:
        return jsonify({'error': 'Station does not exist'}), 404

    line = str(data.get('line') or '').strip() or None
    if line and not line_exists(line):
        return jsonify({'error': 'Line does not exist'}), 404

    api_user_id = f"api_{current_user['id']}"
    
    sid = station['sid']
    uid = station['uid']
        
    saved_name = app_data_manager.save_favorite(api_user_id, fav_name, uid, sid, line)
    
    return jsonify({'message': 'Favorite saved successfully', 'favorite': {'name': saved_name, 'station_uid': uid, 'station_id': sid, 'line': line}}), 201

@app.route('/api/favorites/<name>', methods=['PUT'])
@token_required
def update_favorite(current_user, name):
    data = request.get_json()
    if not data or not data.get('name') or not data.get('station_id'):
        return jsonify({'error': 'name and station_id are required'}), 400

    fav_name = normalize_favorite_name(data['name'])
    if not fav_name:
        return jsonify({'error': 'name must be between 1 and 80 characters'}), 400

    station = resolve_station_id(data['station_id'])
    if not station:
        return jsonify({'error': 'Station does not exist'}), 404

    line = str(data.get('line') or '').strip() or None
    if line and not line_exists(line):
        return jsonify({'error': 'Line does not exist'}), 404

    api_user_id = f"api_{current_user['id']}"
    saved_name = app_data_manager.update_favorite(
        api_user_id,
        name,
        fav_name,
        station['uid'],
        station['sid'],
        line,
    )

    if not saved_name:
        return jsonify({'error': 'Favorite not found'}), 404

    return jsonify({'message': 'Favorite updated successfully', 'favorite': {'name': saved_name, 'station_uid': station['uid'], 'station_id': station['sid'], 'line': line}})

@app.route('/api/favorites/<name>', methods=['DELETE'])
@token_required
def remove_favorite(current_user, name):
    api_user_id = f"api_{current_user['id']}"
    success = app_data_manager.delete_favorite(api_user_id, name)
    if success:
        return jsonify({'message': 'Favorite deleted successfully'})
    else:
        return jsonify({'error': 'Favorite not found'}), 404

if __name__ == '__main__':
    from waitress import serve
    initialize_services()
    # Use a production-ready server
    print("\n" + "!" * 80)
    print("! SECURITY WARNING: This API is currently running over plain HTTP.")
    print("! For production deployments, you MUST use a reverse proxy (like Nginx/Caddy)")
    print("! with an SSL certificate to enable HTTPS and prevent credential theft.")
    print("!" * 80 + "\n")
    print(f"Starting production server on {API_HOST}:{API_PORT}...")
    serve(app, host=API_HOST, port=API_PORT, threads=12)
