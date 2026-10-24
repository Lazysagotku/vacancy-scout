"""Регрессии вокруг зарплатной строки и треков скоринга.

Треков в скоринге больше, чем зарплатных: `detect_track` умеет возвращать
«ai» и None. До этих тестов `draft` на таких треках падала ValueError, и один
такой вакансия останавливала разбор всей пачки.
"""

import logging
import unittest
from unittest.mock import patch

from scout import analysis
from scout.hh import Vacancy
from scout.letter import draft
from scout.scoring import Verdict


def vacancy(name, description=""):
    return Vacancy(id="1", name=name, employer="", url="", salary_from=None,
                   salary_to=None, currency="RUR", schedule=None,
                   experience="От 1 года до 3 лет", published="2026-10-01",
                   description=description)


class DraftTrackTests(unittest.TestCase):
    def test_ai_track_does_not_raise_and_uses_resume_salary(self):
        letter = draft(vacancy("AI инженер (Python)", "Python, LLM, агенты"),
                       Verdict(score=70, track="ai"))
        self.assertIn("рублей на руки", letter)

    def test_unknown_track_falls_back_to_support_salary(self):
        letter = draft(vacancy("Специалист"), Verdict(score=70, track=None))
        self.assertIn("200 000 рублей на руки", letter)

    def test_explicit_resume_wins_over_scoring_track(self):
        letter = draft(vacancy("Technical support engineer", "Python в проде"),
                       Verdict(score=70, track="support"), None, "Python-разработчик")
        self.assertIn("170 000-200 000 рублей на руки", letter)


class BatchIsolationTests(unittest.TestCase):
    def test_one_broken_card_does_not_stop_the_batch(self):
        finds = [{"id": "1", "name": "Плохая", "description": "текст"},
                 {"id": "2", "name": "Хорошая", "description": "текст"}]

        def save(find, description, write_letter=True):
            if find["id"] == "1":
                raise ValueError("сломанная карточка")
            return find

        with patch("scout.analysis._save", side_effect=save), \
             patch("scout.analysis.catch_up_letters", return_value=0), \
             patch("scout.analysis.collector.fetch_details", return_value={}):
            logging.disable(logging.CRITICAL)
            try:
                analyzed = analysis.analyze_many(finds, letter_budget=0)
            finally:
                logging.disable(logging.NOTSET)
        self.assertEqual(analyzed, 1)


class WriterCredentialsTests(unittest.TestCase):
    """CLI писем не должен зависеть от консоли, из которой запущен сервис."""

    def test_gateway_variables_are_stripped(self):
        import os
        from scout import writer
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://example.invalid",
                                     "ANTHROPIC_AUTH_TOKEN": "x",
                                     "PATH": os.environ.get("PATH", "")}, clear=False):
            env = writer._own_credentials()
        for name in writer.GATEWAY_VARS:
            self.assertNotIn(name, env)
        self.assertIn("PATH", env)


if __name__ == "__main__":
    unittest.main()
