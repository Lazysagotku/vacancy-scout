import unittest

from scout.hh import Vacancy
from scout.prescore import prescore
from scout.scoring import score


def vacancy(name, amount):
    return Vacancy(id="1", name=name, employer="", url="", salary_from=amount,
                   salary_to=amount, currency="RUR", schedule=None,
                   experience="От 1 года до 3 лет", published="2026-10-01",
                   description="Python C# SQL PostgreSQL поддержка")


class SalaryScoringTests(unittest.TestCase):
    def test_prescore_uses_track_specific_floor(self):
        cases = [
            ("C#-разработчик", 155_000, False),
            ("Python-разработчик", 160_000, True),
            ("Инженер сопровождения", 180_000, True),
        ]
        for name, amount, below in cases:
            with self.subTest(name=name):
                notes = prescore({"name": name, "salary_from": amount})["notes"]
                self.assertEqual("вилка ниже планки" in notes, below)

    def test_full_scoring_uses_track_specific_floor(self):
        self.assertFalse(any("вилка ниже планки" in note for note in score(vacancy("C#-разработчик", 155_000)).notes))
        self.assertTrue(any("вилка ниже планки" in note for note in score(vacancy("Python-разработчик", 160_000)).notes))


if __name__ == "__main__":
    unittest.main()
