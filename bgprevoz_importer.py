import argparse
import hashlib
import html
import json
import logging
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from config import GTFS_DB
from gtfs_manager import get_belgrade_time, normalize_text
from gsp_overrides import public_stop_id, raw_stop_id


BASE_URL = "https://www.bgprevoz.rs"
LINE_CATEGORY_PATHS = (
    "/linije/red-voznje/autobuske-linije",
    "/linije/red-voznje/tramvajske-linije",
    "/linije/red-voznje/trolejbuske-linije",
)


@dataclass
class BgLine:
    line_id: str
    code: str
    label: str


@dataclass
class BgStop:
    stop_id: str
    name: str
    lat: float
    lon: float
    sequence: int
    distance_from_previous: int
    matched_stop_id: str


@dataclass
class DirectionImport:
    direction_id: int
    headsign: str
    stops: List[BgStop]
    offsets: List[int]


class CellTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            text = re.sub(r"\s+", " ", " ".join(self._current_cell)).strip()
            self._current_row.append(text)
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


class BgPrevozImporter:
    def __init__(self, db_path: str = GTFS_DB, delay_seconds: float = 0.2) -> None:
        self.db_path = db_path
        self.delay_seconds = delay_seconds
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "BusTrackerBG/1.0 GTFS updater (polite scraper)",
            "Accept": "text/html,application/xhtml+xml,application/json",
        })

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get(self, path_or_url: str) -> str:
        url = path_or_url if path_or_url.startswith("http") else f"{BASE_URL}{path_or_url}"
        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return response.text

    def discover_lines(self) -> List[BgLine]:
        lines: Dict[str, BgLine] = {}
        option_re = re.compile(r'<option[^>]+value="[^"]*/smer-a/([^"]+)"[^>]*>(.*?)</option>', re.S)
        for path in LINE_CATEGORY_PATHS:
            page = self._get(path)
            for line_id, raw_label in option_re.findall(page):
                label = html.unescape(re.sub(r"\s+", " ", raw_label)).strip()
                code = label.split("[", 1)[0].strip()
                if not code:
                    continue
                lines[line_id] = BgLine(line_id=line_id, code=code, label=label)
        return sorted(lines.values(), key=lambda item: normalize_text(item.code))

    def fetch_direction_stops(self, line_id: str, direction_id: int, conn: sqlite3.Connection) -> List[BgStop]:
        page = self._get(f"/linije/daljinari-table/linija/{line_id}?smer={direction_id}")
        match = re.search(r"let\s+stations\s*=\s*(\{.*?\});", page, re.S)
        if not match:
            return []
        stations = json.loads(match.group(1))
        rows = sorted(stations.values(), key=lambda item: int(item.get("redni_broj_stajalista") or 0))
        result = []
        for row in rows:
            station = row.get("stajaliste") or {}
            station_id = str(row.get("stajaliste_id") or station.get("id") or "").strip()
            if not station_id:
                continue
            lat = float(station.get("sirina") or 0)
            lon = float(station.get("duzina") or 0)
            name = str(station.get("naziv") or "").strip()
            matched_stop_id = self._match_or_create_stop(conn, station_id, name, lat, lon)
            result.append(BgStop(
                stop_id=station_id,
                name=name,
                lat=lat,
                lon=lon,
                sequence=int(row.get("redni_broj_stajalista") or len(result) + 1),
                distance_from_previous=int(row.get("rastojanje_od_prethodnog_stajalista") or 0),
                matched_stop_id=matched_stop_id,
            ))
        return result

    def _match_or_create_stop(self, conn: sqlite3.Connection, station_id: str, name: str, lat: float, lon: float) -> str:
        cursor = conn.cursor()
        candidates = [raw_stop_id(station_id), station_id]
        for stop_id in candidates:
            cursor.execute("SELECT stop_id FROM stops WHERE stop_id = ?", (stop_id,))
            row = cursor.fetchone()
            if row:
                return str(row["stop_id"])

        nearest = self._nearest_stop(conn, name, lat, lon, max_distance_m=75)
        if nearest:
            return nearest

        stop_id = raw_stop_id(station_id)
        cursor.execute(
            "INSERT OR REPLACE INTO stops (stop_id, stop_name, stop_lat, stop_lon) VALUES (?, ?, ?, ?)",
            (stop_id, name, lat, lon),
        )
        return stop_id

    def _nearest_stop(self, conn: sqlite3.Connection, name: str, lat: float, lon: float, max_distance_m: float) -> Optional[str]:
        if not lat or not lon:
            return None
        cursor = conn.cursor()
        lat_delta = max_distance_m / 111000.0
        lon_delta = max_distance_m / (111000.0 * 0.7)
        cursor.execute(
            """
            SELECT stop_id, stop_name, stop_lat, stop_lon
            FROM stops
            WHERE stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?
            """,
            (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta),
        )
        normalized_name = normalize_text(name)
        best: Optional[Tuple[float, str]] = None
        for row in cursor.fetchall():
            dist = haversine(lat, lon, float(row["stop_lat"]), float(row["stop_lon"]))
            if dist > max_distance_m:
                continue
            row_name = normalize_text(str(row["stop_name"]))
            name_bonus = 0 if normalized_name and (normalized_name in row_name or row_name in normalized_name) else 25
            score = dist + name_bonus
            if best is None or score < best[0]:
                best = (score, str(row["stop_id"]))
        return best[1] if best else None

    def fetch_timetable(self, line_id: str) -> Dict[int, Dict[str, List[str]]]:
        page = self._get(f"/linije/red-voznje/linija/{line_id}/prikaz")
        parser = CellTableParser()
        parser.feed(page)
        schedules: Dict[int, Dict[str, List[str]]] = {}
        direction_id = 0
        for table in parser.tables:
            departures = {"weekday": [], "saturday": [], "sunday": []}
            for row in table:
                if len(row) < 5 or not re.fullmatch(r"\d{1,2}", row[0]):
                    continue
                hour = int(row[0])
                for service_key, cell in zip(("weekday", "saturday", "sunday"), row[1:4]):
                    for minute in re.findall(r"\b\d{1,2}\b", cell):
                        departures[service_key].append(f"{hour:02d}:{int(minute):02d}:00")
            if not any(departures.values()):
                continue
            schedules[direction_id] = departures
            direction_id += 1
            if direction_id >= 2:
                break
        return schedules

    def apply(self, limit: Optional[int] = None, line_codes: Optional[Sequence[str]] = None, dry_run: bool = False) -> Dict[str, Any]:
        conn = self._connect()
        self._ensure_bg_tables(conn)
        lines = self.discover_lines()
        requested_codes = {normalize_text(code) for code in line_codes or []}
        if requested_codes:
            lines = [line for line in lines if normalize_text(line.code) in requested_codes]
        if limit:
            lines = lines[:limit]

        summary = {
            "discovered_lines": len(lines),
            "imported_lines": 0,
            "unchanged_lines": 0,
            "skipped_lines": [],
            "inserted_trips": 0,
            "inserted_stop_times": 0,
        }

        try:
            for line in lines:
                try:
                    logging.info("Importing bgprevoz line %s (%s)", line.code, line.line_id)
                    directions = self._build_directions(conn, line)
                    timetable = self.fetch_timetable(line.line_id)
                    if not directions or not timetable:
                        summary["skipped_lines"].append({"line": line.code, "reason": "missing directions or timetable"})
                        continue

                    line_hash = self._line_content_hash(line, directions, timetable)
                    metadata_key = self._line_hash_metadata_key(line)
                    existing_hash = self._get_metadata(conn, metadata_key)
                    if existing_hash == line_hash:
                        logging.info("BG Prevoz line %s unchanged; skipping DB replacement.", line.code)
                        summary["unchanged_lines"] += 1
                        continue

                    if not dry_run:
                        inserted = self._replace_line(conn, line, directions, timetable)
                        self._set_metadata(conn, metadata_key, line_hash)
                        summary["inserted_trips"] += inserted[0]
                        summary["inserted_stop_times"] += inserted[1]
                        conn.commit()
                    summary["imported_lines"] += 1
                except Exception as exc:
                    conn.rollback()
                    logging.exception("Skipping bgprevoz line %s (%s)", line.code, line.line_id)
                    summary["skipped_lines"].append({"line": line.code, "reason": str(exc)})
        except Exception:
            conn.rollback()
            raise
        finally:
            if not dry_run:
                self._write_metadata(conn, summary)
            conn.close()

        return summary

    def _line_hash_metadata_key(self, line: BgLine) -> str:
        return f"bgprevoz_line_hash:{normalize_text(line.code)}"

    def _line_content_hash(
        self,
        line: BgLine,
        directions: Sequence[DirectionImport],
        timetable: Dict[int, Dict[str, List[str]]],
    ) -> str:
        payload = {
            "line": {
                "id": line.line_id,
                "code": line.code,
                "label": line.label,
            },
            "directions": [
                {
                    "direction_id": direction.direction_id,
                    "headsign": direction.headsign,
                    "stops": [
                        {
                            "stop_id": stop.stop_id,
                            "name": stop.name,
                            "lat": round(stop.lat, 7),
                            "lon": round(stop.lon, 7),
                            "sequence": stop.sequence,
                            "distance_from_previous": stop.distance_from_previous,
                            "matched_stop_id": stop.matched_stop_id,
                            "offset": offset,
                        }
                        for stop, offset in zip(direction.stops, direction.offsets)
                    ],
                }
                for direction in sorted(directions, key=lambda item: item.direction_id)
            ],
            "timetable": {
                str(direction_id): {
                    service_key: sorted(values)
                    for service_key, values in sorted(service_days.items())
                }
                for direction_id, service_days in sorted(timetable.items())
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_directions(self, conn: sqlite3.Connection, line: BgLine) -> List[DirectionImport]:
        directions = []
        for direction_id in (0, 1):
            stops = self.fetch_direction_stops(line.line_id, direction_id, conn)
            if len(stops) < 2:
                continue
            offsets = self._derive_offsets(conn, line.code, direction_id, stops)
            headsign = stops[-1].name
            directions.append(DirectionImport(direction_id=direction_id, headsign=headsign, stops=stops, offsets=offsets))
        return directions

    def _derive_offsets(self, conn: sqlite3.Connection, line_code: str, direction_id: int, stops: List[BgStop]) -> List[int]:
        existing = self._existing_pattern(conn, line_code, direction_id, stops)
        if existing:
            offsets_by_public_id = {public_stop_id(row["stop_id"]): row["offset"] for row in existing}
            known = []
            for index, stop in enumerate(stops):
                offset = offsets_by_public_id.get(public_stop_id(stop.matched_stop_id))
                if offset is not None:
                    known.append((index, offset))
            if len(known) >= 2:
                return interpolate_offsets(stops, known)
            if existing[-1]["offset"] > 0:
                return proportional_distance_offsets(stops, int(existing[-1]["offset"]))

        vehicle_speed_mps = 4.6
        return distance_speed_offsets(stops, vehicle_speed_mps)

    def _existing_pattern(self, conn: sqlite3.Connection, line_code: str, direction_id: int, stops: List[BgStop]) -> List[Dict[str, Any]]:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.trip_id
            FROM trips t
            JOIN routes r ON r.route_id = t.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE r.route_short_name = ? AND t.direction_id = ?
            GROUP BY t.trip_id
            ORDER BY COUNT(st.stop_id) DESC
            LIMIT 1
            """,
            (line_code, direction_id),
        )
        row = cursor.fetchone()
        if not row:
            return []
        cursor.execute(
            """
            SELECT stop_id, arrival_time, stop_sequence
            FROM stop_times
            WHERE trip_id = ?
            ORDER BY stop_sequence
            """,
            (row["trip_id"],),
        )
        rows = cursor.fetchall()
        if not rows:
            return []
        first = parse_time(str(rows[0]["arrival_time"]))
        return [
            {"stop_id": str(item["stop_id"]), "offset": max(0, parse_time(str(item["arrival_time"])) - first)}
            for item in rows
        ]

    def _replace_line(
        self,
        conn: sqlite3.Connection,
        line: BgLine,
        directions: List[DirectionImport],
        timetable: Dict[int, Dict[str, List[str]]],
    ) -> Tuple[int, int]:
        cursor = conn.cursor()
        route_id = self._route_id_for_line(conn, line)
        cursor.execute(
            "INSERT OR REPLACE INTO routes (route_id, route_short_name, route_long_name) VALUES (?, ?, ?)",
            (route_id, line.code, line.label),
        )
        cursor.execute("SELECT trip_id FROM trips WHERE route_id = ?", (route_id,))
        trip_ids = [str(row["trip_id"]) for row in cursor.fetchall()]
        if trip_ids:
            for chunk in chunks(trip_ids, 500):
                placeholders = ", ".join(["?"] * len(chunk))
                cursor.execute(f"DELETE FROM stop_times WHERE trip_id IN ({placeholders})", chunk)
            cursor.execute("DELETE FROM trips WHERE route_id = ?", (route_id,))

        inserted_trips = 0
        inserted_stop_times = 0
        for direction in directions:
            for service_key, service_id in (("weekday", "BGPREVOZ_WEEKDAY"), ("saturday", "BGPREVOZ_SATURDAY"), ("sunday", "BGPREVOZ_SUNDAY")):
                for index, departure in enumerate(timetable.get(direction.direction_id, {}).get(service_key, []), start=1):
                    trip_id = f"bg:{line.line_id}:{direction.direction_id}:{service_key}:{index:04d}"
                    cursor.execute(
                        "INSERT INTO trips (trip_id, route_id, service_id, trip_headsign, direction_id) VALUES (?, ?, ?, ?, ?)",
                        (trip_id, route_id, service_id, direction.headsign, direction.direction_id),
                    )
                    start_seconds = parse_time(departure)
                    rows = []
                    for stop, offset in zip(direction.stops, direction.offsets):
                        timestamp = format_time(start_seconds + offset)
                        rows.append((trip_id, timestamp, timestamp, stop.matched_stop_id, stop.sequence))
                    cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", rows)
                    inserted_trips += 1
                    inserted_stop_times += len(rows)
        return inserted_trips, inserted_stop_times

    def _route_id_for_line(self, conn: sqlite3.Connection, line: BgLine) -> str:
        cursor = conn.cursor()
        cursor.execute("SELECT route_id FROM routes WHERE route_short_name = ? ORDER BY route_id LIMIT 1", (line.code,))
        row = cursor.fetchone()
        return str(row["route_id"]) if row else f"bg:{line.line_id}"

    def _ensure_bg_tables(self, conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO calendar
            VALUES
              ('BGPREVOZ_WEEKDAY', 1, 1, 1, 1, 1, 0, 0, '20260101', '20401231'),
              ('BGPREVOZ_SATURDAY', 0, 0, 0, 0, 0, 1, 0, '20260101', '20401231'),
              ('BGPREVOZ_SUNDAY', 0, 0, 0, 0, 0, 0, 1, '20260101', '20401231')
            """
        )
        conn.commit()

    def _get_metadata(self, conn: sqlite3.Connection, key: str) -> Optional[str]:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return str(row["value"]) if row else None

    def _set_metadata(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))

    def _write_metadata(self, conn: sqlite3.Connection, summary: Dict[str, Any]) -> None:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("bgprevoz_last_update", get_belgrade_time().isoformat()),
        )
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("bgprevoz_last_summary", json.dumps(summary, ensure_ascii=False, sort_keys=True)),
        )
        conn.commit()


