import unittest
from datetime import datetime, timedelta

from scout.policy import classify_find, csharp_experience_ok, salary_line, select_mix


class PolicyTests(unittest.TestCase):
    def test_salary_expectations_are_net_and_track_specific(self):
        self.assertEqual(salary_line("python"), "Ожидания по доходу - 170 000-200 000 рублей на руки.")
        self.assertEqual(salary_line("csharp"), "Ожидания по доходу - 150 000-200 000 рублей на руки.")
        self.assertEqual(salary_line("support"), "Ожидания по доходу - 200 000 рублей на руки.")

    def test_python_developer_and_support_with_python_are_separate_tracks(self):
        self.assertEqual(classify_find({"name": "Python-разработчик"}), "python")
        self.assertEqual(classify_find({"name": "Инженер сопровождения Python-сервиса"}), "support")

    def test_csharp_is_limited_to_one_to_three_year_requirements(self):
        self.assertTrue(csharp_experience_ok("От 1 года до 3 лет"))
        self.assertFalse(csharp_experience_ok("От 3 до 6 лет"))
        self.assertFalse(csharp_experience_ok("Более 6 лет"))
        self.assertFalse(csharp_experience_ok(None))

    def test_mix_has_twelve_python_six_support_two_csharp_in_twenty(self):
        now = datetime(2026, 10, 1, 12, 0)
        items = []
        for track, title, exp in (
            ("python", "Python-разработчик", "От 3 до 6 лет"),
            ("support", "Инженер сопровождения", "Более 6 лет"),
            ("csharp", "C# разработчик", "От 1 года до 3 лет"),
        ):
            for n in range(25):
                items.append({
                    "id": f"{track}-{n}", "name": title, "experience": exp,
                    "status": "analyzed", "score": 80 - n,
                    "found_at": now.isoformat(timespec="seconds"),
                })
        selected = select_mix(items, limit=20, now=now)
        counts = {track: sum(classify_find(f) == track for f in selected)
                  for track in ("python", "support", "csharp")}
        self.assertEqual(counts, {"python": 12, "support": 6, "csharp": 2})
        first_ten = {track: sum(classify_find(f) == track for f in selected[:10])
                     for track in ("python", "support", "csharp")}
        self.assertEqual(first_ten, {"python": 6, "support": 3, "csharp": 1})

    def test_mix_does_not_fill_python_places_with_old_admin_vacancies(self):
        now = datetime(2026, 10, 1, 12, 0)
        items = [
            {"id": str(n), "name": "Системный администратор", "status": "analyzed",
             "score": 90, "found_at": (now - timedelta(days=1)).isoformat(timespec="seconds")}
            for n in range(25)
        ]
        items.append({"id": "old", "name": "Python-разработчик", "status": "analyzed",
                      "score": 95, "found_at": (now - timedelta(days=40)).isoformat(timespec="seconds")})
        selected = select_mix(items, limit=20, now=now)
        self.assertEqual(len(selected), 6)
        self.assertTrue(all(classify_find(f) == "support" for f in selected))

    def test_archived_dropped_and_weak_vacancies_are_excluded(self):
        now = datetime(2026, 10, 1, 12, 0)
        base = {"name": "Python-разработчик", "found_at": now.isoformat(timespec="seconds")}
        items = [
            {**base, "id": "good", "status": "analyzed", "score": 60},
            {**base, "id": "archived", "status": "analyzed", "score": 95, "archived_at": "2026-09-30"},
            {**base, "id": "dropped", "status": "dropped", "score": 95},
            {**base, "id": "weak", "status": "analyzed", "score": 20},
        ]
        self.assertEqual([f["id"] for f in select_mix(items, limit=10, now=now)], ["good"])


if __name__ == "__main__":
    unittest.main()
