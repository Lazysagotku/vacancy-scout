import unittest
from unittest.mock import patch

from scout import runner
from scout.collector import CollectError
from scout.search_sources import SearchSource


class RunnerSourcesTests(unittest.TestCase):
    def test_failed_saved_link_does_not_block_builtin_search(self):
        old = "https://hh.ru/search/vacancy?resume=expired"
        fallback = "https://hh.ru/search/vacancy?text=python"
        settings = {"search_url": old, "analyze_limit": "0", "interval_minutes": "180"}
        vacancy = {"id": "123456", "name": "Python-разработчик", "url": "https://hh.ru/vacancy/123456"}

        def collect(url, **kwargs):
            if url == old:
                raise CollectError("hh ответил 404")
            if url == fallback:
                return [vacancy]
            raise AssertionError(url)

        runner._state.busy = False
        runner._state.running = False
        with patch("scout.runner.store.get_setting", side_effect=lambda key, default="": settings.get(key, default)), \
             patch("scout.runner.store.start_run", return_value=1), \
             patch("scout.runner.store.finish_run"), \
             patch("scout.runner.store.known_ids", return_value=set()), \
             patch("scout.runner.store.save_finds", return_value=1), \
             patch("scout.runner.collector.is_authorized", return_value=False), \
             patch("scout.runner.collect", side_effect=collect), \
             patch("scout.runner.built_in_sources", return_value=[SearchSource("python", "Python", fallback)]):
            result = runner.scan_once()

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["fresh"], 1)
        self.assertIsNone(result["error"])
        self.assertIn("404", result["note"])


if __name__ == "__main__":
    unittest.main()
