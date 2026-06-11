import sqlite3
import zipfile
import io
import csv
import tempfile
import os
import requests
import logging
import pytz
import re
import threading
import hashlib
import urllib.parse
import math
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, cast
from config import (
    GTFS_DB,
    GTFS_DATASET_PAGE_URL,
    GTFS_MAX_DOWNLOAD_BYTES,
    GTFS_MAX_FILE_BYTES,
    GTFS_MAX_UNCOMPRESSED_BYTES,
)
from gsp_overrides import GSP_ROUTE_OVERRIDES, override_shared_lines_between, public_stop_id, raw_stop_id

def normalize_text(text: str) -> str:
    replacements = {
        'č': 'c', 'ć': 'c',
        'š': 's',
        'ž': 'z',
        'đ': 'd',
        'Č': 'C', 'Ć': 'C',
        'Š': 'S',
        'Ž': 'Z',
        'Đ': 'D',
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
        'ђ': 'd', 'е': 'e', 'ж': 'z', 'з': 'z', 'и': 'i',
        'ј': 'j', 'к': 'k', 'л': 'l', 'љ': 'lj', 'м': 'm',
        'н': 'n', 'њ': 'nj', 'о': 'o', 'п': 'p', 'р': 'r',
        'с': 's', 'т': 't', 'ћ': 'c', 'у': 'u', 'ф': 'f',
        'х': 'h', 'ц': 'c', 'ч': 'c', 'џ': 'dz', 'ш': 's',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D',
        'Ђ': 'D', 'Е': 'E', 'Ж': 'Z', 'З': 'Z', 'И': 'I',
        'Ј': 'J', 'К': 'K', 'Л': 'L', 'Љ': 'Lj', 'М': 'M',
        'Н': 'N', 'Њ': 'Nj', 'О': 'O', 'П': 'P', 'Р': 'R',
        'С': 'S', 'Т': 'T', 'Ћ': 'C', 'У': 'U', 'Ф': 'F',
        'Х': 'H', 'Ц': 'C', 'Ч': 'C', 'Џ': 'Dz', 'Ш': 'S'
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text.lower()

def is_night_line(line: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "", str(line or "").strip()).upper()
    return bool(
        re.fullmatch(r"\d+[A-Z]*N[AB]?", normalized)
        or re.fullmatch(r"N[AB]?\d*", normalized)
    )

def should_show_line_now(line: str, current_time_obj: Optional[datetime] = None) -> bool:
    if not is_night_line(line):
        return True

    current_time = current_time_obj or get_belgrade_time()
    return 0 <= current_time.hour < 5

def get_belgrade_time() -> datetime:
    tz = pytz.timezone('Europe/Belgrade')
    return datetime.now(tz)

def distance_meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
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


class GTFSManager:
    def __init__(self, db_path: str = GTFS_DB):
        self.db_path = db_path
        self._update_lock = threading.Lock()
        self._init_db()

    def _connect(self, db_path: Optional[str] = None) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path or self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _init_db(self, db_path: Optional[str] = None):
        conn = self._connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS routes (route_id TEXT PRIMARY KEY, route_short_name TEXT, route_long_name TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, trip_headsign TEXT, direction_id INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS stop_times (trip_id TEXT, arrival_time TEXT, departure_time TEXT, stop_id TEXT, stop_sequence INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS stops (stop_id TEXT PRIMARY KEY, stop_name TEXT, stop_lat REAL, stop_lon REAL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS calendar (service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS calendar_dates (service_id TEXT, date TEXT, exception_type INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        
        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trips_route_service ON trips (route_id, service_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trips_service_trip_route ON trips (service_id, trip_id, route_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_trip_seq ON stop_times (trip_id, stop_sequence)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_trip_stop_seq ON stop_times (trip_id, stop_id, stop_sequence)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop_id ON stop_times (stop_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop_trip_seq ON stop_times (stop_id, trip_id, stop_sequence)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop_seq_trip ON stop_times (stop_id, stop_sequence, trip_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop_arrival_trip ON stop_times (stop_id, arrival_time, trip_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_routes_short_name ON routes (route_short_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stops_name ON stops (stop_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stops_lat_lon ON stops (stop_lat, stop_lon)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_calendar_dates_date_service ON calendar_dates (date, service_id)")
        
        conn.commit()
        conn.close()

    def _checkpoint_db(self, db_path: Optional[str] = None) -> None:
        conn = self._connect(db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

    def _sqlite_sidecar_paths(self, db_path: str) -> List[str]:
        return [f"{db_path}-wal", f"{db_path}-shm", f"{db_path}-journal"]

    def _cleanup_sqlite_sidecars(self, db_path: str) -> None:
        for sidecar_path in self._sqlite_sidecar_paths(db_path):
            if os.path.exists(sidecar_path):
                os.remove(sidecar_path)

    def _create_temp_db_path(self) -> str:
        db_dir = os.path.dirname(os.path.abspath(self.db_path)) or "."
        db_name = os.path.basename(self.db_path)
        fd, temp_db_path = tempfile.mkstemp(prefix=f".{db_name}.", suffix=".tmp", dir=db_dir)
        os.close(fd)
        return temp_db_path

    def _validate_built_db(self, db_path: str) -> None:
        conn = self._connect(db_path)
        cursor = conn.cursor()
        required_counts = {
            "routes": 1,
            "trips": 1,
            "stop_times": 1,
            "stops": 1,
            "calendar": 1,
        }
        for table, minimum_count in required_counts.items():
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = int(cursor.fetchone()[0])
            if row_count < minimum_count:
                conn.close()
                raise ValueError(f"Built GTFS database has no usable {table} rows")
        conn.close()

    def _write_update_metadata(self, db_path: str, feed: Dict[str, str]) -> None:
        conn = self._connect(db_path)
        cursor = conn.cursor()
        now = get_belgrade_time().isoformat()
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("last_update", now))
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("last_checked", now))
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("gtfs_source_url", feed["url"]))
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("gtfs_source_sha1", feed["sha1"]))
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("gtfs_source_modified", feed["modified"]))
        conn.commit()
        conn.close()

    def _promote_db(self, temp_db_path: str) -> None:
        self._checkpoint_db(temp_db_path)
        if os.path.exists(self.db_path):
            self._checkpoint_db(self.db_path)
        os.replace(temp_db_path, self.db_path)
        self._cleanup_sqlite_sidecars(temp_db_path)
        self._cleanup_sqlite_sidecars(self.db_path)

    def _discover_gtfs_feed(self) -> Optional[Dict[str, str]]:
        response = requests.get(GTFS_DATASET_PAGE_URL, timeout=30)
        response.raise_for_status()
        page = response.text

        zip_match = re.search(r'https://data\.gov\.rs/s/resources/gradski-javni-prevoz-u-beogradu-gtfs/[^"\']+\.zip', page)
        if not zip_match:
            zip_match = re.search(r'https://data\.gov\.rs/s/resources/[^"\']+bgprev[^"\']+\.zip', page)

        sha1_match = re.search(r'sha1\s*</[^>]+>\s*<[^>]+>\s*([0-9a-fA-F]{40})', page, re.IGNORECASE)
        modified_match = re.search(r'Промењено\s*</[^>]+>\s*<[^>]+>\s*([^<]+)', page)

        if not zip_match:
            logging.error("Could not discover current GTFS zip URL from %s", GTFS_DATASET_PAGE_URL)
            return None

        return {
            "url": zip_match.group(0),
            "sha1": sha1_match.group(1).lower() if sha1_match else "",
            "modified": modified_match.group(1).strip() if modified_match else "",
        }

    def _validate_gtfs_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("GTFS feed URL must use HTTPS")
        if parsed.hostname != "data.gov.rs":
            raise ValueError("GTFS feed URL must be hosted on data.gov.rs")

    def _validate_zip_manifest(self, z: zipfile.ZipFile) -> None:
        required_files = {"routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "calendar.txt"}
        names = set(z.namelist())

        total_uncompressed = 0
        for info in z.infolist():
            filename = info.filename
            normalized_name = os.path.normpath(filename)
            if filename.startswith(("/", "\\")) or normalized_name.startswith(".."):
                raise ValueError(f"GTFS zip contains unsafe path: {filename}")
            if info.file_size > GTFS_MAX_FILE_BYTES:
                raise ValueError(f"GTFS zip member exceeds limit: {filename}")
            total_uncompressed += info.file_size
            if total_uncompressed > GTFS_MAX_UNCOMPRESSED_BYTES:
                raise ValueError("GTFS zip uncompressed size exceeds configured limit")

        missing_files = sorted(required_files - names)
        if missing_files:
            raise ValueError(f"GTFS zip is missing required files: {', '.join(missing_files)}")

    def update_gtfs(self, force: bool = False):
        if not self._update_lock.acquire(blocking=False):
            logging.info("GTFS update skipped because another update is already in progress.")
            return False

        logging.info("Starting GTFS update (memory efficient mode)...")
        temp_zip = None
        temp_db_path = None
        try:
            feed = self._discover_gtfs_feed()
            if not feed:
                return False

            current_sha1 = self.get_metadata("gtfs_source_sha1")
            current_url = self.get_metadata("gtfs_source_url")
            if not force and feed["sha1"] and current_sha1 == feed["sha1"] and current_url == feed["url"]:
                logging.info("GTFS source unchanged (sha1=%s); skipping rebuild.", feed["sha1"])
                self.set_metadata("last_checked", get_belgrade_time().isoformat())
                return True

            self._validate_gtfs_url(feed["url"])
            digest = hashlib.sha1()
            downloaded_bytes = 0

            # Download in chunks to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", mode='wb') as tmp_file:
                tmp = cast(Any, tmp_file)
                temp_zip = tmp.name
                with requests.get(feed["url"], stream=True, timeout=120) as r:
                    r.raise_for_status()
                    content_length = r.headers.get("Content-Length")
                    if content_length and int(content_length) > GTFS_MAX_DOWNLOAD_BYTES:
                        raise ValueError("GTFS zip download exceeds configured limit")
                    for chunk in r.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > GTFS_MAX_DOWNLOAD_BYTES:
                            raise ValueError("GTFS zip download exceeds configured limit")
                        digest.update(chunk)
                        tmp.write(chunk)

            if feed["sha1"] and digest.hexdigest().lower() != feed["sha1"]:
                raise ValueError("GTFS zip sha1 digest does not match dataset metadata")
            
            # Parse from the temporary file directly
            with zipfile.ZipFile(temp_zip) as z:
                self._validate_zip_manifest(z)
                temp_db_path = self._create_temp_db_path()
                self._init_db(temp_db_path)
                self._parse_zip(z, temp_db_path)
                self._validate_built_db(temp_db_path)
                self._write_update_metadata(temp_db_path, feed)
                self._promote_db(temp_db_path)
                temp_db_path = None

            logging.info("GTFS update completed successfully.")
            return True
        except Exception as e:
            logging.error(f"Error updating GTFS: {e}")
            return False
        finally:
            if temp_zip and os.path.exists(temp_zip):
                try:
                    os.remove(temp_zip)
                except Exception as e:
                    logging.error(f"Failed to remove temp GTFS zip: {e}")
            if temp_db_path and os.path.exists(temp_db_path):
                try:
                    os.remove(temp_db_path)
                    self._cleanup_sqlite_sidecars(temp_db_path)
                except Exception as e:
                    logging.error(f"Failed to remove temp GTFS database: {e}")
            self._update_lock.release()

    def _parse_zip(self, z: zipfile.ZipFile, db_path: Optional[str] = None):
        conn = self._connect(db_path)
        cursor = conn.cursor()
        
        def iter_csv(filename: str):
            if filename not in z.namelist():
                return
            with z.open(filename) as f:
                # Wrap binary stream in TextIOWrapper to read line by line
                # Use utf-8-sig to handle optional BOM
                text_wrapper = io.TextIOWrapper(f, encoding='utf-8-sig')
                reader = csv.DictReader(text_wrapper)
                for row in reader:
                    yield row

        cursor.execute("DELETE FROM routes")
        cursor.execute("DELETE FROM trips")
        cursor.execute("DELETE FROM stop_times")
        cursor.execute("DELETE FROM stops")
        cursor.execute("DELETE FROM calendar")
        cursor.execute("DELETE FROM calendar_dates")

        logging.info("Parsing routes.txt...")
        route_rows = []
        for row in iter_csv('routes.txt'):
            r = cast(Dict[str, Any], row)
            route_rows.append((r.get('route_id'), r.get('route_short_name'), r.get('route_long_name', '')))
        cursor.executemany("INSERT INTO routes (route_id, route_short_name, route_long_name) VALUES (?, ?, ?)", route_rows)

        logging.info("Parsing trips.txt...")
        trip_rows = []
        for row in iter_csv('trips.txt'):
            r = cast(Dict[str, Any], row)
            trip_rows.append((r.get('trip_id'), r.get('route_id'), r.get('service_id'), r.get('trip_headsign', ''), r.get('direction_id')))
        cursor.executemany("INSERT INTO trips (trip_id, route_id, service_id, trip_headsign, direction_id) VALUES (?, ?, ?, ?, ?)", trip_rows)

        logging.info("Parsing stops.txt...")
        stop_rows = []
        for row in iter_csv('stops.txt'):
            r = cast(Dict[str, Any], row)
            stop_rows.append((r.get('stop_id'), r.get('stop_name'), float(r.get('stop_lat', 0)), float(r.get('stop_lon', 0))))
        cursor.executemany("INSERT INTO stops (stop_id, stop_name, stop_lat, stop_lon) VALUES (?, ?, ?, ?)", stop_rows)

        logging.info("Parsing stop_times.txt...")
        st_data = []
        for row in iter_csv('stop_times.txt'):
            r = cast(Dict[str, Any], row)
            st_data.append((r.get('trip_id'), r.get('arrival_time'), r.get('departure_time'), r.get('stop_id'), int(r.get('stop_sequence', 0))))
            if len(st_data) >= 10000:
                cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", st_data)
                st_data = []
        if st_data:
            cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", st_data)

        logging.info("Parsing calendar.txt...")
        calendar_rows = []
        for row in iter_csv('calendar.txt'):
            r = cast(Dict[str, Any], row)
            calendar_rows.append((r.get('service_id'), int(r.get('monday', 0)), int(r.get('tuesday', 0)), int(r.get('wednesday', 0)), int(r.get('thursday', 0)), int(r.get('friday', 0)), int(r.get('saturday', 0)), int(r.get('sunday', 0)), r.get('start_date'), r.get('end_date')))
        cursor.executemany("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", calendar_rows)

        logging.info("Parsing calendar_dates.txt...")
        calendar_date_rows = []
        for row in iter_csv('calendar_dates.txt'):
            r = cast(Dict[str, Any], row)
            calendar_date_rows.append((r.get('service_id'), r.get('date'), int(r.get('exception_type', 0))))
        cursor.executemany("INSERT INTO calendar_dates VALUES (?, ?, ?)", calendar_date_rows)

        conn.commit()
        conn.close()

    def get_last_update(self) -> Optional[str]:
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'last_update'")
            res = cursor.fetchone()
            conn.close()
            return res[0] if res else None
        except Exception:
            return None

    def get_metadata(self, key: str) -> Optional[str]:
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def set_metadata(self, key: str, value: str) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def _get_active_service_ids(self, date_obj: datetime) -> List[str]:
        day_eng = date_obj.strftime('%A').lower()
        # Security: Whitelist day_eng to prevent SQL injection as it cannot be parameterized as a column name
        valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        if day_eng not in valid_days:
            return []
            
        date_str = date_obj.strftime('%Y%m%d')
        
        conn = self._connect()
        cursor = conn.cursor()
        # Parameterize only the date values; column names must be from the whitelist
        query = f"SELECT service_id FROM calendar WHERE {day_eng} = 1 AND start_date <= ? AND end_date >= ?"
        cursor.execute(query, (date_str, date_str))
        service_ids = [r[0] for r in cursor.fetchall()]
        active_service_ids = set(service_ids)

        cursor.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
            (date_str,),
        )
        for service_id, exception_type in cursor.fetchall():
            if exception_type == 1:
                active_service_ids.add(service_id)
            elif exception_type == 2:
                active_service_ids.discard(service_id)

        conn.close()
        return [service_id for service_id in service_ids if service_id in active_service_ids] + sorted(active_service_ids - set(service_ids))

    def _get_active_route_short_names(self, cursor: sqlite3.Cursor, service_ids: List[str]) -> set[str]:
        if not service_ids:
            return set()

        placeholders = ", ".join(["?"] * len(service_ids))
        cursor.execute(
            f"""
            SELECT DISTINCT r.route_short_name
            FROM trips t
            JOIN routes r ON t.route_id = r.route_id
            WHERE t.service_id IN ({placeholders})
            """,
            service_ids,
        )
        return {str(row[0]) for row in cursor.fetchall()}

    def is_data_outdated(self) -> bool:
        """Returns True if today's date is outside the GTFS calendar range."""
        today_str = get_belgrade_time().strftime('%Y%m%d')
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(start_date), MAX(end_date) FROM calendar")
        res = cursor.fetchone()
        conn.close()
        if not res or not res[0]:
            return True
        return not (res[0] <= today_str <= res[1])

    def resolve_stop_name(self, name: str) -> List[Dict[str, Any]]:
        """Resolves a stop name or public stop id to likely GTFS stops."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = str(name).strip()
        results: List[Dict[str, Any]] = []

        lookup_ids = []
        if query.isdigit():
            if int(query) < 20000:
                lookup_ids.append(str(int(query) + 20000))
            lookup_ids.append(query)

        if lookup_ids:
            placeholders = ', '.join(['?'] * len(lookup_ids))
            order_cases = " ".join(f"WHEN ? THEN {index}" for index, _ in enumerate(lookup_ids))
            cursor.execute(
                f"""
                SELECT stop_id, stop_name, stop_lat, stop_lon
                FROM stops
                WHERE stop_id IN ({placeholders})
                ORDER BY CASE stop_id {order_cases} ELSE {len(lookup_ids)} END
                """,
                lookup_ids + lookup_ids,
            )
            results = [dict(s) for s in cursor.fetchall()]
            if results:
                conn.close()
                return results

        cursor.execute("SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops")
        all_stops = [dict(s) for s in cursor.fetchall()]
        conn.close()

        query_variants = [query]
        without_numbers = re.sub(r"\b\d+[a-zA-Z]?\b", " ", query)
        without_numbers = re.sub(r"\s+", " ", without_numbers).strip(" ,.-")
        if without_numbers and without_numbers != query:
            query_variants.append(without_numbers)

        matches: List[Dict[str, Any]] = []
        seen_stop_ids = set()
        for query_variant in query_variants:
            normalized_query = normalize_text(query_variant)
            if not normalized_query:
                continue

            for stop in all_stops:
                normalized_name = normalize_text(str(stop['stop_name']))
                public_stop_id = str(stop['stop_id'])
                if public_stop_id.isdigit() and int(public_stop_id) >= 20000:
                    public_stop_id = str(int(public_stop_id) - 20000)

                if (
                    normalized_query in normalized_name
                    or normalized_query == public_stop_id
                    or normalized_query in public_stop_id
                ):
                    stop_id = str(stop["stop_id"])
                    if stop_id in seen_stop_ids:
                        continue
                    seen_stop_ids.add(stop_id)
                    matches.append(stop)

            if matches:
                break

        return matches[:50]

    def _parse_gtfs_time(self, time_str: str) -> int:
        """Converts HH:MM:SS to seconds from midnight. Supports HH >= 24."""
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    def _format_gtfs_time(self, seconds: int) -> str:
        """Converts seconds from service-day midnight to HH:MM:SS. Supports HH >= 24."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def predict_bus_position(self, line_number: str, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if current_time_obj is None:
            current_time_obj = get_belgrade_time()
            
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            return []
            
        # Get seconds from midnight
        now_seconds = current_time_obj.hour * 3600 + current_time_obj.minute * 60 + current_time_obj.second
        
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Find route_id
        cursor.execute("SELECT route_id FROM routes WHERE route_short_name = ?", (line_number,))
        route = cursor.fetchone()
        if not route:
            conn.close()
            return []
        route_id = route['route_id']
        
        placeholders = ', '.join(['?'] * len(service_ids))
        cursor.execute(
            f"SELECT trip_id, trip_headsign, direction_id FROM trips WHERE route_id = ? AND service_id IN ({placeholders})",
            [route_id] + service_ids
        )
        trips = [dict(row) for row in cursor.fetchall()]
        if not trips:
            conn.close()
            return []

        trip_ids = [str(trip['trip_id']) for trip in trips]
        trip_placeholders = ', '.join(['?'] * len(trip_ids))
        cursor.execute(f"""
            SELECT st.trip_id, st.arrival_time, st.departure_time, st.stop_sequence, s.stop_name, s.stop_lat, s.stop_lon
            FROM stop_times st
            JOIN stops s ON st.stop_id = s.stop_id
            WHERE st.trip_id IN ({trip_placeholders})
            ORDER BY st.trip_id, st.stop_sequence
        """, trip_ids)
        stops_by_trip: Dict[str, List[sqlite3.Row]] = {}
        for row in cursor.fetchall():
            stops_by_trip.setdefault(str(row['trip_id']), []).append(row)

        active_buses = []
        for trip in trips:
            trip_id = str(trip['trip_id'])
            stops = stops_by_trip.get(trip_id, [])
            if not stops:
                continue
                
            first_dep = self._parse_gtfs_time(stops[0]['departure_time'])
            last_arr = self._parse_gtfs_time(stops[-1]['arrival_time'])
            
            # Bus hasn't started yet
            if now_seconds < first_dep:
                active_buses.append({
                    'status': 'not_started',
                    'trip_id': trip_id,
                    'direction': trip['trip_headsign'],
                    'next_stop': stops[0]['stop_name'],
                    'arrival_time': stops[0]['arrival_time'],
                    'mins_until': (first_dep - now_seconds) // 60
                })
                continue
                
            # Bus already finished
            if now_seconds > last_arr:
                # We can optionally hide finished buses or mark them
                continue
                
            # Bus is in transit
            current_segment = None
            for i in range(len(stops) - 1):
                s1 = stops[i]
                s2 = stops[i+1]
                t1 = self._parse_gtfs_time(s1['departure_time'])
                t2 = self._parse_gtfs_time(s2['arrival_time'])
                
                if t1 <= now_seconds <= t2:
                    current_segment = (s1, s2)
                    break
                elif i > 0 and self._parse_gtfs_time(stops[i-1]['departure_time']) < now_seconds < t1:
                    # Bus is currently AT a stop or just departing
                    # For simplicity, we'll treat it as between previous and current
                    current_segment = (stops[i-1], s1)
                    break

            if current_segment is not None:
                s1, s2 = current_segment
                t2 = self._parse_gtfs_time(s2['arrival_time'])
                active_buses.append({
                    'status': 'in_transit',
                    'trip_id': trip_id,
                    'direction': trip['trip_headsign'],
                    'position': f"Između {s1['stop_name']} i {s2['stop_name']}",
                    'position_en': f"Between {s1['stop_name']} and {s2['stop_name']}",
                    'next_stop': s2['stop_name'],
                    'arrival_time': s2['arrival_time'],
                    'mins_until': (t2 - now_seconds) // 60
                })
        
        conn.close()
        return active_buses

    def predict_arrivals_at_stop(self, stop_id_or_name: str, line_numbers: Optional[List[str]] = None, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if current_time_obj is None:
            current_time_obj = get_belgrade_time()
            
        now_seconds = current_time_obj.hour * 3600 + current_time_obj.minute * 60 + current_time_obj.second
        service_windows = []
        current_service_ids = self._get_active_service_ids(current_time_obj)
        if current_service_ids:
            service_windows.append((current_service_ids, now_seconds))

        if now_seconds < 4 * 3600:
            previous_day = current_time_obj - timedelta(days=1)
            previous_service_ids = self._get_active_service_ids(previous_day)
            if previous_service_ids:
                service_windows.append((previous_service_ids, now_seconds + 24 * 3600))

        if not service_windows:
            return []
        
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. BusLogic API IDs map directly to GTFS IDs by adding 20000 
        buslogic_name = stop_id_or_name
        is_numeric = stop_id_or_name.isdigit()
        
        matched_stop_ids = []
        matched_stop_names = set()
        
        if is_numeric:
            gtfs_target_id = str(int(stop_id_or_name) + 20000)
            cursor.execute("SELECT stop_id, stop_name FROM stops WHERE stop_id = ?", (gtfs_target_id,))
            r = cursor.fetchone()
            if r:
                matched_stop_ids.append(str(r['stop_id']))
                matched_stop_names.add(str(r['stop_name']))
            
            # Fallback to direct numeric match if the user actually inputted a 20xxx ID
            if not matched_stop_ids:
                cursor.execute("SELECT stop_id, stop_name FROM stops WHERE stop_id = ?", (stop_id_or_name,))
                fallback = cursor.fetchone()
                if fallback:
                    matched_stop_ids.append(str(fallback['stop_id']))
                    matched_stop_names.add(str(fallback['stop_name']))
        else:
            norm_target = normalize_text(buslogic_name)
            like_pattern = f"%{buslogic_name}%"
            cursor.execute("SELECT stop_id, stop_name FROM stops WHERE stop_name LIKE ?", (like_pattern,))
            candidate_rows = cursor.fetchall()

            if not candidate_rows:
                prefix = buslogic_name.split()[0] if buslogic_name.split() else buslogic_name
                cursor.execute("SELECT stop_id, stop_name FROM stops WHERE stop_name LIKE ?", (f"%{prefix}%",))
                candidate_rows = cursor.fetchall()

            if not candidate_rows:
                cursor.execute("SELECT stop_id, stop_name FROM stops")
                candidate_rows = cursor.fetchall()

            for r in candidate_rows:
                gtfs_id = str(r['stop_id'])
                gtfs_name = str(r['stop_name'])
                norm_gtfs = normalize_text(gtfs_name)
                if norm_target in norm_gtfs or norm_gtfs in norm_target:
                    matched_stop_ids.append(gtfs_id)
                    matched_stop_names.add(gtfs_name)
                
        if not matched_stop_ids:
            conn.close()
            return [{'error': f"Nije pronađena GTFS stanica u redu vožnje za: {buslogic_name}"}]
            
        display_stop_name = " / ".join(sorted(list(matched_stop_names)))
        
        placeholders_stops = ', '.join(['?'] * len(matched_stop_ids))
        line_filter = ""
        if line_numbers:
            lp = ', '.join(['?'] * len(line_numbers))
            line_filter = f"AND r.route_short_name IN ({lp})"
            
        row_limit = max(100, min(600, (len(line_numbers) if line_numbers else 10) * 60))
        rows = []
        for window_service_ids, comparison_now_seconds in service_windows:
            placeholders_services = ', '.join(['?'] * len(window_service_ids))
            params = list(matched_stop_ids) + window_service_ids
            if line_numbers:
                params.extend(line_numbers)
            query = f"""
                SELECT r.route_short_name, st.arrival_time, t.trip_headsign, st.trip_id
                FROM stop_times st
                JOIN trips t ON st.trip_id = t.trip_id
                JOIN routes r ON t.route_id = r.route_id
                WHERE st.stop_id IN ({placeholders_stops}) AND t.service_id IN ({placeholders_services}) {line_filter}
                  AND st.arrival_time >= ?
                ORDER BY st.arrival_time
                LIMIT ?
            """
            cursor.execute(query, params + [self._format_gtfs_time(comparison_now_seconds), row_limit])
            rows.extend((row, comparison_now_seconds) for row in cursor.fetchall())
        
        # Spatial fallback if the GTFS stop is dead (0 scheduled trips) but has coordinates
        if not rows and matched_stop_ids:
            # Take the coordinates of the first matched stop
            cursor.execute("SELECT stop_lat, stop_lon FROM stops WHERE stop_id = ?", (matched_stop_ids[0],))
            coords = cursor.fetchone()
            if coords and coords['stop_lat'] and coords['stop_lon']:
                nearby = self.get_stops_nearby(coords['stop_lat'], coords['stop_lon'], 250)
                nearby_ids = [str(nb['stop_id']) for nb in nearby if str(nb['stop_id']) not in matched_stop_ids]
                if nearby_ids:
                    placeholders_stops = ', '.join(['?'] * len(nearby_ids))
                    for window_service_ids, comparison_now_seconds in service_windows:
                        placeholders_services = ', '.join(['?'] * len(window_service_ids))
                        params = nearby_ids + window_service_ids
                        if line_numbers:
                            params.extend(line_numbers)
                        query = f"""
                            SELECT r.route_short_name, st.arrival_time, t.trip_headsign, st.trip_id
                            FROM stop_times st
                            JOIN trips t ON st.trip_id = t.trip_id
                            JOIN routes r ON t.route_id = r.route_id
                            WHERE st.stop_id IN ({placeholders_stops}) AND t.service_id IN ({placeholders_services}) {line_filter}
                              AND st.arrival_time >= ?
                            ORDER BY st.arrival_time
                            LIMIT ?
                        """
                        cursor.execute(query, params + [self._format_gtfs_time(comparison_now_seconds), row_limit])
                        rows.extend((row, comparison_now_seconds) for row in cursor.fetchall())
                    if rows:
                        display_stop_name += " (obližnje/nearby)"
        
        arrivals = []
        line_counts: Dict[str, int] = {}
        seen_arrivals = set()
        for row, comparison_now_seconds in sorted(rows, key=lambda item: self._parse_gtfs_time(item[0]['arrival_time']) - item[1]):
            arrival_key = (row['trip_id'], row['arrival_time'])
            if arrival_key in seen_arrivals:
                continue
            seen_arrivals.add(arrival_key)
            arr_seconds = self._parse_gtfs_time(row['arrival_time'])
            if arr_seconds < comparison_now_seconds:
                continue
                
            line = row['route_short_name']
            if line not in line_counts:
                if len(line_counts) >= 10: # Limit to 10 lines total
                    continue
                line_counts[line] = 0
            if line_counts[line] >= 5: # Limit next arrivals to 5 per line
                continue
                
            line_counts[line] += 1
            arrivals.append({
                'line': line,
                'arrival_time': row['arrival_time'],
                'mins_remaining': (arr_seconds - comparison_now_seconds) // 60,
                'direction': row['trip_headsign'],
                'stop_name': display_stop_name,
                'buslogic_name': buslogic_name
            })
            
        conn.close()
        
        if not arrivals:
            return [{'empty': True, 'stop_name': display_stop_name, 'buslogic_name': buslogic_name}]
            
        arrivals.sort(key=lambda x: x['mins_remaining'])
        return arrivals

    def get_line_route(self, line_number: str, stop_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        line_number = str(line_number)
        has_bgprevoz_import = self._has_bgprevoz_import(cursor, line_number)
        override_directions = GSP_ROUTE_OVERRIDES.get(str(line_number))
        if override_directions and not has_bgprevoz_import:
            routes_data = []
            for direction in override_directions:
                lookup_ids = [raw_stop_id(station_id) for station_id in direction["station_ids"]]
                placeholders = ", ".join(["?"] * len(lookup_ids))
                cursor.execute(
                    f"SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops WHERE stop_id IN ({placeholders})",
                    lookup_ids,
                )
                stops_by_id = {str(row["stop_id"]): dict(row) for row in cursor.fetchall()}
                stop_list = []
                for index, station_id in enumerate(direction["station_ids"], start=1):
                    raw_id = raw_stop_id(station_id)
                    stop = stops_by_id.get(raw_id)
                    if not stop:
                        continue
                    stop_list.append({
                        **stop,
                        "stop_sequence": index,
                        "override_source": direction["source_url"],
                    })

                matching_stop = next((stop for stop in stop_list if public_stop_id(stop["stop_id"]) == public_stop_id(stop_id or "")), None)
                routes_data.append({
                    "direction_id": direction["direction_id"],
                    "headsign": direction["headsign"],
                    "stops": stop_list,
                    "serves_selected_stop": matching_stop is not None,
                    "selected_stop_sequence": matching_stop["stop_sequence"] if matching_stop else None,
                    "source": "gsp_override",
                    "source_url": direction["source_url"],
                })

            conn.close()
            if stop_id:
                matching_routes = [route for route in routes_data if route["serves_selected_stop"]]
                if matching_routes:
                    return matching_routes
            return routes_data
        
        # We need to find the "representative" pattern for each direction
        # Usually each direction is defined by trips. Let's find trips with max stops.
        cursor.execute("""
            SELECT t.trip_id, t.direction_id, t.trip_headsign, COUNT(st.stop_id) as stop_count
            FROM trips t
            JOIN routes r ON t.route_id = r.route_id
            JOIN stop_times st ON t.trip_id = st.trip_id
            WHERE r.route_short_name = ?
            GROUP BY t.trip_id
            ORDER BY t.direction_id ASC, stop_count DESC, t.trip_id ASC
        """, (line_number,))
        
        trips = cursor.fetchall()
        directions = {}
        for trip in trips:
            d_id = trip['direction_id']
            if d_id not in directions:
                directions[d_id] = trip['trip_id']
            if len(directions) == 2:
                break
                
        routes_data = []
        for d_id, trip_id in directions.items():
            cursor.execute("""
                SELECT s.stop_id, s.stop_name, s.stop_lat, s.stop_lon, st.stop_sequence
                FROM stop_times st
                JOIN stops s ON st.stop_id = s.stop_id
                WHERE st.trip_id = ?
                ORDER BY st.stop_sequence
            """, (trip_id,))
            stops = cursor.fetchall()
            
            # Get headsign for this direction
            cursor.execute("SELECT trip_headsign FROM trips WHERE trip_id = ?", (trip_id,))
            headsign = cursor.fetchone()['trip_headsign']
            
            stop_list = [dict(s) for s in stops]
            matching_stop = next((stop for stop in stop_list if str(stop['stop_id']) == str(stop_id)), None)

            routes_data.append({
                'direction_id': d_id,
                'headsign': headsign,
                'stops': stop_list,
                'serves_selected_stop': matching_stop is not None,
                'selected_stop_sequence': matching_stop['stop_sequence'] if matching_stop else None,
                'source': 'bgprevoz' if str(trip_id).startswith('bg:') else 'gtfs'
            })
            
        conn.close()
        if stop_id:
            matching_routes = [route for route in routes_data if route['serves_selected_stop']]
            if matching_routes:
                return matching_routes

        return routes_data

    def _has_bgprevoz_import(self, cursor: sqlite3.Cursor, line_number: str) -> bool:
        metadata_key = f"bgprevoz_line_hash:{normalize_text(str(line_number))}"
        cursor.execute("SELECT 1 FROM metadata WHERE key = ? LIMIT 1", (metadata_key,))
        if not cursor.fetchone():
            return False

        cursor.execute(
            """
            SELECT 1
            FROM routes r
            JOIN trips t ON t.route_id = r.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE r.route_short_name = ?
              AND t.trip_id LIKE 'bg:%'
            LIMIT 1
            """,
            (str(line_number),),
        )
        return cursor.fetchone() is not None

    def find_routes_between_stops(self, origin_stop_id: str, dest_stop_id: str, expand_nearby: bool = True, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        current_time_obj = current_time_obj or get_belgrade_time()
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            conn.close()
            return []
        service_placeholders = ", ".join(["?"] * len(service_ids))
        active_route_names = self._get_active_route_short_names(cursor, service_ids)

        def nearby_stop_ids(stop_id: str) -> List[str]:
            cursor.execute("SELECT stop_lat, stop_lon FROM stops WHERE stop_id = ?", (stop_id,))
            row = cursor.fetchone()
            if not row:
                return [str(stop_id)]

            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
            radius_m = 240
            lat_delta = radius_m / 111000.0
            lon_delta = radius_m / (111000.0 * 0.7)
            cursor.execute(
                """
                SELECT stop_id
                FROM stops
                WHERE stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?
                ORDER BY ((stop_lat - ?) * (stop_lat - ?)) + ((stop_lon - ?) * (stop_lon - ?))
                LIMIT 16
                """,
                (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta, lat, lat, lon, lon),
            )
            ids = [str(candidate["stop_id"]) for candidate in cursor.fetchall()]
            return list(dict.fromkeys([str(stop_id), *ids]))

        origin_ids = nearby_stop_ids(str(origin_stop_id)) if expand_nearby else [str(origin_stop_id)]
        dest_ids = nearby_stop_ids(str(dest_stop_id)) if expand_nearby else [str(dest_stop_id)]
        if str(origin_stop_id) != str(dest_stop_id):
            overlapping_ids = set(origin_ids) & set(dest_ids)
            origin_ids = [stop_id for stop_id in origin_ids if stop_id not in overlapping_ids or stop_id == str(origin_stop_id)]
            dest_ids = [stop_id for stop_id in dest_ids if stop_id not in overlapping_ids or stop_id == str(dest_stop_id)]
            if not origin_ids:
                origin_ids = [str(origin_stop_id)]
            if not dest_ids:
                dest_ids = [str(dest_stop_id)]
        origin_placeholders = ", ".join(["?"] * len(origin_ids))
        dest_placeholders = ", ".join(["?"] * len(dest_ids))

        # 1. Direct routes
        query = f"""
            SELECT r.route_short_name, t.trip_headsign, t.direction_id,
                   st1.stop_id as origin_stop_id,
                   st2.stop_id as dest_stop_id,
                   MIN(st2.stop_sequence - st1.stop_sequence) as stops_count
            FROM stop_times st1
            JOIN stop_times st2 ON st1.trip_id = st2.trip_id
            JOIN trips t ON st1.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE st1.stop_id IN ({origin_placeholders})
              AND st2.stop_id IN ({dest_placeholders})
              AND t.service_id IN ({service_placeholders})
              AND st1.stop_id != st2.stop_id
              AND st1.stop_sequence < st2.stop_sequence
            GROUP BY r.route_short_name, t.trip_headsign, t.direction_id, st1.stop_id, st2.stop_id
            ORDER BY stops_count ASC, r.route_short_name
            LIMIT 250
        """
        cursor.execute(query, origin_ids + dest_ids + service_ids)
        direct_rows = cursor.fetchall()
        
        results = []
        seen_routes = set()
        for row in direct_rows:
            if not should_show_line_now(row['route_short_name'], current_time_obj):
                continue
            route_key = (row['route_short_name'], row['direction_id'], row['trip_headsign'])
            if route_key in seen_routes:
                continue
            seen_routes.add(route_key)
            results.append({
                'type': 'direct',
                'line': row['route_short_name'],
                'direction': row['trip_headsign'],
                'stops_count': row['stops_count'],
                'origin_stop_id': str(row['origin_stop_id']),
                'origin_station_id': public_stop_id(row['origin_stop_id']),
                'dest_stop_id': str(row['dest_stop_id']),
                'dest_station_id': public_stop_id(row['dest_stop_id']),
            })
            if len(results) >= 20:
                break

        if not results:
            origin_leg_query = f"""
                SELECT r.route_short_name as line1,
                       st_start.stop_id as origin_stop_id,
                       st_end.stop_id as transfer_stop_id,
                       s.stop_name as transfer_stop,
                       s.stop_lat as transfer_stop_lat,
                       s.stop_lon as transfer_stop_lon,
                       MIN(st_end.stop_sequence - st_start.stop_sequence) as stops1
                FROM stop_times st_start
                JOIN stop_times st_end
                  ON st_start.trip_id = st_end.trip_id
                 AND st_start.stop_sequence < st_end.stop_sequence
                JOIN trips t ON st_start.trip_id = t.trip_id
                JOIN routes r ON t.route_id = r.route_id
                JOIN stops s ON st_end.stop_id = s.stop_id
                WHERE st_start.stop_id IN ({origin_placeholders})
                  AND t.service_id IN ({service_placeholders})
                GROUP BY r.route_short_name, st_start.stop_id, st_end.stop_id
                ORDER BY stops1 ASC
                LIMIT 900
            """
            cursor.execute(origin_leg_query, origin_ids + service_ids)
            origin_legs = cursor.fetchall()

            dest_leg_query = f"""
                SELECT r.route_short_name as line2,
                       st_start.stop_id as transfer_stop_id,
                       s.stop_name as transfer_stop,
                       s.stop_lat as transfer_stop_lat,
                       s.stop_lon as transfer_stop_lon,
                       st_end.stop_id as dest_stop_id,
                       MIN(st_end.stop_sequence - st_start.stop_sequence) as stops2
                FROM stop_times st_end
                JOIN stop_times st_start
                  ON st_start.trip_id = st_end.trip_id
                 AND st_start.stop_sequence < st_end.stop_sequence
                JOIN trips t ON st_end.trip_id = t.trip_id
                JOIN routes r ON t.route_id = r.route_id
                JOIN stops s ON st_start.stop_id = s.stop_id
                WHERE st_end.stop_id IN ({dest_placeholders})
                  AND t.service_id IN ({service_placeholders})
                GROUP BY r.route_short_name, st_start.stop_id, st_end.stop_id
                ORDER BY stops2 ASC
                LIMIT 900
            """
            cursor.execute(dest_leg_query, dest_ids + service_ids)
            dest_legs = cursor.fetchall()

            dest_by_transfer: Dict[str, List[sqlite3.Row]] = {}
            for leg in dest_legs:
                dest_by_transfer.setdefault(str(leg["transfer_stop_id"]), []).append(leg)

            seen_transfers = set()
            transfer_results = []
            for first_leg in origin_legs:
                if not should_show_line_now(first_leg["line1"], current_time_obj):
                    continue
                for second_leg in dest_by_transfer.get(str(first_leg["transfer_stop_id"]), []):
                    if not should_show_line_now(second_leg["line2"], current_time_obj):
                        continue
                    if first_leg["line1"] == second_leg["line2"]:
                        continue
                    route_key = (first_leg["line1"], second_leg["line2"], first_leg["transfer_stop_id"])
                    if route_key in seen_transfers:
                        continue
                    seen_transfers.add(route_key)
                    transfer_results.append({
                        'type': 'transfer',
                        'line1': first_leg['line1'],
                        'line2': second_leg['line2'],
                        'transfer_at': first_leg['transfer_stop'],
                        'transfer_stop_id': str(first_leg['transfer_stop_id']),
                        'transfer_station_id': public_stop_id(first_leg['transfer_stop_id']),
                        'transfer_from_stop_id': str(first_leg['transfer_stop_id']),
                        'transfer_from_station_id': public_stop_id(first_leg['transfer_stop_id']),
                        'transfer_from_stop_name': first_leg['transfer_stop'],
                        'transfer_to_stop_id': str(second_leg['transfer_stop_id']),
                        'transfer_to_station_id': public_stop_id(second_leg['transfer_stop_id']),
                        'transfer_to_stop_name': second_leg['transfer_stop'],
                        'transfer_walk_m': 0,
                        'transfer_stop_lat': first_leg['transfer_stop_lat'],
                        'transfer_stop_lon': first_leg['transfer_stop_lon'],
                        'transfer_to_stop_lat': second_leg['transfer_stop_lat'],
                        'transfer_to_stop_lon': second_leg['transfer_stop_lon'],
                        'origin_stop_id': str(first_leg['origin_stop_id']),
                        'origin_station_id': public_stop_id(first_leg['origin_stop_id']),
                        'dest_stop_id': str(second_leg['dest_stop_id']),
                        'dest_station_id': public_stop_id(second_leg['dest_stop_id']),
                        'stops_count': int(first_leg['stops1']) + int(second_leg['stops2']),
                    })

            if not transfer_results:
                walking_transfer_results = []
                max_transfer_walk_m = 220
                for first_leg in origin_legs:
                    if not should_show_line_now(first_leg["line1"], current_time_obj):
                        continue
                    for second_leg in dest_legs:
                        if not should_show_line_now(second_leg["line2"], current_time_obj):
                            continue
                        if first_leg["line1"] == second_leg["line2"]:
                            continue
                        transfer_walk_m = distance_meters_between(
                            first_leg["transfer_stop_lat"],
                            first_leg["transfer_stop_lon"],
                            second_leg["transfer_stop_lat"],
                            second_leg["transfer_stop_lon"],
                        )
                        if transfer_walk_m > max_transfer_walk_m:
                            continue
                        route_key = (
                            first_leg["line1"],
                            second_leg["line2"],
                            first_leg["transfer_stop_id"],
                            second_leg["transfer_stop_id"],
                        )
                        if route_key in seen_transfers:
                            continue
                        seen_transfers.add(route_key)
                        transfer_name = first_leg["transfer_stop"]
                        if str(first_leg["transfer_stop_id"]) != str(second_leg["transfer_stop_id"]):
                            transfer_name = f"{first_leg['transfer_stop']} -> {second_leg['transfer_stop']}"
                        walking_transfer_results.append({
                            'type': 'transfer',
                            'line1': first_leg['line1'],
                            'line2': second_leg['line2'],
                            'transfer_at': transfer_name,
                            'transfer_stop_id': str(second_leg['transfer_stop_id']),
                            'transfer_station_id': public_stop_id(second_leg['transfer_stop_id']),
                            'transfer_from_stop_id': str(first_leg['transfer_stop_id']),
                            'transfer_from_station_id': public_stop_id(first_leg['transfer_stop_id']),
                            'transfer_from_stop_name': first_leg['transfer_stop'],
                            'transfer_to_stop_id': str(second_leg['transfer_stop_id']),
                            'transfer_to_station_id': public_stop_id(second_leg['transfer_stop_id']),
                            'transfer_to_stop_name': second_leg['transfer_stop'],
                            'transfer_walk_m': transfer_walk_m,
                            'transfer_stop_lat': second_leg['transfer_stop_lat'],
                            'transfer_stop_lon': second_leg['transfer_stop_lon'],
                            'transfer_from_stop_lat': first_leg['transfer_stop_lat'],
                            'transfer_from_stop_lon': first_leg['transfer_stop_lon'],
                            'transfer_to_stop_lat': second_leg['transfer_stop_lat'],
                            'transfer_to_stop_lon': second_leg['transfer_stop_lon'],
                            'origin_stop_id': str(first_leg['origin_stop_id']),
                            'origin_station_id': public_stop_id(first_leg['origin_stop_id']),
                            'dest_stop_id': str(second_leg['dest_stop_id']),
                            'dest_station_id': public_stop_id(second_leg['dest_stop_id']),
                            'stops_count': int(first_leg['stops1']) + int(second_leg['stops2']),
                        })

                transfer_results.extend(sorted(
                    walking_transfer_results,
                    key=lambda route: (route['stops_count'], route['transfer_walk_m']),
                )[:10])

            results.extend(sorted(transfer_results, key=lambda route: route['stops_count'])[:10])

        override_results = []
        origin_public_ids = {public_stop_id(stop_id) for stop_id in origin_ids}
        dest_public_ids = {public_stop_id(stop_id) for stop_id in dest_ids}
        for line, directions in GSP_ROUTE_OVERRIDES.items():
            if line not in active_route_names or not should_show_line_now(line, current_time_obj):
                continue
            for direction in directions:
                station_ids = direction["station_ids"]
                origin_matches = [
                    (index, station_id)
                    for index, station_id in enumerate(station_ids)
                    if station_id in origin_public_ids
                ]
                dest_matches = [
                    (index, station_id)
                    for index, station_id in enumerate(station_ids)
                    if station_id in dest_public_ids
                ]
                for origin_index, origin_station_id in origin_matches:
                    for dest_index, dest_station_id in dest_matches:
                        if origin_index >= dest_index:
                            continue
                        override_results.append({
                            "type": "direct",
                            "line": line,
                            "direction": direction["headsign"],
                            "stops_count": dest_index - origin_index,
                            "origin_stop_id": raw_stop_id(origin_station_id),
                            "origin_station_id": origin_station_id,
                            "dest_stop_id": raw_stop_id(dest_station_id),
                            "dest_station_id": dest_station_id,
                            "source": "gsp_override",
                            "source_url": direction["source_url"],
                        })

        if override_results:
            existing_keys = {
                (
                    route.get("type"),
                    route.get("line"),
                    route.get("origin_station_id"),
                    route.get("dest_station_id"),
                )
                for route in results
            }
            for route in sorted(override_results, key=lambda item: item["stops_count"]):
                key = (
                    route.get("type"),
                    route.get("line"),
                    route.get("origin_station_id"),
                    route.get("dest_station_id"),
                )
                if key in existing_keys:
                    continue
                results.append(route)
                existing_keys.add(key)
        
        conn.close()
        return results

    def find_two_transfer_routes_between_stops(self, origin_stop_id: str, dest_stop_id: str, expand_nearby: bool = True, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        current_time_obj = current_time_obj or get_belgrade_time()
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            conn.close()
            return []
        service_placeholders = ", ".join(["?"] * len(service_ids))

        def nearby_stop_ids(stop_id: str) -> List[str]:
            cursor.execute("SELECT stop_lat, stop_lon FROM stops WHERE stop_id = ?", (stop_id,))
            row = cursor.fetchone()
            if not row:
                return [str(stop_id)]

            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
            radius_m = 240
            lat_delta = radius_m / 111000.0
            lon_delta = radius_m / (111000.0 * 0.7)
            cursor.execute(
                """
                SELECT stop_id
                FROM stops
                WHERE stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?
                ORDER BY ((stop_lat - ?) * (stop_lat - ?)) + ((stop_lon - ?) * (stop_lon - ?))
                LIMIT 16
                """,
                (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta, lat, lat, lon, lon),
            )
            ids = [str(candidate["stop_id"]) for candidate in cursor.fetchall()]
            return list(dict.fromkeys([str(stop_id), *ids]))

        def nearby_transfer_stops(lat: float, lon: float, radius_m: float = 220) -> List[Dict[str, Any]]:
            cache_key = (round(float(lat), 6), round(float(lon), 6), int(radius_m))
            cached = nearby_transfer_cache.get(cache_key)
            if cached is not None:
                return cached

            lat_delta = radius_m / 111000.0
            lon_delta = radius_m / (111000.0 * 0.7)
            min_lat = float(lat) - lat_delta
            max_lat = float(lat) + lat_delta
            min_lon = float(lon) - lon_delta
            max_lon = float(lon) + lon_delta
            stops = []
            for row in all_transfer_stops:
                if (
                    row["stop_lat"] < min_lat
                    or row["stop_lat"] > max_lat
                    or row["stop_lon"] < min_lon
                    or row["stop_lon"] > max_lon
                ):
                    continue
                distance = distance_meters_between(lat, lon, row["stop_lat"], row["stop_lon"])
                if distance <= radius_m:
                    stops.append({**dict(row), "transfer_walk_m": distance})
            stops.sort(key=lambda stop: stop["transfer_walk_m"])
            nearby_transfer_cache[cache_key] = stops[:16]
            return nearby_transfer_cache[cache_key]

        origin_ids = nearby_stop_ids(str(origin_stop_id)) if expand_nearby else [str(origin_stop_id)]
        dest_ids = nearby_stop_ids(str(dest_stop_id)) if expand_nearby else [str(dest_stop_id)]
        origin_placeholders = ", ".join(["?"] * len(origin_ids))
        dest_placeholders = ", ".join(["?"] * len(dest_ids))

        first_leg_query = f"""
            SELECT r.route_short_name as line1,
                   st_start.stop_id as origin_stop_id,
                   st_end.stop_id as transfer1_from_stop_id,
                   s.stop_name as transfer1_from_stop,
                   s.stop_lat as transfer1_from_stop_lat,
                   s.stop_lon as transfer1_from_stop_lon,
                   MIN(st_end.stop_sequence - st_start.stop_sequence) as stops1
            FROM stop_times st_start
            JOIN stop_times st_end
              ON st_start.trip_id = st_end.trip_id
             AND st_start.stop_sequence < st_end.stop_sequence
            JOIN trips t ON st_start.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            JOIN stops s ON st_end.stop_id = s.stop_id
            WHERE st_start.stop_id IN ({origin_placeholders})
              AND t.service_id IN ({service_placeholders})
            GROUP BY r.route_short_name, st_start.stop_id, st_end.stop_id
            ORDER BY stops1 ASC
            LIMIT 350
        """
        cursor.execute(first_leg_query, origin_ids + service_ids)
        first_legs = [row for row in cursor.fetchall() if should_show_line_now(row["line1"], current_time_obj)]

        last_leg_query = f"""
            SELECT r.route_short_name as line3,
                   st_start.stop_id as transfer2_to_stop_id,
                   s.stop_name as transfer2_to_stop,
                   s.stop_lat as transfer2_to_stop_lat,
                   s.stop_lon as transfer2_to_stop_lon,
                   st_end.stop_id as dest_stop_id,
                   MIN(st_end.stop_sequence - st_start.stop_sequence) as stops3
            FROM stop_times st_end
            JOIN stop_times st_start
              ON st_start.trip_id = st_end.trip_id
             AND st_start.stop_sequence < st_end.stop_sequence
            JOIN trips t ON st_end.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            JOIN stops s ON st_start.stop_id = s.stop_id
            WHERE st_end.stop_id IN ({dest_placeholders})
              AND t.service_id IN ({service_placeholders})
            GROUP BY r.route_short_name, st_start.stop_id, st_end.stop_id
            ORDER BY stops3 ASC
            LIMIT 350
        """
        cursor.execute(last_leg_query, dest_ids + service_ids)
        last_legs = [row for row in cursor.fetchall() if should_show_line_now(row["line3"], current_time_obj)]

        cursor.execute("SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops")
        all_transfer_stops = [dict(row) for row in cursor.fetchall()]
        nearby_transfer_cache: Dict[tuple, List[Dict[str, Any]]] = {}

        first_by_middle_start: Dict[str, List[Dict[str, Any]]] = {}
        for leg in first_legs:
            nearby_stops = nearby_transfer_stops(leg["transfer1_from_stop_lat"], leg["transfer1_from_stop_lon"])
            for stop in nearby_stops:
                first_by_middle_start.setdefault(str(stop["stop_id"]), []).append({
                    "leg": leg,
                    "transfer1_to_stop_id": str(stop["stop_id"]),
                    "transfer1_to_station_id": public_stop_id(stop["stop_id"]),
                    "transfer1_to_stop_name": stop["stop_name"],
                    "transfer1_to_stop_lat": stop["stop_lat"],
                    "transfer1_to_stop_lon": stop["stop_lon"],
                    "transfer1_walk_m": stop["transfer_walk_m"],
                })

        last_by_middle_end: Dict[str, List[Dict[str, Any]]] = {}
        for leg in last_legs:
            nearby_stops = nearby_transfer_stops(leg["transfer2_to_stop_lat"], leg["transfer2_to_stop_lon"])
            for stop in nearby_stops:
                last_by_middle_end.setdefault(str(stop["stop_id"]), []).append({
                    "leg": leg,
                    "transfer2_from_stop_id": str(stop["stop_id"]),
                    "transfer2_from_station_id": public_stop_id(stop["stop_id"]),
                    "transfer2_from_stop_name": stop["stop_name"],
                    "transfer2_from_stop_lat": stop["stop_lat"],
                    "transfer2_from_stop_lon": stop["stop_lon"],
                    "transfer2_walk_m": stop["transfer_walk_m"],
                })

        middle_start_ids = list(first_by_middle_start.keys())[:1200]
        middle_end_ids = list(last_by_middle_end.keys())[:1200]
        if not middle_start_ids or not middle_end_ids:
            conn.close()
            return []

        middle_start_placeholders = ", ".join(["?"] * len(middle_start_ids))
        middle_end_placeholders = ", ".join(["?"] * len(middle_end_ids))
        middle_leg_query = f"""
            SELECT r.route_short_name as line2,
                   st_start.stop_id as transfer1_to_stop_id,
                   st_end.stop_id as transfer2_from_stop_id,
                   s1.stop_name as transfer1_to_stop,
                   s1.stop_lat as transfer1_to_stop_lat,
                   s1.stop_lon as transfer1_to_stop_lon,
                   s2.stop_name as transfer2_from_stop,
                   s2.stop_lat as transfer2_from_stop_lat,
                   s2.stop_lon as transfer2_from_stop_lon,
                   MIN(st_end.stop_sequence - st_start.stop_sequence) as stops2
            FROM stop_times st_start
            JOIN stop_times st_end
              ON st_start.trip_id = st_end.trip_id
             AND st_start.stop_sequence < st_end.stop_sequence
            JOIN trips t ON st_start.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            JOIN stops s1 ON st_start.stop_id = s1.stop_id
            JOIN stops s2 ON st_end.stop_id = s2.stop_id
            WHERE st_start.stop_id IN ({middle_start_placeholders})
              AND st_end.stop_id IN ({middle_end_placeholders})
              AND t.service_id IN ({service_placeholders})
            GROUP BY r.route_short_name, st_start.stop_id, st_end.stop_id
            ORDER BY stops2 ASC
            LIMIT 900
        """
        cursor.execute(middle_leg_query, middle_start_ids + middle_end_ids + service_ids)
        middle_legs = [row for row in cursor.fetchall() if should_show_line_now(row["line2"], current_time_obj)]

        results = []
        seen_routes = set()
        for middle_leg in middle_legs:
            first_options = first_by_middle_start.get(str(middle_leg["transfer1_to_stop_id"]), [])
            last_options = last_by_middle_end.get(str(middle_leg["transfer2_from_stop_id"]), [])
            for first_option in first_options:
                first_leg = first_option["leg"]
                if first_leg["line1"] == middle_leg["line2"]:
                    continue
                for last_option in last_options:
                    last_leg = last_option["leg"]
                    if middle_leg["line2"] == last_leg["line3"]:
                        continue
                    route_key = (
                        first_leg["line1"],
                        middle_leg["line2"],
                        last_leg["line3"],
                        first_leg["transfer1_from_stop_id"],
                        middle_leg["transfer2_from_stop_id"],
                        last_leg["dest_stop_id"],
                    )
                    if route_key in seen_routes:
                        continue
                    seen_routes.add(route_key)
                    transfer1_name = first_leg["transfer1_from_stop"]
                    if str(first_leg["transfer1_from_stop_id"]) != str(first_option["transfer1_to_stop_id"]):
                        transfer1_name = f"{first_leg['transfer1_from_stop']} -> {first_option['transfer1_to_stop_name']}"
                    transfer2_name = middle_leg["transfer2_from_stop"]
                    if str(middle_leg["transfer2_from_stop_id"]) != str(last_leg["transfer2_to_stop_id"]):
                        transfer2_name = f"{middle_leg['transfer2_from_stop']} -> {last_leg['transfer2_to_stop']}"
                    results.append({
                        "type": "multi_transfer",
                        "line1": first_leg["line1"],
                        "line2": middle_leg["line2"],
                        "line3": last_leg["line3"],
                        "transfer_at": f"{transfer1_name}; {transfer2_name}",
                        "transfer1_at": transfer1_name,
                        "transfer1_from_stop_id": str(first_leg["transfer1_from_stop_id"]),
                        "transfer1_from_station_id": public_stop_id(first_leg["transfer1_from_stop_id"]),
                        "transfer1_from_stop_name": first_leg["transfer1_from_stop"],
                        "transfer1_from_stop_lat": first_leg["transfer1_from_stop_lat"],
                        "transfer1_from_stop_lon": first_leg["transfer1_from_stop_lon"],
                        "transfer1_to_stop_id": first_option["transfer1_to_stop_id"],
                        "transfer1_to_station_id": first_option["transfer1_to_station_id"],
                        "transfer1_to_stop_name": first_option["transfer1_to_stop_name"],
                        "transfer1_to_stop_lat": first_option["transfer1_to_stop_lat"],
                        "transfer1_to_stop_lon": first_option["transfer1_to_stop_lon"],
                        "transfer1_walk_m": first_option["transfer1_walk_m"],
                        "transfer2_at": transfer2_name,
                        "transfer2_from_stop_id": str(middle_leg["transfer2_from_stop_id"]),
                        "transfer2_from_station_id": public_stop_id(middle_leg["transfer2_from_stop_id"]),
                        "transfer2_from_stop_name": middle_leg["transfer2_from_stop"],
                        "transfer2_from_stop_lat": middle_leg["transfer2_from_stop_lat"],
                        "transfer2_from_stop_lon": middle_leg["transfer2_from_stop_lon"],
                        "transfer2_to_stop_id": str(last_leg["transfer2_to_stop_id"]),
                        "transfer2_to_station_id": public_stop_id(last_leg["transfer2_to_stop_id"]),
                        "transfer2_to_stop_name": last_leg["transfer2_to_stop"],
                        "transfer2_to_stop_lat": last_leg["transfer2_to_stop_lat"],
                        "transfer2_to_stop_lon": last_leg["transfer2_to_stop_lon"],
                        "transfer2_walk_m": last_option["transfer2_walk_m"],
                        "transfer_stop_id": str(middle_leg["transfer2_from_stop_id"]),
                        "transfer_station_id": public_stop_id(middle_leg["transfer2_from_stop_id"]),
                        "transfer_stop_lat": middle_leg["transfer2_from_stop_lat"],
                        "transfer_stop_lon": middle_leg["transfer2_from_stop_lon"],
                        "origin_stop_id": str(first_leg["origin_stop_id"]),
                        "origin_station_id": public_stop_id(first_leg["origin_stop_id"]),
                        "dest_stop_id": str(last_leg["dest_stop_id"]),
                        "dest_station_id": public_stop_id(last_leg["dest_stop_id"]),
                        "stops_count": int(first_leg["stops1"]) + int(middle_leg["stops2"]) + int(last_leg["stops3"]),
                    })

        conn.close()
        return sorted(
            results,
            key=lambda route: (
                route["stops_count"],
                route.get("transfer1_walk_m", 0) + route.get("transfer2_walk_m", 0),
                str(route.get("line1") or ""),
            ),
        )[:10]

    def find_direct_routes_between_stop_sets(self, origin_stop_ids: List[str], dest_stop_ids: List[str], limit: int = 500, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        origin_ids = list(dict.fromkeys(str(stop_id) for stop_id in origin_stop_ids if str(stop_id)))
        dest_ids = list(dict.fromkeys(str(stop_id) for stop_id in dest_stop_ids if str(stop_id)))
        if not origin_ids or not dest_ids:
            return []

        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        current_time_obj = current_time_obj or get_belgrade_time()
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            conn.close()
            return []

        origin_placeholders = ", ".join(["?"] * len(origin_ids))
        dest_placeholders = ", ".join(["?"] * len(dest_ids))
        service_placeholders = ", ".join(["?"] * len(service_ids))
        cursor.execute(
            f"""
            SELECT r.route_short_name, t.trip_headsign, t.direction_id,
                   st1.stop_id as origin_stop_id,
                   st2.stop_id as dest_stop_id,
                   MIN(st2.stop_sequence - st1.stop_sequence) as stops_count
            FROM stop_times st1
            JOIN stop_times st2 ON st1.trip_id = st2.trip_id
            JOIN trips t ON st1.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE st1.stop_id IN ({origin_placeholders})
              AND st2.stop_id IN ({dest_placeholders})
              AND t.service_id IN ({service_placeholders})
              AND st1.stop_id != st2.stop_id
              AND st1.stop_sequence < st2.stop_sequence
            GROUP BY r.route_short_name, t.trip_headsign, t.direction_id, st1.stop_id, st2.stop_id
            ORDER BY stops_count ASC, r.route_short_name
            LIMIT ?
            """,
            origin_ids + dest_ids + service_ids + [limit],
        )
        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            if not should_show_line_now(row["route_short_name"], current_time_obj):
                continue
            results.append({
                "type": "direct",
                "line": row["route_short_name"],
                "direction": row["trip_headsign"],
                "stops_count": row["stops_count"],
                "origin_stop_id": str(row["origin_stop_id"]),
                "origin_station_id": public_stop_id(row["origin_stop_id"]),
                "dest_stop_id": str(row["dest_stop_id"]),
                "dest_station_id": public_stop_id(row["dest_stop_id"]),
            })
        return results

    def get_connected_stops(self, origin_stop_id: str, query: str = "", limit: int = 25, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        current_time_obj = current_time_obj or get_belgrade_time()
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            conn.close()
            return []
        active_route_names = self._get_active_route_short_names(cursor, service_ids)

        def nearby_boarding_stop_ids(stop_id: str) -> List[str]:
            cursor.execute("SELECT stop_lat, stop_lon FROM stops WHERE stop_id = ?", (stop_id,))
            row = cursor.fetchone()
            if not row:
                return [str(stop_id)]

            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
            radius_m = 240
            lat_delta = radius_m / 111000.0
            lon_delta = radius_m / (111000.0 * 0.7)
            cursor.execute(
                """
                SELECT stop_id
                FROM stops
                WHERE stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?
                ORDER BY ((stop_lat - ?) * (stop_lat - ?)) + ((stop_lon - ?) * (stop_lon - ?))
                LIMIT 16
                """,
                (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta, lat, lat, lon, lon),
            )
            ids = [str(candidate["stop_id"]) for candidate in cursor.fetchall()]
            return list(dict.fromkeys([str(stop_id), *ids]))

        origin_ids = nearby_boarding_stop_ids(str(origin_stop_id))
        origin_placeholders = ", ".join(["?"] * len(origin_ids))
        service_placeholders = ", ".join(["?"] * len(service_ids))
        cursor.execute(
            f"""
            SELECT
                s.stop_id,
                s.stop_name,
                s.stop_lat,
                s.stop_lon,
                GROUP_CONCAT(DISTINCT r.route_short_name) AS shared_lines
            FROM stop_times st_origin
            JOIN trips t ON st_origin.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            JOIN stop_times st_dest ON st_dest.trip_id = st_origin.trip_id
              AND st_dest.stop_sequence > st_origin.stop_sequence
            JOIN stops s ON s.stop_id = st_dest.stop_id
            WHERE st_origin.stop_id IN ({origin_placeholders})
              AND s.stop_id NOT IN ({origin_placeholders})
              AND t.service_id IN ({service_placeholders})
            GROUP BY s.stop_id, s.stop_name, s.stop_lat, s.stop_lon
            """,
            origin_ids + origin_ids + service_ids,
        )

        normalized_query = normalize_text(str(query or "").strip())
        candidates = []
        for row in cursor.fetchall():
            stop = dict(row)
            public_stop_id = str(stop["stop_id"])
            if public_stop_id.isdigit() and int(public_stop_id) >= 20000:
                public_stop_id = str(int(public_stop_id) - 20000)

            if normalized_query:
                normalized_name = normalize_text(str(stop["stop_name"]))
                if (
                    normalized_query not in normalized_name
                    and normalized_query not in public_stop_id
                ):
                    continue

            shared_lines = sorted({
                line.strip()
                for line in str(stop.get("shared_lines") or "").split(",")
                if line.strip() and should_show_line_now(line, current_time_obj)
            }, key=lambda line: normalize_text(line))
            route_lines = set()
            for candidate_origin_id in origin_ids:
                for line in override_shared_lines_between(candidate_origin_id, str(stop["stop_id"])):
                    if line in active_route_names and should_show_line_now(line, current_time_obj):
                        route_lines.add(line)
            shared_lines = sorted({*shared_lines, *route_lines}, key=lambda line: normalize_text(str(line)))

            candidates.append({
                "stop_id": str(stop["stop_id"]),
                "stop_name": str(stop["stop_name"]),
                "stop_lat": stop["stop_lat"],
                "stop_lon": stop["stop_lon"],
                "shared_lines": shared_lines,
            })

        conn.close()

        def sort_key(stop: Dict[str, Any]) -> Any:
            public_stop_id = str(stop["stop_id"])
            if public_stop_id.isdigit() and int(public_stop_id) >= 20000:
                public_stop_id = str(int(public_stop_id) - 20000)
            normalized_name = normalize_text(str(stop["stop_name"]))
            exact_id = normalized_query and public_stop_id == normalized_query
            starts = normalized_query and normalized_name.startswith(normalized_query)
            return (
                0 if exact_id else 1,
                0 if starts else 1,
                len(stop["shared_lines"]) * -1,
                normalized_name,
                public_stop_id,
            )

        return sorted(candidates, key=sort_key)[:limit]

    def get_stops_nearby(self, lat: float, lon: float, radius_m: float = 500, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        # 1 degree lat is approx 111km
        # 1 degree lon is approx 111km * cos(lat)
        lat_delta = radius_m / 111000.0
        lon_delta = radius_m / (111000.0 * 0.7) # Approx for Belgrade (44.8 deg)
        
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT stop_id, stop_name, stop_lat, stop_lon
            FROM stops
            WHERE stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?
        """, (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta))
        
        rows = cursor.fetchall()
        conn.close()
        
        # Calculate real distance and sort
        import math
        def distance(lat1, lon1, lat2, lon2):
            R = 6371000 # meters
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            dphi = math.radians(lat2 - lat1)
            dlambda = math.radians(lon2 - lon1)
            a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
            return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

        results = []
        for row in rows:
            dist = distance(lat, lon, row['stop_lat'], row['stop_lon'])
            if dist <= radius_m:
                d = dict(row)
                d['distance'] = round(dist)
                results.append(d)
                
        results.sort(key=lambda x: x['distance'])
        return results[:limit] if limit is not None else results

    def get_timetable(self, line_no: str) -> str:
        now = get_belgrade_time()
        day_eng = now.strftime('%A').lower()
        today_str = now.strftime('%Y%m%d')

        # Serbian names for days and months
        serbian_days = {
            'monday': 'Ponedeljak', 'tuesday': 'Utorak', 'wednesday': 'Sreda',
            'thursday': 'Četvrtak', 'friday': 'Petak', 'saturday': 'Subota', 'sunday': 'Nedelja'
        }
        serbian_months = [
            '', 'Januar', 'Februar', 'Mart', 'April', 'Maj', 'Jun',
            'Jul', 'Avgust', 'Septembar', 'Oktobar', 'Novembar', 'Decembar'
        ]
        
        day_srb = serbian_days.get(day_eng, day_eng.capitalize())
        date_srb = f"{now.day}. {serbian_months[now.month]} {now.year}."
        header_date = f"📅 <b>{day_srb}, {date_srb}</b>"

        conn = self._connect()
        cursor = conn.cursor()

        cursor.execute("SELECT route_id, route_long_name FROM routes WHERE route_short_name = ?", (line_no,))
        route = cursor.fetchone()
        if not route:
            conn.close()
            return f"Linija {line_no} nije pronađena u planiranom redu vožnje."
        
        route_id, route_long_name = route

        service_ids = self._get_active_service_ids(now)

        if not service_ids:
            conn.close()
            return f"{header_date}\n\nNema aktivnih polazaka za liniju {line_no} za današnji dan."

        placeholders = ', '.join(['?'] * len(service_ids))
        query = f"""
            SELECT t.trip_headsign, st.departure_time, t.direction_id
            FROM trips t
            JOIN stop_times st ON t.trip_id = st.trip_id
            WHERE t.route_id = ? AND t.service_id IN ({placeholders}) AND st.stop_sequence = 1
            ORDER BY t.direction_id, st.departure_time
        """
        cursor.execute(query, [route_id] + service_ids)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return f"{header_date}\n\nNema dostupnih planiranih polazaka za liniju {line_no} od početne stanice."

        directions: Dict[str, List[str]] = {}
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        
        for row_headsign, dep_time, dir_id in rows:
            headsign = str(row_headsign or f"Smer {dir_id}")
            time_hm = ":".join(dep_time.split(":")[:2])
            
            dep_seconds = self._parse_gtfs_time(dep_time)
            if dep_seconds < now_seconds:
                continue # Skip past departures
                
            if headsign not in directions:
                directions[headsign] = []
                
            if len(directions[headsign]) >= 15:
                continue # Limit to next 15 upcoming departures
                
            if time_hm not in directions[headsign]:
                directions[headsign].append(time_hm)

        result = [f"{header_date}\n<b>Planirani red vožnje: Linija {line_no}</b>\n<i>{route_long_name}</i>\n"]
        for headsign, times in directions.items():
            result.append(f"➡️ <b>Smer: {headsign}</b>")
            result.append(", ".join(times) + "\n")

        return "\n".join(result)


gtfs_manager = GTFSManager()
