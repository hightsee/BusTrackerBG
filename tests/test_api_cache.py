import os
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch


_IMPORT_DB_DIR = tempfile.TemporaryDirectory()
os.environ["APP_DATA_DB"] = os.path.join(_IMPORT_DB_DIR.name, "app_data.db")
os.environ["GTFS_DB"] = os.path.join(_IMPORT_DB_DIR.name, "gtfs.db")
os.environ["JWT_SECRET"] = "test-secret-for-bustracker-unit-tests"

import api  # noqa: E402


class APICacheInvalidationTest(unittest.TestCase):
    def setUp(self):
        api.cache_clear()

    def tearDown(self):
        api.cache_clear()

    def _reset_stops(self, rows):
        conn = api.gtfs_manager._connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stops")
        cursor.executemany("INSERT INTO stops VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()

    def _reset_gtfs(self):
        conn = api.gtfs_manager._connect()
        cursor = conn.cursor()
        for table in ("routes", "trips", "stop_times", "stops", "calendar", "calendar_dates"):
            cursor.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()

    def test_cache_clear_can_target_transit_data_namespaces(self):
        api.cache_set("stop_lines", "100", ["10"], 300)
        api.cache_set("connected_stops", "100", {"stops": []}, 300)
        api.cache_set("geocode_address", "main 1", {"lat": 44.8}, 300)

        api.cache_clear(api.TRANSIT_DATA_CACHE_NAMESPACES)

        self.assertIsNone(api.cache_get("stop_lines", "100", 300))
        self.assertIsNone(api.cache_get("connected_stops", "100", 300))
        self.assertEqual({"lat": 44.8}, api.cache_get("geocode_address", "main 1", 300))

    def test_scheduled_gtfs_update_clears_transit_cache_on_success(self):
        api.cache_set("stop_search", "kalemegdan", {"matches": [1]}, 300)
        api.gtfs_manager.update_gtfs = Mock(return_value=True)

        with patch.object(api, "BgPrevozImporter") as importer_class:
            importer_class.return_value.apply.return_value = {"imported_lines": 0}
            self.assertTrue(api.scheduled_gtfs_update(force=True))

        api.gtfs_manager.update_gtfs.assert_called_once_with(force=True)
        self.assertIsNone(api.cache_get("stop_search", "kalemegdan", 300))

    def test_scheduled_gtfs_update_runs_bgprevoz_overlay_after_gtfs_success(self):
        api.gtfs_manager.update_gtfs = Mock(return_value=True)

        with patch.object(api, "BgPrevozImporter") as importer_class:
            importer_class.return_value.apply.return_value = {"imported_lines": 1}
            self.assertTrue(api.scheduled_gtfs_update(force=True))

        importer_class.assert_called_once()

    def test_scheduled_bgprevoz_update_clears_transit_cache_only_when_lines_imported(self):
        api.cache_set("stop_lines", "100", ["10"], 300)

        with patch.object(api, "BgPrevozImporter") as importer_class:
            importer_class.return_value.apply.return_value = {"imported_lines": 0}
            self.assertTrue(api.scheduled_bgprevoz_update())
            self.assertEqual(["10"], api.cache_get("stop_lines", "100", 300))

            importer_class.return_value.apply.return_value = {"imported_lines": 1}
            self.assertTrue(api.scheduled_bgprevoz_update())
            self.assertIsNone(api.cache_get("stop_lines", "100", 300))

    def test_resolve_station_id_prefers_public_plus_20000_over_low_raw_id(self):
        self._reset_stops([
            ("123", "Legacy raw", 44.8, 20.4),
            ("20123", "Public mapped", 44.81, 20.41),
        ])

        station = api.resolve_station_id("123")

        self.assertEqual("20123", station["raw_stop_id"])
        self.assertEqual("123", station["station_id"] if "station_id" in station else station["sid"])

    def test_resolve_station_id_uses_exact_raw_id_and_low_direct_fallback(self):
        self._reset_stops([
            ("123", "Legacy raw", 44.8, 20.4),
        ])

        low_station = api.resolve_station_id("123")
        missing_raw_station = api.resolve_station_id("20123")

        self.assertEqual("123", low_station["raw_stop_id"])
        self.assertIsNone(missing_raw_station)

    def test_routing_falls_back_to_walk_expanded_candidates(self):
        self._reset_gtfs()
        conn = api.gtfs_manager._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("20182", "Skola Josif Pancic", 44.78154, 20.417828),
                ("21653", "Cukarica", 44.786426, 20.415076),
                ("20832", "Zvezdara", 44.7928648244, 20.5036892411),
                ("21577", "Zvezdara /pijaca/", 44.79405, 20.504543),
                ("21428", "Bajdina", 44.779368, 20.52586),
            ],
        )
        cursor.execute("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("ALL", 1, 1, 1, 1, 1, 1, 1, "20260101", "20261231"))
        cursor.executemany(
            "INSERT INTO routes VALUES (?, ?, ?)",
            [("r55", "55", "55"), ("r309", "309", "309")],
        )
        cursor.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?)",
            [("t55", "r55", "ALL", "Zvezdara", 0), ("t309", "r309", "ALL", "Bajdina", 0)],
        )
        cursor.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                ("t55", "10:00:00", "10:00:00", "21653", 1),
                ("t55", "10:20:00", "10:20:00", "20832", 2),
                ("t309", "10:25:00", "10:25:00", "21577", 1),
                ("t309", "10:45:00", "10:45:00", "21428", 2),
            ],
        )
        conn.commit()
        conn.close()

        with api.app.test_request_context("/api/routing?from=182&to=1428"):
            response = api.find_routing()

        payload = response.get_json()
        self.assertEqual(("182", "1428"), (payload["from"], payload["to"]))
        self.assertEqual("transfer", payload["possible_routes"][0]["type"])
        self.assertEqual(("55", "309"), (payload["possible_routes"][0]["line1"], payload["possible_routes"][0]["line2"]))
        self.assertEqual("walk_expanded", payload["possible_routes"][0]["source"])
        self.assertEqual("1653", payload["possible_routes"][0]["origin_station_id"])

    def test_routing_can_return_two_transfer_route_from_requested_station(self):
        self._reset_gtfs()
        conn = api.gtfs_manager._connect()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("20182", "Skola Josif Pancic", 44.78154, 20.417828),
                ("20503", "Ada Ciganlija", 44.7884938958, 20.4200184945),
                ("20832", "Zvezdara", 44.7928648244, 20.5036892411),
                ("21577", "Zvezdara /pijaca/", 44.79405, 20.504543),
                ("21428", "Bajdina", 44.779368, 20.52586),
            ],
        )
        cursor.execute("INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("ALL", 1, 1, 1, 1, 1, 1, 1, "20260101", "20261231"))
        cursor.executemany(
            "INSERT INTO routes VALUES (?, ?, ?)",
            [("r23", "23", "23"), ("r55", "55", "55"), ("r309", "309", "309")],
        )
        cursor.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?, ?)",
            [
                ("t23", "r23", "ALL", "Ada", 0),
                ("t55", "r55", "ALL", "Zvezdara", 0),
                ("t309", "r309", "ALL", "Bajdina", 0),
            ],
        )
        cursor.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                ("t23", "10:00:00", "10:00:00", "20182", 1),
                ("t23", "10:05:00", "10:05:00", "20503", 2),
                ("t55", "10:10:00", "10:10:00", "20503", 1),
                ("t55", "10:30:00", "10:30:00", "20832", 2),
                ("t309", "10:35:00", "10:35:00", "21577", 1),
                ("t309", "10:55:00", "10:55:00", "21428", 2),
            ],
        )
        conn.commit()
        conn.close()

        with api.app.test_request_context("/api/routing?from=182&to=1428"):
            response = api.find_routing()

        route = response.get_json()["possible_routes"][0]
        self.assertEqual("multi_transfer", route["type"])
        self.assertEqual(("23", "55", "309"), (route["line1"], route["line2"], route["line3"]))
        self.assertEqual("182", route["origin_station_id"])
        self.assertEqual("1428", route["dest_station_id"])

    def test_favorite_name_validation_rejects_blank_and_overlong_names(self):
        self.assertIsNone(api.normalize_favorite_name("   "))
        self.assertIsNone(api.normalize_favorite_name("x" * 81))
        self.assertEqual("Home Stop", api.normalize_favorite_name("  Home   Stop  "))

        with api.app.test_request_context("/api/favorites", method="POST", json={"name": "   ", "station_id": "123"}):
            response, status = api.add_favorite.__wrapped__({"id": 1})

        self.assertEqual(400, status)
        self.assertEqual({"error": "name must be between 1 and 80 characters"}, response.get_json())

    def test_predict_line_separates_active_buses_from_scheduled_trips(self):
        api.gtfs_manager.predict_bus_position = Mock(return_value=[
            {
                "status": "not_started",
                "trip_id": "future-trip",
                "direction": "Downtown",
            },
            {
                "status": "in_transit",
                "trip_id": "active-trip",
                "direction": "Downtown",
            },
        ])

        with api.app.test_request_context("/api/predict/line?line=10"):
            response = api.predict_line()

        payload = response.get_json()
        self.assertEqual(["active-trip"], [item["trip_id"] for item in payload["active_buses"]])
        self.assertEqual(["future-trip"], [item["trip_id"] for item in payload["scheduled_trips"]])

    def test_journey_rejects_origin_coordinates_outside_belgrade(self):
        with api.app.test_request_context(
            "/api/journey",
            method="POST",
            json={
                "origin": {"lat": 91, "lon": 181},
                "destination": {"station_id": "1"},
            },
        ):
            response, status = api.find_journey()

        self.assertEqual(400, status)
        self.assertEqual({"error": "origin coordinates are outside the supported Belgrade area"}, response.get_json())

    def test_password_reset_invalidates_existing_jwt(self):
        client = api.app.test_client()
        username = f"user-{uuid.uuid4()}"

        register_response = client.post("/api/register", json={"username": username, "password": "old-password"})
        self.assertEqual(201, register_response.status_code)
        login_response = client.post("/api/login", json={"username": username, "password": "old-password"})
        self.assertEqual(200, login_response.status_code)
        old_token = login_response.get_json()["token"]

        reset_response = client.post("/api/password-reset/request", json={"username": username})
        self.assertEqual(200, reset_response.status_code)
        reset_token = reset_response.get_json()["reset_token"]
        confirm_response = client.post("/api/password-reset/confirm", json={"token": reset_token, "password": "new-password"})
        self.assertEqual(200, confirm_response.status_code)

        favorites_response = client.get("/api/favorites", headers={"Authorization": f"Bearer {old_token}"})
        self.assertEqual(401, favorites_response.status_code)

    def test_password_reset_request_hides_token_outside_local_dev(self):
        client = api.app.test_client()
        username = f"user-{uuid.uuid4()}"
        self.assertEqual(201, client.post("/api/register", json={"username": username, "password": "old-password"}).status_code)

        with patch.object(api, "IS_LOCAL_DEV", False):
            reset_response = client.post("/api/password-reset/request", json={"username": username})

        self.assertEqual(200, reset_response.status_code)
        self.assertNotIn("reset_token", reset_response.get_json())


if __name__ == "__main__":
    unittest.main()
