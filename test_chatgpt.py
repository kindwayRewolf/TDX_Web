import unittest
from unittest.mock import Mock, patch

import app


class ChatgptPageTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_html_pages_and_version_links(self):
        for path, filename in (
            ("/chatgpt", "chatgpt.html"),
            ("/chatgpt/version.html", "version.html"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "text/html")
                text = response.get_data(as_text=True)
                self.assertIn(f"<h1>{filename}</h1>", text)
                self.assertIn("Date: 2026-11-10", text)
                self.assertIn("Version: 0.1", text)
                if path == "/chatgpt":
                    for extension in ("html", "txt", "json"):
                        self.assertIn(f'href="/chatgpt/version.{extension}"', text)

    def test_text_version(self):
        response = self.client.get("/chatgpt/version.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/plain; charset=utf-8")
        self.assertEqual(
            response.get_data(as_text=True),
            "Filename: version.txt\nDate: 2026-11-10\nVersion: 0.1\n",
        )

    def test_json_version(self):
        response = self.client.get("/chatgpt/version.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertEqual(
            response.get_json(),
            {"filename": "version.json", "date": "2026-11-10", "version": "0.1"},
        )

    def test_rain_station_from_pages_snapshot(self):
        station = {
            "id": "C0AH70", "name": "\u6e2c\u7ad9 <test>", "county": "County",
            "town": "Town", "obs_time": "2026-11-10T18:00:00+08:00",
            "rainfall": dict(zip(app._RAIN_PERIODS, [None, 0, 3.2, 4.5, 6, 8])),
        }
        upstream = Mock()
        upstream.json.return_value = {"stations": [station], "fetched_at": 1794305627}
        with patch.object(app.requests, "get", return_value=upstream) as get:
            response = self.client.get("/chatgpt/rain.html?StationID=%20c0ah70%20")
        get.assert_called_once_with("https://kindwayrewolf.github.io/TDX_Web/rain.json", timeout=20)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        text = response.get_data(as_text=True)
        self.assertIn("\u6e2c\u7ad9 &lt;test&gt;", text)
        self.assertIn("<dd>C0AH70</dd>", text)
        self.assertIn("2026-11-10T18:00:00+08:00", text)
        self.assertIn("2026-11-10T18:13:47+08:00", text)
        for period, amount in zip(app._RAIN_PERIODS, ["-", "0", "3.2", "4.5", "6", "8"]):
            self.assertIn(f'<th scope="row">{period}</th><td>{amount}</td>', text)
        self.assertNotIn("<script", text)

    def test_rain_station_input_and_lookup_errors(self):
        with patch.object(app.requests, "get") as get:
            for query in ("", "?StationID", "?StationID=%20"):
                with self.subTest(query=query):
                    response = self.client.get("/chatgpt/rain.html" + query)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(b"StationID is required", response.data)
            get.assert_not_called()
        upstream = Mock()
        upstream.json.return_value = {"stations": [], "fetched_at": 1794305627}
        with patch.object(app.requests, "get", return_value=upstream):
            response = self.client.get("/chatgpt/rain.html?StationID=UNKNOWN")
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"was not found", response.data)

    def test_rain_snapshot_failures_are_explicit(self):
        cases = [
            (app.requests.Timeout(), 504, "timed out"),
            (app.requests.HTTPError("404"), 502, "Could not retrieve"),
            (ValueError("Invalid JSON"), 502, "invalid rainfall snapshot"),
        ]
        for error, status, message in cases:
            for path in ("/chatgpt/rain.html?StationID=C0AH70", "/chatgpt/stationID.html"):
                with self.subTest(error=error, path=path):
                    upstream = Mock()
                    upstream.json.side_effect = error
                    with patch.object(app.requests, "get", return_value=upstream), \
                            self.assertLogs(app.log, level="WARNING"):
                        response = self.client.get(path)
                    self.assertEqual(response.status_code, status)
                    self.assertIn(message, response.get_data(as_text=True))
        for payload in ({}, {"stations": [None]}, {"stations": [{"id": "C0AH70"}]}):
            with self.subTest(payload=payload):
                upstream = Mock()
                upstream.json.return_value = payload
                with patch.object(app.requests, "get", return_value=upstream), \
                        self.assertLogs(app.log, level="WARNING"):
                    response = self.client.get("/chatgpt/rain.html?StationID=C0AH70")
                self.assertEqual(response.status_code, 502)

    def test_all_station_ids_are_listed_and_linked(self):
        stations = [
            {
                "id": station_id, "name": "Name <test>", "county": "County", "town": "Town",
                "obs_time": "", "rainfall": dict.fromkeys(app._RAIN_PERIODS),
            }
            for station_id in ("ZZ9999", "C0AH70", "AA0001")
        ]
        upstream = Mock()
        upstream.json.return_value = {"stations": stations, "fetched_at": 1794305627}
        with patch.object(app.requests, "get", return_value=upstream) as get:
            response = self.client.get("/chatgpt/stationID.html")
        get.assert_called_once_with(app._PAGES_RAIN_URL, timeout=20)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        text = response.get_data(as_text=True)
        self.assertIn("Total stations: 3", text)
        self.assertIn("2026-11-10T18:13:47+08:00", text)
        self.assertIn("Name &lt;test&gt;", text)
        for station in stations:
            self.assertIn(f'href="/chatgpt/rain.html?StationID={station["id"]}"', text)
        self.assertLess(text.index(">AA0001</a>"), text.index(">C0AH70</a>"))
        self.assertLess(text.index(">C0AH70</a>"), text.index(">ZZ9999</a>"))
        self.assertNotIn("<script", text)

    def test_empty_station_snapshot(self):
        upstream = Mock()
        upstream.json.return_value = {"stations": [], "fetched_at": 1794305627}
        with patch.object(app.requests, "get", return_value=upstream):
            response = self.client.get("/chatgpt/stationID.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Total stations: 0", response.data)
        self.assertIn(b"No stations in the published snapshot.", response.data)


if __name__ == "__main__":
    unittest.main()
