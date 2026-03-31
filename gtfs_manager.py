import sqlite3
import zipfile
import io
import csv
import tempfile
import os
import requests
import logging
import pytz
from datetime import datetime
from typing import List, Optional, Dict, Any, cast
from config import GTFS_DB, GTFS_URL

def get_belgrade_time() -> datetime:
    tz = pytz.timezone('Europe/Belgrade')
    return datetime.now(tz)


class GTFSManager:
    def __init__(self, db_path: str = GTFS_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS routes (route_id TEXT PRIMARY KEY, route_short_name TEXT, route_long_name TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS trips (trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, trip_headsign TEXT, direction_id INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS stop_times (trip_id TEXT, arrival_time TEXT, departure_time TEXT, stop_id TEXT, stop_sequence INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS stops (stop_id TEXT PRIMARY KEY, stop_name TEXT, stop_lat REAL, stop_lon REAL)")
        cursor.execute("CREATE TABLE IF NOT EXISTS calendar (service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        
        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trips_route_service ON trips (route_id, service_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_trip_seq ON stop_times (trip_id, stop_sequence)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stoptimes_stop_id ON stop_times (stop_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_routes_short_name ON routes (route_short_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stops_name ON stops (stop_name)")
        
        conn.commit()
        conn.close()

    def update_gtfs(self):
        logging.info("Starting GTFS update (memory efficient mode)...")
        temp_zip = None
        try:
            # Download in chunks to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", mode='wb') as tmp_file:
                tmp = cast(Any, tmp_file)
                temp_zip = tmp.name
                with requests.get(GTFS_URL, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(chunk_size=8192):
                        tmp.write(chunk)
            
            # Parse from the temporary file directly
            with zipfile.ZipFile(temp_zip) as z:
                self._parse_zip(z)
                
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("last_update", get_belgrade_time().isoformat()))
            conn.commit()
            conn.close()
            logging.info("GTFS update completed successfully.")
        except Exception as e:
            logging.error(f"Error updating GTFS: {e}")
        finally:
            if temp_zip and os.path.exists(temp_zip):
                try:
                    os.remove(temp_zip)
                except Exception as e:
                    logging.error(f"Failed to remove temp GTFS zip: {e}")

    def _parse_zip(self, z: zipfile.ZipFile):
        conn = sqlite3.connect(self.db_path)
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

        logging.info("Parsing routes.txt...")
        for row in iter_csv('routes.txt'):
            r = cast(Dict[str, Any], row)
            cursor.execute("INSERT INTO routes (route_id, route_short_name, route_long_name) VALUES (?, ?, ?)",
                           (r.get('route_id'), r.get('route_short_name'), r.get('route_long_name', '')))

        logging.info("Parsing trips.txt...")
        for row in iter_csv('trips.txt'):
            r = cast(Dict[str, Any], row)
            cursor.execute("INSERT INTO trips (trip_id, route_id, service_id, trip_headsign, direction_id) VALUES (?, ?, ?, ?, ?)",
                           (r.get('trip_id'), r.get('route_id'), r.get('service_id'), r.get('trip_headsign', ''), r.get('direction_id')))

        logging.info("Parsing stops.txt...")
        for row in iter_csv('stops.txt'):
            r = cast(Dict[str, Any], row)
            cursor.execute("INSERT INTO stops (stop_id, stop_name, stop_lat, stop_lon) VALUES (?, ?, ?, ?)",
                           (r.get('stop_id'), r.get('stop_name'), float(r.get('stop_lat', 0)), float(r.get('stop_lon', 0))))

        logging.info("Parsing stop_times.txt...")
        st_data = []
        for row in iter_csv('stop_times.txt'):
            r = cast(Dict[str, Any], row)
            st_data.append((r.get('trip_id'), r.get('arrival_time'), r.get('departure_time'), r.get('stop_id'), int(r.get('stop_sequence', 0))))
            if len(st_data) >= 10000:
                cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", st_data)
                conn.commit() # Commit periodically to keep Journal size small
                st_data = []
        if st_data:
            cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", st_data)
            conn.commit()

        logging.info("Parsing calendar.txt...")
        for row in iter_csv('calendar.txt'):
            r = cast(Dict[str, Any], row)
            cursor.execute("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (r.get('service_id'), int(r.get('monday', 0)), int(r.get('tuesday', 0)), int(r.get('wednesday', 0)), int(r.get('thursday', 0)), int(r.get('friday', 0)), int(r.get('saturday', 0)), int(r.get('sunday', 0)), r.get('start_date'), r.get('end_date')))

        conn.commit()
        conn.close()

    def get_last_update(self) -> Optional[str]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'last_update'")
            res = cursor.fetchone()
            conn.close()
            return res[0] if res else None
        except Exception:
            return None

    def _get_active_service_ids(self, date_obj: datetime) -> List[str]:
        day_eng = date_obj.strftime('%A').lower()
        # Security: Whitelist day_eng to prevent SQL injection as it cannot be parameterized as a column name
        valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        if day_eng not in valid_days:
            return []
            
        date_str = date_obj.strftime('%Y%m%d')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # Parameterize only the date values; column names must be from the whitelist
        query = f"SELECT service_id FROM calendar WHERE {day_eng} = 1 AND start_date <= ? AND end_date >= ?"
        cursor.execute(query, (date_str, date_str))
        service_ids = [r[0] for r in cursor.fetchall()]
        conn.close()
        return service_ids

    def is_data_outdated(self) -> bool:
        """Returns True if today's date is outside the GTFS calendar range."""
        today_str = get_belgrade_time().strftime('%Y%m%d')
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(start_date), MAX(end_date) FROM calendar")
        res = cursor.fetchone()
        conn.close()
        if not res or not res[0]:
            return True
        return not (res[0] <= today_str <= res[1])

    def resolve_stop_name(self, name: str) -> List[Dict[str, Any]]:
        """Resolves a stop name to a list of potential stops."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT stop_id, stop_name FROM stops WHERE stop_id = ? OR stop_name LIKE ?", (name, f"%{name}%"))
        stops = [dict(s) for s in cursor.fetchall()]
        conn.close()
        return stops

    def _parse_gtfs_time(self, time_str: str) -> int:
        """Converts HH:MM:SS to seconds from midnight. Supports HH >= 24."""
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

    def predict_bus_position(self, line_number: str, current_time_obj: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if current_time_obj is None:
            current_time_obj = get_belgrade_time()
            
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            return []
            
        # Get seconds from midnight
        now_seconds = current_time_obj.hour * 3600 + current_time_obj.minute * 60 + current_time_obj.second
        
        conn = sqlite3.connect(self.db_path)
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
        # Find all trips for this route today
        cursor.execute(f"SELECT trip_id, trip_headsign, direction_id FROM trips WHERE route_id = ? AND service_id IN ({placeholders})", [route_id] + service_ids)
        trips = cursor.fetchall()
        if trips is None:
            conn.close()
            return []
            
        active_buses = []
        for trip_row in trips:
            if trip_row is None:
                continue
            trip = dict(trip_row)
            trip_id = str(trip['trip_id'])
            # Get all stop times for this trip
            cursor.execute("""
                SELECT st.arrival_time, st.departure_time, st.stop_sequence, s.stop_name, s.stop_lat, s.stop_lon
                FROM stop_times st
                JOIN stops s ON st.stop_id = s.stop_id
                WHERE st.trip_id = ?
                ORDER BY st.stop_sequence
            """, (trip_id,))
            stops = cursor.fetchall()
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
        from api_client import fetch_stations_list, normalize_text
        
        if current_time_obj is None:
            current_time_obj = get_belgrade_time()
            
        service_ids = self._get_active_service_ids(current_time_obj)
        if not service_ids:
            return []
            
        now_seconds = current_time_obj.hour * 3600 + current_time_obj.minute * 60 + current_time_obj.second
        
        conn = sqlite3.connect(self.db_path)
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
            # 2. Search GTFS stops for matches using normalized text
            norm_target = normalize_text(buslogic_name)
            cursor.execute("SELECT stop_id, stop_name FROM stops")
            all_gtfs_stops = cursor.fetchall()
            
            for r in all_gtfs_stops:
                gtfs_id = str(r['stop_id'])
                gtfs_name = str(r['stop_name'])
                norm_gtfs = normalize_text(gtfs_name)
                
                # Match if the target is in the GTFS name or vice versa
                if norm_target in norm_gtfs or norm_gtfs in norm_target:
                    matched_stop_ids.append(gtfs_id)
                    matched_stop_names.add(gtfs_name)
                
        if not matched_stop_ids:
            conn.close()
            return [{'error': f"Nije pronađena GTFS stanica u redu vožnje za: {buslogic_name}"}]
            
        display_stop_name = " / ".join(sorted(list(matched_stop_names)))
        
        placeholders_stops = ', '.join(['?'] * len(matched_stop_ids))
        placeholders_services = ', '.join(['?'] * len(service_ids))
        
        line_filter = ""
        params = list(matched_stop_ids) + service_ids
        if line_numbers:
            lp = ', '.join(['?'] * len(line_numbers))
            line_filter = f"AND r.route_short_name IN ({lp})"
            params.extend(line_numbers)
            
        query = f"""
            SELECT r.route_short_name, st.arrival_time, t.trip_headsign, st.trip_id
            FROM stop_times st
            JOIN trips t ON st.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE st.stop_id IN ({placeholders_stops}) AND t.service_id IN ({placeholders_services}) {line_filter}
            ORDER BY st.arrival_time
        """
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
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
                    params = nearby_ids + service_ids
                    if line_numbers:
                        params.extend(line_numbers)
                    query = f"""
                        SELECT r.route_short_name, st.arrival_time, t.trip_headsign, st.trip_id
                        FROM stop_times st
                        JOIN trips t ON st.trip_id = t.trip_id
                        JOIN routes r ON t.route_id = r.route_id
                        WHERE st.stop_id IN ({placeholders_stops}) AND t.service_id IN ({placeholders_services}) {line_filter}
                        ORDER BY st.arrival_time
                    """
                    cursor.execute(query, params)
                    rows = cursor.fetchall()
                    if rows:
                        display_stop_name += " (obližnje/nearby)"
        
        arrivals = []
        line_counts: Dict[str, int] = {}
        for row in rows:
            arr_seconds = self._parse_gtfs_time(row['arrival_time'])
            if arr_seconds < now_seconds:
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
                'mins_remaining': (arr_seconds - now_seconds) // 60,
                'direction': row['trip_headsign'],
                'stop_name': display_stop_name,
                'buslogic_name': buslogic_name
            })
            
        conn.close()
        
        if not arrivals:
            return [{'empty': True, 'stop_name': display_stop_name, 'buslogic_name': buslogic_name}]
            
        arrivals.sort(key=lambda x: x['arrival_time'])
        return arrivals

    def get_line_route(self, line_number: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # We need to find the "representative" pattern for each direction
        # Usually each direction is defined by trips. Let's find trips with max stops.
        cursor.execute("""
            SELECT t.trip_id, t.direction_id, t.trip_headsign, COUNT(st.stop_id) as stop_count
            FROM trips t
            JOIN routes r ON t.route_id = r.route_id
            JOIN stop_times st ON t.trip_id = st.trip_id
            WHERE r.route_short_name = ?
            GROUP BY t.trip_id
            ORDER BY stop_count DESC
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
            
            routes_data.append({
                'direction_id': d_id,
                'headsign': headsign,
                'stops': [dict(s) for s in stops]
            })
            
        conn.close()
        return routes_data

    def find_routes_between_stops(self, origin_stop_id: str, dest_stop_id: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Direct routes
        query = """
            SELECT r.route_short_name, t.trip_headsign, 
                   st1.stop_sequence as s1, st2.stop_sequence as s2
            FROM stop_times st1
            JOIN stop_times st2 ON st1.trip_id = st2.trip_id
            JOIN trips t ON st1.trip_id = t.trip_id
            JOIN routes r ON t.route_id = r.route_id
            WHERE st1.stop_id = ? AND st2.stop_id = ? AND st1.stop_sequence < st2.stop_sequence
            GROUP BY r.route_short_name, t.direction_id
        """
        cursor.execute(query, (origin_stop_id, dest_stop_id))
        direct_rows = cursor.fetchall()
        
        results = []
        for row in direct_rows:
            results.append({
                'type': 'direct',
                'line': row['route_short_name'],
                'direction': row['trip_headsign'],
                'stops_count': row['s2'] - row['s1']
            })
            
        # 2. Transfers (1 transfer)
        # Find lines from origin, and lines to destination, intersection at any stop
        if not results:
            query = """
                SELECT DISTINCT r1.route_short_name as line1, r2.route_short_name as line2,
                       s_trans.stop_name as transfer_stop, s_trans.stop_id as transfer_stop_id
                FROM stop_times st1_start
                JOIN stop_times st1_end ON st1_start.trip_id = st1_end.trip_id
                JOIN stop_times st2_start ON st2_start.stop_id = st1_end.stop_id
                JOIN stop_times st2_end ON st2_start.trip_id = st2_end.trip_id
                JOIN trips t1 ON st1_start.trip_id = t1.trip_id
                JOIN trips t2 ON st2_start.trip_id = t2.trip_id
                JOIN routes r1 ON t1.route_id = r1.route_id
                JOIN routes r2 ON t2.route_id = r2.route_id
                JOIN stops s_trans ON st1_end.stop_id = s_trans.stop_id
                WHERE st1_start.stop_id = ? AND st2_end.stop_id = ?
                AND st1_start.stop_sequence < st1_end.stop_sequence
                AND st2_start.stop_sequence < st2_end.stop_sequence
                AND r1.route_id != r2.route_id
                LIMIT 10
            """
            cursor.execute(query, (origin_stop_id, dest_stop_id))
            transfer_rows = cursor.fetchall()
            for row in transfer_rows:
                results.append({
                    'type': 'transfer',
                    'line1': row['line1'],
                    'line2': row['line2'],
                    'transfer_at': row['transfer_stop']
                })
        
        conn.close()
        return results

    def get_stops_nearby(self, lat: float, lon: float, radius_m: float = 500) -> List[Dict[str, Any]]:
        # 1 degree lat is approx 111km
        # 1 degree lon is approx 111km * cos(lat)
        lat_delta = radius_m / 111000.0
        lon_delta = radius_m / (111000.0 * 0.7) # Approx for Belgrade (44.8 deg)
        
        conn = sqlite3.connect(self.db_path)
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
        return results

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

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT route_id, route_long_name FROM routes WHERE route_short_name = ?", (line_no,))
        route = cursor.fetchone()
        if not route:
            conn.close()
            return f"Linija {line_no} nije pronađena u planiranom redu vožnje."
        
        route_id, route_long_name = route

        # Strictly today's service IDs
        day_eng = now.strftime('%A').lower()
        # Security: Whitelist day_eng
        valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        if day_eng not in valid_days:
            return f"Nije moguće utvrditi dan u nedelji: {day_eng}"

        query = f"SELECT service_id FROM calendar WHERE {day_eng} = 1 AND start_date <= ? AND end_date >= ?"
        cursor.execute(query, (today_str, today_str))
        service_ids = [r[0] for r in cursor.fetchall()]

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