def parse_time(value: str) -> int:
    hour, minute, second = [int(part) for part in value.split(":")]
    return hour * 3600 + minute * 60 + second


def format_time(seconds: int) -> str:
    hour = seconds // 3600
    minute = (seconds % 3600) // 60
    second = seconds % 60
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def cumulative_distances(stops: Sequence[BgStop]) -> List[int]:
    total = 0
    distances = []
    for stop in stops:
        total += max(0, stop.distance_from_previous)
        distances.append(total)
    return distances


def interpolate_offsets(stops: Sequence[BgStop], known: Sequence[Tuple[int, int]]) -> List[int]:
    distances = cumulative_distances(stops)
    offsets = [0] * len(stops)
    anchors = sorted(set(known))
    if anchors[0][0] != 0:
        anchors.insert(0, (0, 0))
    if anchors[-1][0] != len(stops) - 1:
        tail_total = max(distances[-1] - distances[anchors[-1][0]], 1)
        tail_seconds = int(tail_total / 4.6)
        anchors.append((len(stops) - 1, anchors[-1][1] + tail_seconds))

    for (left_index, left_offset), (right_index, right_offset) in zip(anchors, anchors[1:]):
        span_distance = max(distances[right_index] - distances[left_index], 1)
        span_seconds = max(right_offset - left_offset, 0)
        for index in range(left_index, right_index + 1):
            ratio = (distances[index] - distances[left_index]) / span_distance
            offsets[index] = int(left_offset + span_seconds * ratio)
    return monotonic_offsets(offsets)


def proportional_distance_offsets(stops: Sequence[BgStop], total_seconds: int) -> List[int]:
    distances = cumulative_distances(stops)
    total_distance = max(distances[-1], 1)
    return monotonic_offsets([int(total_seconds * distance / total_distance) for distance in distances])


def distance_speed_offsets(stops: Sequence[BgStop], speed_mps: float) -> List[int]:
    distances = cumulative_distances(stops)
    return monotonic_offsets([int(distance / speed_mps) for distance in distances])


def monotonic_offsets(offsets: Sequence[int]) -> List[int]:
    result = []
    current = 0
    for offset in offsets:
        current = max(current, int(offset))
        result.append(current)
    if result:
        result[0] = 0
    return result


def chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import bgprevoz route patterns and departure timetables into gtfs.db")
    parser.add_argument("--db", default=GTFS_DB)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--line", action="append", dest="lines")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
    importer = BgPrevozImporter(db_path=args.db, delay_seconds=args.delay)
    summary = importer.apply(limit=args.limit, line_codes=args.lines, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
