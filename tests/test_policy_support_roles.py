"""Роли эксплуатации не должны выпадать из короткой выдачи.

Заголовки взяты из реальной базы: до правки политика возвращала по ним пустой
трек, и 146 из 493 сильных находок не попадали в выдачу ни в одну квоту.
"""

import unittest
from datetime import datetime

from scout.policy import classify_find, select_mix

NOW = datetime(2026, 10, 6, 12, 0)


class SupportRoleTests(unittest.TestCase):
    def test_real_operations_titles_land_in_support(self):
        titles = [
            "Администратор баз данных",
            "Администратор серверов Linux",
            "Инженер linux",
            "Инженер мониторинга",
            "Главный администратор СУБД (DBA, L3)",
            "Инженер платформы сборки (ОС Astra Linux)",
            "Database Administrator/DBA (MSSQL)",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(classify_find({"name": title}), "support")

    def test_general_engineer_title_stays_outside_support_quota(self):
        # Общее «инженер» затянуло бы QA и дата-инженеров в квоту поддержки.
        self.assertIsNone(classify_find({"name": "Инженер данных"}))
        self.assertIsNone(classify_find({"name": "QA инженер/Инженер по тестированию"}))

    def test_python_title_keeps_its_own_quota(self):
        self.assertEqual(classify_find({"name": "Python-разработчик"}), "python")
        self.assertEqual(classify_find({"name": "Backend-разработчик (Python/FastAPI)"}), "python")

    def test_support_role_reaches_short_list(self):
        finds = [{"id": str(number), "name": "Администратор баз данных",
                  "status": "analyzed", "score": 85, "found_at": NOW.isoformat()}
                 for number in range(5)]
        selected = select_mix(finds, limit=10, now=NOW)
        self.assertEqual(len(selected), 3)      # квота поддержки при лимите 10


if __name__ == "__main__":
    unittest.main()
