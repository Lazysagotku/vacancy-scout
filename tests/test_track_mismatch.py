"""Расхождение двух классификаторов не должно блокировать отклик навсегда.

`policy.classify_find` смотрит на роль в заголовке, `profile.resume_for` - на
стек в описании. На 4000 находок базы они расходятся в 32 случаях, например
«Technical support engineer» с Python-резюме. Проверка письма и стажа идёт по
приложенному резюме, иначе такие находки вечно висят в блокерах, а
`catch_up_letters` переписывает им письмо на каждом прогоне.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from scout import analysis
from scout.application_plan import evaluate
from scout.policy import classify_find

NOW = datetime(2026, 10, 1, 12, 0)
PYTHON_SALARY = "Ожидания по доходу - 170 000-200 000 рублей на руки."


def letter_with(salary):
    return ("Добрый день!\n\nОткликаюсь на позицию Technical support engineer.\n\n"
            + "Разрабатывал интеграции и автоматизировал процессы. " * 12
            + "\n\n" + salary
            + "\n\nМои проекты: https://lazysagotku.github.io\n\n"
            + "Готов обсудить детали.\n\nС уважением,\nИван Архипов")


def mismatched(**changes):
    item = {
        "id": "777", "name": "Technical support engineer", "status": "analyzed",
        "score": 85, "found_at": NOW.isoformat(), "analyzed_at": NOW.isoformat(),
        "description": "Поддержка сервисов, Python, SQL", "experience": "От 3 до 6 лет",
        "opinion": "Рекомендую", "verdict": "сильный матч", "blockers": "",
        "resume": "Python-разработчик", "letter_kind": "claude",
        "letter": letter_with(PYTHON_SALARY),
    }
    item.update(changes)
    return item


class TrackMismatchTests(unittest.TestCase):
    def test_fixture_really_is_a_mismatch(self):
        self.assertEqual(classify_find(mismatched()), "support")

    def test_letter_under_resume_salary_is_eligible(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        result = evaluate(mismatched(), form=form, now=NOW)
        self.assertEqual(result["reasons"], [])

    def test_catch_up_does_not_rewrite_letter_matching_resume(self):
        with patch("scout.analysis.store.list_finds", return_value=[mismatched()]), \
             patch("scout.analysis.write_letter") as write:
            self.assertEqual(analysis.catch_up_letters(5, now=NOW), 0)
        write.assert_not_called()

    def test_catch_up_rewrites_letter_with_salary_of_another_track(self):
        stale = mismatched(letter=letter_with("Ожидания по доходу - 200 000 рублей на руки."))
        with patch("scout.analysis.store.list_finds", return_value=[stale]), \
             patch("scout.analysis.write_letter", return_value=stale) as write:
            self.assertEqual(analysis.catch_up_letters(5, now=NOW), 1)
        write.assert_called_once_with(stale)

    def test_find_without_resume_is_blocked_with_clear_reason(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        result = evaluate(mismatched(resume=""), form=form, now=NOW)
        self.assertIn("Резюме для отклика не выбрано", result["reasons"])


if __name__ == "__main__":
    unittest.main()
