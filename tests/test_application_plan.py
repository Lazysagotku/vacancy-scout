import unittest
from datetime import datetime, timedelta

from scout.application_plan import evaluate, preview


NOW = datetime(2026, 10, 1, 12, 0)
SALARY = "Ожидания по доходу - 170 000-200 000 рублей на руки."


def candidate(**changes):
    item = {
        "id": "123456", "name": "Python-разработчик", "status": "analyzed",
        "score": 85, "found_at": NOW.isoformat(), "analyzed_at": NOW.isoformat(),
        "description": "Разработка сервиса на Python", "experience": "От 3 до 6 лет",
        "opinion": "Рекомендую", "verdict": "сильный матч", "blockers": "",
        "resume": "Python-разработчик", "letter_kind": "claude",
        "letter": ("Добрый день!\n\nОткликаюсь на позицию Python-разработчик.\n\n"
                   + "Разрабатывал интеграции и автоматизировал процессы. " * 12
                   + "\n\n" + SALARY
                   + "\n\nМои проекты: https://lazysagotku.github.io\n\n"
                   + "Готов обсудить детали.\n\nС уважением,\nИван Архипов"),
    }
    item.update(changes)
    return item


class ApplicationPlanTests(unittest.TestCase):
    def test_ready_candidate_requires_current_form_without_questions(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        result = evaluate(candidate(), form=form, now=NOW)
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["track"], "python")

    def test_template_letter_and_missing_form_block_send(self):
        result = evaluate(candidate(letter_kind="template"), form=None, now=NOW)
        self.assertIn("Письмо не проверено моделью", result["reasons"])
        self.assertIn("Форма отклика не проверена", result["reasons"])

    def test_stale_vacancy_and_existing_application_block_send(self):
        old = (NOW - timedelta(days=7)).isoformat()
        result = evaluate(candidate(found_at=old, status="tracked"), now=NOW)
        self.assertIn("Вакансия старше 72 часов", result["reasons"])
        self.assertIn("Отклик уже отмечен", result["reasons"])

    def test_missing_salary_and_required_question_block_send(self):
        form = {"checked_at": NOW.isoformat(), "questions": [{"required": True}],
                "has_letter": True, "note": ""}
        result = evaluate(candidate(letter="Добрый день!"), form=form, now=NOW)
        self.assertIn("Нет согласованной зарплаты в письме", result["reasons"])
        self.assertIn("Форма требует ответа", result["reasons"])

    def test_preview_never_sends_and_deduplicates(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        result = preview([candidate(), candidate()], forms={"123456": form}, now=NOW)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["sent"])

    def test_preview_does_not_let_unanalyzed_result_displace_ready_one(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        newer = candidate(id="999999", status="new", score=100)
        result = preview([newer, candidate()], forms={"123456": form}, now=NOW, limit=1)
        self.assertEqual([item["id"] for item in result], ["123456"])


if __name__ == "__main__":
    unittest.main()
