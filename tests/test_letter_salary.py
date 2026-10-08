import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scout.hh import Vacancy
from scout.letter import draft
from scout.scoring import Verdict
from scout.writer import compose
from scout.writer import _looks_like_letter


def vacancy(name):
    return Vacancy(id="1", name=name, employer="", url="", salary_from=None,
                   salary_to=None, currency="RUR", schedule=None,
                   experience="От 1 года до 3 лет", published="2026-10-01")


class LetterSalaryTests(unittest.TestCase):
    def test_quality_gate_rejects_markup_cliches_and_oversized_letter(self):
        base = ("Добрый день!\n\nОткликаюсь на позицию Python-разработчик.\n\n"
                + "Разрабатывал интеграции и автоматизировал процессы. " * 12
                + "\n\nОжидания по доходу - 170 000-200 000 рублей на руки."
                + "\n\nМои проекты: https://lazysagotku.github.io\n\n"
                + "Готов обсудить детали.\n\nС уважением,\nИван Архипов")
        self.assertTrue(_looks_like_letter(base))
        self.assertFalse(_looks_like_letter(base.replace("Разрабатывал", "**Разрабатывал**", 1)))
        self.assertFalse(_looks_like_letter(base.replace("Разрабатывал", "Не просто разрабатывал, а", 1)))
        self.assertFalse(_looks_like_letter(base.replace("процессы.", "процессы — быстро.", 1)))
        self.assertFalse(_looks_like_letter(base.replace("С уважением,", "Лишний абзац. " * 350 + "\n\nС уважением,")))
        too_many_words = base.replace("Ожидания по доходу", "Вёл релизы. " * 80 + "Ожидания по доходу")
        self.assertLess(len(too_many_words), 2500)
        self.assertFalse(_looks_like_letter(too_many_words))

    def test_python_template_mentions_170_to_200_net(self):
        letter = draft(vacancy("Python-разработчик"), Verdict(score=70, track="dev"))
        self.assertIn("170 000-200 000 рублей на руки", letter)

    def test_csharp_template_mentions_150_to_200_net(self):
        letter = draft(vacancy("C#-разработчик"), Verdict(score=70, track="dev"))
        self.assertIn("150 000-200 000 рублей на руки", letter)

    def test_support_template_mentions_200_net(self):
        letter = draft(vacancy("Инженер сопровождения"), Verdict(score=70, track="support"))
        self.assertIn("200 000 рублей на руки", letter)

    def test_ai_prompt_uses_python_range_for_python_resume(self):
        answer = ("Добрый день!\n\nОткликаюсь на позицию Python-разработчик.\n\n"
                  + "Разрабатывал интеграции и автоматизировал процессы. " * 12
                  + "\n\nОжидания по доходу - 170 000-200 000 рублей на руки."
                  + "\n\nМои проекты: https://lazysagotku.github.io\n\n"
                  + "Готов обсудить детали.\n\nС уважением,\nИван Архипов")
        with patch("scout.writer.binary", return_value="claude.exe"), \
             patch("scout.writer.reachable", return_value=True), \
             patch("scout.writer.facts_for", return_value="Подтверждённые факты"), \
             patch("scout.writer.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=answer)) as run:
            compose(vacancy("Python-разработчик"), Verdict(score=70, track="dev"), "Python-разработчик")
        self.assertIn("170 000-200 000 рублей на руки", run.call_args.kwargs["input"])

    def test_ai_letter_without_salary_is_rejected(self):
        answer = ("Добрый день!\n\nОткликаюсь на позицию Python-разработчик.\n\n"
                  + "Разрабатывал интеграции и автоматизировал процессы. " * 12
                  + "\n\nМои проекты: https://lazysagotku.github.io\n\n"
                  + "Готов обсудить детали.\n\nС уважением,\nИван Архипов")
        with patch("scout.writer.binary", return_value="claude.exe"), \
             patch("scout.writer.reachable", return_value=True), \
             patch("scout.writer.facts_for", return_value="Подтверждённые факты"), \
             patch("scout.writer.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=answer)):
            result = compose(vacancy("Python-разработчик"), Verdict(score=70, track="dev"), "Python-разработчик")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
