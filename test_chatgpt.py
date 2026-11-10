import unittest

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


if __name__ == "__main__":
    unittest.main()
