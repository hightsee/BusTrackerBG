"""Supplemental route-stop corrections from gsp.rs.

These overrides do not mutate GTFS schedules. They are used only where the
published route-stop pattern is newer than the GTFS route pattern.
"""

GSP_ROUTE_OVERRIDES = {
    "58": [
        {
            "direction_id": 0,
            "headsign": "Novi Zeleznik",
            "source_url": "https://www.gsp.rs/linija.aspx?id=58",
            "station_ids": [
                "2113", "352", "492", "356", "357", "704", "268", "271",
                "493", "495", "497", "179", "181", "183", "185", "187",
                "1035", "1037", "1039", "982", "984", "2469", "986",
                "988", "990", "992", "994", "961", "959", "958", "996",
                "998",
            ],
        },
        {
            "direction_id": 1,
            "headsign": "Pancevacki most /Zeleznicka stanica/",
            "source_url": "https://www.gsp.rs/linija.aspx?id=58",
            "station_ids": [
                "999", "997", "957", "467", "960", "995", "993", "991",
                "989", "987", "2470", "985", "983", "1040", "1038",
                "1036", "188", "186", "184", "182", "505", "503", "501",
                "499", "498", "496", "494", "272", "270", "269", "703",
                "358", "355", "1034", "720", "2131",
            ],
        },
    ],
}


def public_stop_id(stop_id: str) -> str:
    value = str(stop_id or "").strip()
    return str(int(value) - 20000) if value.isdigit() and int(value) >= 20000 else value


def raw_stop_id(station_id: str) -> str:
    value = str(station_id or "").strip()
    return str(int(value) + 20000) if value.isdigit() and int(value) < 20000 else value


def override_lines_for_stop(stop_id: str):
    station_id = public_stop_id(stop_id)
    lines = []
    for line, directions in GSP_ROUTE_OVERRIDES.items():
        if any(station_id in direction["station_ids"] for direction in directions):
            lines.append(line)
    return lines


def override_shared_lines_between(origin_stop_id: str, dest_stop_id: str):
    origin_station_id = public_stop_id(origin_stop_id)
    dest_station_id = public_stop_id(dest_stop_id)
    lines = []

    for line, directions in GSP_ROUTE_OVERRIDES.items():
        for direction in directions:
            station_ids = direction["station_ids"]
            try:
                origin_index = station_ids.index(origin_station_id)
                dest_index = station_ids.index(dest_station_id)
            except ValueError:
                continue

            if origin_index < dest_index:
                lines.append(line)
                break

    return lines
