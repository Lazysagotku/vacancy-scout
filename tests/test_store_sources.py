import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scout import store


class StoreSourceTests(unittest.TestCase):
    def test_each_vacancy_keeps_its_own_search_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, "DB", Path(folder) / "test.db"):
            store.init()
            store.save_finds([
                {"id": "111111", "name": "Python-разработчик", "query": "Python-разработчик"},
                {"id": "222222", "name": "Инженер сопровождения", "query": "Сопровождение"},
            ], query="смешанный поиск")
            self.assertEqual(store.get_find("111111")["query"], "Python-разработчик")
            self.assertEqual(store.get_find("222222")["query"], "Сопровождение")


if __name__ == "__main__":
    unittest.main()
