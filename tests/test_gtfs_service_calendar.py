import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from datetime import datetime
from unittest.mock import patch


_IMPORT_DB_DIR = tempfile.TemporaryDirectory()
os.environ["GTFS_DB"] = os.path.join(_IMPORT_DB_DIR.name, "import.db")

from gtfs_manager import GTFSManager  # noqa: E402


class GTFSServiceCalendarTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "gtfs.db")
        self.manager = GTFSManager(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        return self.manager._connect()

    def _insert_service(self, service_id, *, monday=0, tuesday=0, wednesday=0, thursday=0, friday=0, saturday=0, sunday=0):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO calendar
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, "20260101", "20261231"),
        )
        conn.commit()
        conn.close()

    def _insert_route_trip(self, route_id, route_name, service_id, trip_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO routes VALUES (?, ?, ?)", (route_id, route_name, route_name))
        cursor.execute("INSERT INTO trips VALUES (?, ?, ?, ?, ?)", (trip_id, route_id, service_id, "Downtown", 0))
        cursor.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                (trip_id, "10:00:00", "10:00:00", "100", 1),
                (trip_id, "10:05:00", "10:05:00", "200", 2),
            ],
        )
        conn.commit()
        conn.close()

    def _insert_route_trip_with_stops(self, route_id, route_name, service_id, trip_id, stops):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO routes VALUES (?, ?, ?)", (route_id, route_name, route_name))
        cursor.execute("INSERT INTO trips VALUES (?, ?, ?, ?, ?)", (trip_id, route_id, service_id, route_name, 0))
        cursor.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                (trip_id, "10:00:00", "10:00:00", stop_id, index)
                for index, stop_id in enumerate(stops, start=1)
            ],
        )
        conn.commit()
        conn.close()

    def _insert_stops(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("100", "Origin", 44.80, 20.40),
                ("200", "Destination", 44.81, 20.41),
            ],
        )
        conn.commit()
        conn.close()

    def test_calendar_dates_adds_and_removes_services_for_date(self):
        self._insert_service("WEEKDAY", tuesday=1)
        self._insert_service("REMOVED", tuesday=1)
        self._insert_service("WEEKEND", saturday=1)

        conn = self._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO calendar_dates VALUES (?, ?, ?)",
            [
                ("SPECIAL", "20260512", 1),
                ("REMOVED", "20260512", 2),
            ],
        )
        conn.commit()
        conn.close()

        service_ids = self.manager._get_active_service_ids(datetime(2026, 5, 12, 12, 0, 0))

        self.assertIn("WEEKDAY", service_ids)
        self.assertIn("SPECIAL", service_ids)
        self.assertNotIn("REMOVED", service_ids)
        self.assertNotIn("WEEKEND", service_ids)

    def test_find_routes_between_stops_uses_active_service_ids(self):
        self._insert_stops()
        self._insert_service("ACTIVE", tuesday=1)
        self._insert_service("INACTIVE", wednesday=1)
        self._insert_route_trip("route-active", "10", "ACTIVE", "trip-active")
        self._insert_route_trip("route-inactive", "20", "INACTIVE", "trip-inactive")

        routes = self.manager.find_routes_between_stops(
            "100",
            "200",
            expand_nearby=False,
            current_time_obj=datetime(2026, 5, 12, 12, 0, 0),
        )

        self.assertEqual(["10"], [route["line"] for route in routes])

    def test_find_routes_between_stops_allows_short_walk_between_transfer_platforms(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("100", "Origin", 44.8000, 20.4000),
                ("200", "Transfer alight", 44.8100, 20.4100),
                ("201", "Transfer board", 44.8105, 20.4100),
                ("300", "Destination", 44.8200, 20.4200),
            ],
        )
        conn.commit()
        conn.close()
        self._insert_service("ACTIVE", tuesday=1)
        self._insert_route_trip_with_stops("route-10", "10", "ACTIVE", "trip-10", ["100", "200"])
        self._insert_route_trip_with_stops("route-20", "20", "ACTIVE", "trip-20", ["201", "300"])

        routes = self.manager.find_routes_between_stops(
            "100",
            "300",
            expand_nearby=False,
            current_time_obj=datetime(2026, 5, 12, 12, 0, 0),
        )

        self.assertEqual(1, len(routes))
        self.assertEqual("transfer", routes[0]["type"])
        self.assertEqual(("10", "20"), (routes[0]["line1"], routes[0]["line2"]))
        self.assertEqual("200", routes[0]["transfer_from_stop_id"])
        self.assertEqual("201", routes[0]["transfer_to_stop_id"])
        self.assertGreater(routes[0]["transfer_walk_m"], 0)

    def test_get_connected_stops_uses_active_service_ids_for_shared_lines(self):
        self._insert_stops()
        self._insert_service("ACTIVE", tuesday=1)
        self._insert_service("INACTIVE", wednesday=1)
        self._insert_route_trip("route-active", "10", "ACTIVE", "trip-active")
        self._insert_route_trip("route-inactive", "20", "INACTIVE", "trip-inactive")

        stops = self.manager.get_connected_stops(
            "100",
            current_time_obj=datetime(2026, 5, 12, 12, 0, 0),
        )

        self.assertEqual(1, len(stops))
        self.assertEqual("200", stops[0]["stop_id"])
        self.assertEqual(["10"], stops[0]["shared_lines"])

    def test_get_stops_nearby_applies_result_limit_after_distance_sort(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("100", "Origin", 44.8000, 20.4000),
                ("200", "Near", 44.8001, 20.4001),
                ("300", "Far", 44.8050, 20.4050),
            ],
        )
        conn.commit()
        conn.close()

        stops = self.manager.get_stops_nearby(44.8000, 20.4000, 1000, limit=2)

        self.assertEqual(["100", "200"], [stop["stop_id"] for stop in stops])

    def test_predict_arrivals_includes_previous_service_day_after_midnight_trips(self):
        self._insert_stops()
        self._insert_service("MONDAY_NIGHT", monday=1)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO routes VALUES (?, ?, ?)", ("route-night", "N1", "Night Route"))
        cursor.execute("INSERT INTO trips VALUES (?, ?, ?, ?, ?)", ("trip-night", "route-night", "MONDAY_NIGHT", "Night direction", 0))
        cursor.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                ("trip-night", "24:40:00", "24:40:00", "100", 1),
                ("trip-night", "25:00:00", "25:00:00", "200", 2),
            ],
        )
        conn.commit()
        conn.close()

        arrivals = self.manager.predict_arrivals_at_stop(
            "100",
            current_time_obj=datetime(2026, 5, 12, 0, 30, 0),
        )

        self.assertEqual("N1", arrivals[0]["line"])
        self.assertEqual(10, arrivals[0]["mins_remaining"])

    def test_get_timetable_uses_calendar_date_exceptions(self):
        self._insert_service("REMOVED", tuesday=1)
        conn = self._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO calendar_dates VALUES (?, ?, ?)",
            [
                ("SPECIAL", "20260512", 1),
                ("REMOVED", "20260512", 2),
            ],
        )
        cursor.execute("INSERT INTO routes VALUES (?, ?, ?)", ("route-10", "10", "Special line"))
        cursor.execute("INSERT INTO trips VALUES (?, ?, ?, ?, ?)", ("trip-special", "route-10", "SPECIAL", "Special direction", 0))
        cursor.execute("INSERT INTO trips VALUES (?, ?, ?, ?, ?)", ("trip-removed", "route-10", "REMOVED", "Removed direction", 1))
        cursor.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                ("trip-special", "10:00:00", "10:00:00", "100", 1),
                ("trip-removed", "11:00:00", "11:00:00", "100", 1),
            ],
        )
        conn.commit()
        conn.close()

        with patch("gtfs_manager.get_belgrade_time", return_value=datetime(2026, 5, 12, 9, 0, 0)):
            timetable = self.manager.get_timetable("10")

        self.assertIn("Special direction", timetable)
        self.assertNotIn("Removed direction", timetable)

    def test_gtfs_build_promotes_temp_database_after_validation(self):
        self._insert_stops()
        self._insert_service("OLD", tuesday=1)
        self._insert_route_trip("old-route", "OLD", "OLD", "old-trip")

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as z:
            z.writestr("routes.txt", "route_id,route_short_name,route_long_name\nnew-route,NEW,New Route\n")
            z.writestr("trips.txt", "route_id,service_id,trip_id,trip_headsign,direction_id\nnew-route,NEW,new-trip,New,0\n")
            z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n100,Origin,44.8,20.4\n200,Destination,44.81,20.41\n")
            z.writestr(
                "stop_times.txt",
                "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                "new-trip,10:00:00,10:00:00,100,1\n"
                "new-trip,10:05:00,10:05:00,200,2\n",
            )
            z.writestr("calendar.txt", "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nNEW,0,1,0,0,0,0,0,20260101,20261231\n")

        temp_db_path = self.manager._create_temp_db_path()
        with zipfile.ZipFile(BytesIO(zip_buffer.getvalue())) as z:
            self.manager._init_db(temp_db_path)
            self.manager._parse_zip(z, temp_db_path)
            self.manager._validate_built_db(temp_db_path)

        old_routes = self.manager.find_routes_between_stops(
            "100",
            "200",
            expand_nearby=False,
            current_time_obj=datetime(2026, 5, 12, 12, 0, 0),
        )
        self.assertEqual(["OLD"], [route["line"] for route in old_routes])

        self.manager._promote_db(temp_db_path)

        new_routes = self.manager.find_routes_between_stops(
            "100",
            "200",
            expand_nearby=False,
            current_time_obj=datetime(2026, 5, 12, 12, 0, 0),
        )
        self.assertEqual(["NEW"], [route["line"] for route in new_routes])

    def test_resolve_stop_name_prefers_public_plus_20000_over_low_raw_id(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("123", "Legacy raw", 44.8, 20.4),
                ("20123", "Public mapped", 44.81, 20.41),
            ],
        )
        conn.commit()
        conn.close()

        stops = self.manager.resolve_stop_name("123")

        self.assertEqual(["20123", "123"], [stop["stop_id"] for stop in stops])


if __name__ == "__main__":
    unittest.main()
