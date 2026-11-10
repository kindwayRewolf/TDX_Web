import csv
import io
import unittest
from unittest.mock import patch

import app


class RainSummaryTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def station(self, station_id, county, amount):
        return {
            "id": station_id,
            "name": "Station & <test>",
            "county": county,
            "town": "Town",
            "obs_time": "2026-11-10T17:00:00+08:00",
            "rainfall": {
                "10min": None, "1hr": amount, "3hr": 0,
                "6hr": 0, "12hr": 0, "24hr": amount,
            },
        }

    def test_text_metadata_rankings_and_filename(self):
        stations = [self.station("C0AH70", "County", 0)]
        stations += [self.station(f"S{i:02}", "County", i) for i in range(1, 23)]
        with patch.object(app, "_rain_station_items", return_value=(stations, "", 200)):
            response = self.client.get("/rain/summary.txt")
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/plain; charset=utf-8")
        self.assertEqual(response.headers["Content-Disposition"], 'inline; filename="Summary.txt"')
        self.assertIn("Total stations: 23", text)
        self.assertIn("C0AH70 | Station & <test> | County | Town | - | 0", text)
        top = text.split("Top 20 by 1-hour rainfall", 1)[1].split("Top 20 by 24-hour rainfall", 1)[0]
        rows = [line for line in top.splitlines() if line.startswith("S")]
        self.assertEqual(len(rows), 20)
        self.assertTrue(rows[0].startswith("S22 |"))
        self.assertTrue(rows[-1].startswith("S03 |"))
        self.assertIn("Per-county maximum", text)

    def test_filters_match_html_and_preserve_unicode(self):
        county = "\u81fa\u5317\u5e02"
        stations = [
            self.station("ONE", county, 2),
            self.station("TWO", "\u65b0\u5317\u5e02", 3),
        ]
        query = {"county": "\u53f0\u5317\u5e02", "ids": "one"}
        with patch.object(app, "_rain_station_items", return_value=(stations, "", 200)):
            text = self.client.get("/rain/summary.txt", query_string=query).get_data(as_text=True)
            html = self.client.get("/rain/summary", query_string=query)
        self.assertEqual(html.status_code, 200)
        self.assertIn(f"All stations in {county} (1)", text)
        self.assertIn("ONE |", text)
        self.assertNotIn("TWO |", text)
        self.assertNotIn("Per-county maximum", text)
        self.assertIn(b"/rain/summary.txt?", html.data)
        self.assertIn(b"ids=one", html.data)

    def test_empty_data_and_upstream_errors(self):
        with patch.object(app, "_rain_station_items", return_value=([], "", 200)):
            response = self.client.get("/rain/summary.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No data / no rainfall recorded.", response.get_data(as_text=True))
        with patch.object(app, "_rain_station_items", return_value=([], "CWA unavailable", 503)):
            for path in ("/rain/summary.txt", "/rain/summary"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn("CWA unavailable", response.get_data(as_text=True))

    def test_all_text_contains_every_station_and_json_field(self):
        stations = [self.station(f"S{i:02}", "County", i) for i in range(25)]
        stations[0]["name"] = '\u6e2c\u7ad9 | "A"'
        stations[0]["obs_time"] = "2026-11-10T17:00:00+08:00"
        stations[1]["obs_time"] = "2026-11-10T17:10:00+08:00"
        with patch.object(app, "_rain_station_items", return_value=(stations, "", 200)), \
                patch.object(app, "_rain_cache", {"fetched_at": 1794305627}), \
                patch.object(app, "_rain_api_calls_today", return_value=7):
            response = self.client.get("/rain/all.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/plain; charset=utf-8")
        self.assertEqual(response.headers["Content-Disposition"], 'inline; filename="all.txt"')
        metadata, table = response.get_data(as_text=True).split("\n\n", 1)
        self.assertIn("Fetched: 2026-11-10T18:13:47+08:00", metadata)
        self.assertIn("Fetched timestamp: 1794305627", metadata)
        self.assertIn("API calls today: 7", metadata)
        self.assertIn("Total stations: 25", metadata)
        rows = list(csv.reader(io.StringIO(table), delimiter="|"))
        self.assertEqual(rows[0], ["ID", "Station", "County", "Town", "Observation time", *app._RAIN_PERIODS])
        self.assertEqual(len(rows), len(stations) + 1)
        for station, row in zip(stations, rows[1:]):
            expected = [
                station["id"], station["name"], station["county"], station["town"], station["obs_time"],
                *("" if station["rainfall"][p] is None else str(station["rainfall"][p]) for p in app._RAIN_PERIODS),
            ]
            self.assertEqual(row, expected)

    def test_all_text_uses_api_filters_and_error_status(self):
        stations = [self.station("ONE", "\u81fa\u5317\u5e02", 1), self.station("TWO", "\u65b0\u5317\u5e02", 2)]
        with patch.object(app, "_rain_station_items", return_value=(stations, "", 200)) as fetch:
            response = self.client.get("/rain/all.txt?station=one&refresh=1")
            fetch.assert_called_with(force=True)
            self.assertIn("Total stations: 1", response.get_data(as_text=True))
            self.assertIn("ONE|", response.get_data(as_text=True))
            self.assertNotIn("TWO|", response.get_data(as_text=True))
            response = self.client.get("/rain/all.txt", query_string={"county": "\u53f0\u5317\u5e02"})
            self.assertIn("Total stations: 1", response.get_data(as_text=True))
            self.assertIn("ONE|", response.get_data(as_text=True))
            response = self.client.get("/rain/all.txt?station=unknown")
            self.assertEqual(response.status_code, 404)
            self.assertIn("was not found", response.get_data(as_text=True))
        with patch.object(app, "_rain_station_items", return_value=([], "CWA unavailable", 503)):
            response = self.client.get("/rain/all.txt")
            self.assertEqual(response.status_code, 503)
            self.assertIn("Error: CWA unavailable", response.get_data(as_text=True))
        with patch.object(app, "_rain_station_items", return_value=([], "", 200)):
            response = self.client.get("/rain/all.txt")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Total stations: 0", response.get_data(as_text=True))

    def test_commute_text_has_only_requested_stations_in_order(self):
        stations = [
            self.station("CAA030", "\u65b0\u5317\u5e02", 2),
            self.station("OTHER", "County", 3),
            self.station("C0AH70", "\u81fa\u5317\u5e02", 1),
        ]
        stations[0]["name"], stations[0]["town"] = "\u570b\u4e09S010K", "\u6c50\u6b62\u5340"
        stations[2]["name"], stations[2]["town"] = "\u677e\u5c71", "\u677e\u5c71\u5340"
        with patch.object(app, "_rain_station_items", return_value=(stations, "", 200)) as fetch, \
                patch.object(app, "_rain_cache", {"fetched_at": 1794305627}):
            response = self.client.get("/rain/commute.txt?station=OTHER&county=County&refresh=1")
        fetch.assert_called_once_with(force=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/plain; charset=utf-8")
        self.assertEqual(response.headers["Content-Disposition"], 'inline; filename="commute.txt"')
        metadata, table = response.get_data(as_text=True).split("\n\n", 1)
        self.assertIn("Total stations: 2", metadata)
        self.assertIn("2026-11-10T18:13:47+08:00", metadata)
        rows = list(csv.reader(io.StringIO(table), delimiter="|"))
        self.assertEqual([row[0] for row in rows[1:]], ["C0AH70", "CAA030"])
        self.assertEqual(rows[1][1:5], ["\u677e\u5c71", "\u81fa\u5317\u5e02", "\u677e\u5c71\u5340", stations[2]["obs_time"]])
        self.assertEqual(rows[2][1:5], ["\u570b\u4e09S010K", "\u65b0\u5317\u5e02", "\u6c50\u6b62\u5340", stations[0]["obs_time"]])
        self.assertEqual(rows[1][5:], ["", "1", "0", "0", "0", "1"])

    def test_commute_text_reports_missing_station_and_upstream_error(self):
        with patch.object(app, "_rain_station_items", return_value=([self.station("C0AH70", "County", 1)], "", 200)), \
                self.assertLogs(app.log, level="WARNING"):
            response = self.client.get("/rain/commute.txt")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Stations missing from rainfall data: CAA030", response.get_data(as_text=True))
        self.assertIn("C0AH70|", response.get_data(as_text=True))
        with patch.object(app, "_rain_station_items", return_value=([], "CWA unavailable", 503)):
            response = self.client.get("/rain/commute.txt")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Error: CWA unavailable", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
