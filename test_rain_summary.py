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


if __name__ == "__main__":
    unittest.main()
