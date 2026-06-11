import os
import sqlite3
import tempfile
import unittest


_IMPORT_DB_DIR = tempfile.TemporaryDirectory()
os.environ["GTFS_DB"] = os.path.join(_IMPORT_DB_DIR.name, "gtfs.db")

from bgprevoz_importer import BgLine, BgPrevozImporter, BgStop, DirectionImport  # noqa: E402
from gtfs_manager import GTFSManager  # noqa: E402


class BgPrevozImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "gtfs.db")
        self.manager = GTFSManager(db_path=self.db_path)
        self.importer = BgPrevozImporter(db_path=self.db_path, delay_seconds=0)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_calendar(self):
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO calendar
            VALUES ('ACTIVE', 1, 1, 1, 1, 1, 1, 1, '20260101', '20261231')
            """
        )
        conn.commit()
        conn.close()

    def test_match_or_create_stop_prefers_public_raw_mapping_before_nearest(self):
        conn = self._connect()
        conn.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("20703", "Pionirski park", 44.8120, 20.4620),
                ("20266", "Glavna posta", 44.81201, 20.46201),
            ],
        )
        conn.commit()

        matched = self.importer._match_or_create_stop(conn, "703", "Glavna posta", 44.81201, 20.46201)

        conn.close()
        self.assertEqual("20703", matched)

    def test_replace_line_uses_imported_pionirski_park_stop_ids_not_glavna_posta(self):
        conn = self._connect()
        self.importer._ensure_bg_tables(conn)
        conn.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("20703", "Pionirski park", 44.8120, 20.4620),
                ("20704", "Pionirski park", 44.8130, 20.4630),
                ("20266", "Glavna posta", 44.8140, 20.4640),
                ("20267", "Glavna posta", 44.8150, 20.4650),
            ],
        )
        conn.execute("INSERT INTO routes VALUES ('r58', '58', 'Old 58')")
        conn.execute("INSERT INTO trips VALUES ('old58', 'r58', 'ACTIVE', 'Old', 0)")
        conn.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                ("old58", "10:00:00", "10:00:00", "20266", 1),
                ("old58", "10:05:00", "10:05:00", "20267", 2),
            ],
        )
        line = BgLine(line_id="58-id", code="58", label="58")
        directions = [
            DirectionImport(
                direction_id=0,
                headsign="Direction A",
                stops=[
                    BgStop("703", "Pionirski park", 44.8120, 20.4620, 1, 0, "20703"),
                    BgStop("900", "Next", 44.8200, 20.4700, 2, 500, "20900"),
                ],
                offsets=[0, 300],
            ),
            DirectionImport(
                direction_id=1,
                headsign="Direction B",
                stops=[
                    BgStop("901", "Return", 44.8210, 20.4710, 1, 0, "20901"),
                    BgStop("704", "Pionirski park", 44.8130, 20.4630, 2, 500, "20704"),
                ],
                offsets=[0, 300],
            ),
        ]
        conn.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("20900", "Next", 44.8200, 20.4700),
                ("20901", "Return", 44.8210, 20.4710),
            ],
        )

        self.importer._replace_line(
            conn,
            line,
            directions,
            {
                0: {"weekday": ["10:00:00"], "saturday": [], "sunday": []},
                1: {"weekday": ["11:00:00"], "saturday": [], "sunday": []},
            },
        )
        conn.commit()

        rows = conn.execute(
            """
            SELECT st.stop_id
            FROM routes r
            JOIN trips t ON t.route_id = r.route_id
            JOIN stop_times st ON st.trip_id = t.trip_id
            WHERE r.route_short_name = '58'
            ORDER BY st.stop_id
            """
        ).fetchall()
        conn.close()

        stop_ids = {row["stop_id"] for row in rows}
        self.assertIn("20703", stop_ids)
        self.assertIn("20704", stop_ids)
        self.assertNotIn("20266", stop_ids)
        self.assertNotIn("20267", stop_ids)

    def test_get_line_route_prefers_bgprevoz_import_over_static_gsp_override(self):
        self._insert_calendar()
        conn = self._connect()
        conn.executemany(
            "INSERT INTO stops VALUES (?, ?, ?, ?)",
            [
                ("20703", "Pionirski park", 44.8120, 20.4620),
                ("20704", "Pionirski park", 44.8130, 20.4630),
                ("20900", "Next", 44.8200, 20.4700),
                ("20901", "Return", 44.8210, 20.4710),
            ],
        )
        conn.execute("INSERT INTO routes VALUES ('r58', '58', 'Imported 58')")
        conn.execute("INSERT INTO trips VALUES ('bg:58-id:0:weekday:0001', 'r58', 'ACTIVE', 'Direction A', 0)")
        conn.execute("INSERT INTO trips VALUES ('bg:58-id:1:weekday:0001', 'r58', 'ACTIVE', 'Direction B', 1)")
        conn.executemany(
            "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
            [
                ("bg:58-id:0:weekday:0001", "10:00:00", "10:00:00", "20703", 1),
                ("bg:58-id:0:weekday:0001", "10:05:00", "10:05:00", "20900", 2),
                ("bg:58-id:1:weekday:0001", "11:00:00", "11:00:00", "20901", 1),
                ("bg:58-id:1:weekday:0001", "11:05:00", "11:05:00", "20704", 2),
            ],
        )
        conn.execute("INSERT INTO metadata VALUES ('bgprevoz_line_hash:58', 'test-hash')")
        conn.commit()
        conn.close()

        routes = self.manager.get_line_route("58")

        self.assertEqual(["bgprevoz", "bgprevoz"], [route["source"] for route in routes])
        self.assertEqual({"20703", "20900"}, {stop["stop_id"] for stop in routes[0]["stops"]})
        self.assertEqual({"20704", "20901"}, {stop["stop_id"] for stop in routes[1]["stops"]})


if __name__ == "__main__":
    unittest.main()
