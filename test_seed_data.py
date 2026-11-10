import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import app


class StationSeedTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.seed = directory / "seed_data.json"
        self.cache = directory / "seed_data.cache.json"
        self.stack.enter_context(patch.object(app, "_SEED_FILE", self.seed))
        self.stack.enter_context(patch.object(app, "_SEED_CACHE_FILE", self.cache))
        for name in ("STATIONS", "_STATION_CLASSES", "_STATION_PHONES",
                     "_STATION_ADDRESSES", "_VALID_CODES"):
            self.stack.enter_context(patch.object(app, name, {} if name != "_VALID_CODES" else set()))
        self.stack.enter_context(patch.object(app, "_STATION_GROUPS", []))
        self.stack.enter_context(patch.object(app, "_seed_generated_at", ""))
        self.baseline = {
            "generated_at": "2026-01-01T00:00:00",
            "stations": {"Bundled": "1000"},
            "station_classes": {"1000": 2},
            "station_groups": [{"city": "City", "codes": ["1000"]}],
            "station_phones": {"1000": "123"},
            "station_addresses": {"1000": "Address"},
        }
        self.seed.write_text(json.dumps(self.baseline), encoding="utf-8")

    def test_missing_cache_loads_bundled_seed(self):
        with self.assertLogs(app.log, level="INFO") as logs:
            app._load_seed_data()
        self.assertIn("seed_data.cache.json not found", "\n".join(logs.output))
        self.assertEqual(app.STATIONS, self.baseline["stations"])
        self.assertEqual(app._STATION_CLASSES, {"1000": 2})
        self.assertEqual(app._STATION_GROUPS, self.baseline["station_groups"])
        self.assertEqual(app._STATION_PHONES, {"1000": "123"})
        self.assertEqual(app._STATION_ADDRESSES, {"1000": "Address"})

    def test_valid_cache_takes_priority_even_when_old(self):
        cached = {**self.baseline, "stations": {"Cached": "2000"}}
        self.cache.write_text(json.dumps(cached), encoding="utf-8")
        app._load_seed_data()
        self.assertEqual(app.STATIONS, {"Cached": "2000"})
        self.assertEqual(app._seed_generated_at, cached["generated_at"])

    def test_invalid_cache_falls_back_without_partial_data(self):
        invalid = [
            "{broken", "[]", '{"stations": {}}',
            json.dumps({**self.baseline, "station_classes": {"1000": "bad"}}),
            json.dumps({**self.baseline, "station_groups": [{}]}),
            json.dumps({**self.baseline, "generated_at": "bad"}),
            json.dumps({**self.baseline, "station_phones": {"1000": []}}),
        ]
        for text in invalid:
            with self.subTest(text=text):
                self.cache.write_text(text, encoding="utf-8")
                with self.assertLogs(app.log, level="WARNING"):
                    app._load_seed_data()
                self.assertEqual(app.STATIONS, self.baseline["stations"])
                self.assertEqual(app._STATION_GROUPS, self.baseline["station_groups"])

    def test_unusable_sources_are_reported(self):
        self.seed.unlink()
        with self.assertLogs(app.log, level="WARNING") as logs:
            app._load_seed_data()
        self.assertIn("No usable station cache or bundled seed", "\n".join(logs.output))
        self.assertEqual(app.STATIONS, {})

    def test_api_refresh_writes_only_runtime_cache_and_round_trips(self):
        original_seed = self.seed.read_bytes()
        app._STATION_CLASSES["legacy"] = 4
        stations = [
            {
                "StationID": f"TEST{i}",
                "StationName": {"Zh_tw": f"Station {i}"},
                "StationClass": "2",
                "LocationCity": "City",
                "StationPhone": "123",
                "StationAddress": "Address",
            }
            for i in range(50)
        ]
        with patch.object(app, "api_get", return_value=stations), \
                patch.object(app, "_load_branch_lines", return_value=[]):
            self.assertTrue(app._load_stations_from_api())
        self.assertEqual(self.seed.read_bytes(), original_seed)
        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(len(cached["stations"]), 50)
        self.assertEqual(cached["station_classes"]["legacy"], 4)
        self.assertFalse(self.cache.with_suffix(".tmp").exists())
        app.STATIONS.clear()
        app._load_seed_data()
        self.assertEqual(app.STATIONS, cached["stations"])
        self.assertEqual(app._seed_generated_at, cached["generated_at"])


if __name__ == "__main__":
    unittest.main()
