import unittest

from scout.question_plan import pending_questions, plan_answers


class QuestionPlanTests(unittest.TestCase):
    def test_salary_suggestion_uses_track_policy(self):
        form = {"questions": [{"question": "Какие у вас зарплатные ожидания?", "kind": "text"}]}
        result = plan_answers({"name": "Python-разработчик"}, form)
        self.assertIn("170 000-200 000", result[0]["suggestion"])
        self.assertEqual(result[0]["status"], "suggested")

    def test_unknown_experience_and_choices_need_user(self):
        form = {"questions": [
            {"question": "Сколько лет вы работали с Kubernetes?", "kind": "text"},
            {"question": "Готовы к переезду?", "kind": "radio", "options": ["Да", "Нет"]},
        ]}
        result = plan_answers({"name": "Python-разработчик"}, form)
        self.assertTrue(all(item["status"] == "needs_user" for item in result))
        self.assertTrue(all(item["suggestion"] == "" for item in result))

    def test_past_salary_is_not_answered_as_current_expectation(self):
        form = {"questions": [{"question": "Какая зарплата была на предыдущем месте?",
                               "kind": "text"}]}
        result = plan_answers({"name": "Python-разработчик"}, form)
        self.assertEqual(result[0]["status"], "needs_user")

    def test_unrecognized_track_cannot_get_salary_suggestion(self):
        form = {"questions": [{"question": "Зарплатные ожидания?", "kind": "text"}]}
        result = plan_answers({"name": "Неизвестная роль"}, form)
        self.assertEqual(result[0]["status"], "needs_user")

    def test_pending_questions_are_recent_and_read_only(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 10, 1, 12, 0)
        fresh = {"id": "123456", "name": "Python-разработчик", "status": "analyzed",
                 "score": 90, "found_at": now.isoformat()}
        old = {**fresh, "id": "987654", "found_at": (now - timedelta(days=8)).isoformat()}
        forms = {"123456": {"checked_at": now.isoformat(),
                            "questions": [{"question": "Зарплатные ожидания?", "kind": "text"}]},
                 "987654": {"checked_at": now.isoformat(),
                            "questions": [{"question": "Опыт?", "kind": "text"}]}}
        result = pending_questions([old, fresh], forms, now=now)
        self.assertEqual([item["id"] for item in result], ["123456"])
        self.assertEqual(result[0]["answers"][0]["status"], "suggested")

    def test_corrupt_form_data_does_not_break_bot_question_list(self):
        from datetime import datetime
        now = datetime(2026, 10, 1, 12, 0)
        find = {"id": "123456", "name": "Python-разработчик", "status": "analyzed",
                "score": 90, "found_at": now.isoformat()}
        self.assertEqual(pending_questions([find], {"123456": "bad data"}, now=now), [])
        self.assertEqual(pending_questions([find], [], now=now), [])


class HhOwnWidgetTests(unittest.TestCase):
    """Собственный виджет hh не должен блокировать отклик."""

    def test_ask_employer_widget_is_not_a_question(self):
        # «Задайте вопрос работодателю» - предложение hh кандидату, а не
        # требование работодателя. 23.10 оно блокировало обоих кандидатов,
        # которых разведчик рекомендовал.
        form = {"questions": [
            {"question": "Задайте вопрос работодателю", "kind": "checkbox",
             "options": ["Где располагается место работы?", "Какой график работы?"]},
        ]}
        self.assertEqual(plan_answers({"resume": "Специалист сопровождения систем"}, form), [])

    def test_real_question_still_counts(self):
        form = {"questions": [{"question": "Почему вы хотите к нам?", "kind": "text"}]}
        planned = plan_answers({"resume": "Специалист сопровождения систем"}, form)
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0]["status"], "needs_user")


if __name__ == "__main__":
    unittest.main()
