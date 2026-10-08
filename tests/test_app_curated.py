import unittest
from datetime import datetime
from unittest.mock import patch

from scout.app import curated, application_preview


class CuratedEndpointTests(unittest.TestCase):
    def test_application_preview_is_read_only(self):
        with patch("scout.app.store.list_finds", return_value=[]), \
             patch("scout.response_form.all_forms", return_value={}):
            result = application_preview(limit=10)
        self.assertEqual(result, {"mode": "dry-run", "items": [], "ready": 0})

    def test_endpoint_returns_weighted_shortlist(self):
        found_at = datetime.now().isoformat(timespec="seconds")
        finds = [
            {"id": str(n), "name": "Python-разработчик", "experience": "От 3 до 6 лет",
             "status": "analyzed", "score": 70, "found_at": found_at}
            for n in range(10)
        ] + [
            {"id": f"s{n}", "name": "Инженер сопровождения", "experience": "Более 6 лет",
             "status": "analyzed", "score": 90, "found_at": found_at}
            for n in range(10)
        ]
        with patch("scout.app.store.list_finds", return_value=finds):
            result = curated(limit=10)
        self.assertEqual(len(result), 9)
        self.assertEqual(sum(f["name"].startswith("Python") for f in result), 6)
        self.assertEqual(sum(f["name"].startswith("Инженер") for f in result), 3)


if __name__ == "__main__":
    unittest.main()
