"""Перепроверка живости вакансий, подбор резюме и предохранители отклика."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from scout import apply, hh_login, profile, recheck

NOW = datetime(2026, 10, 13, 12, 0)
SALARY = "Ожидания по доходу - 200 000 рублей на руки."


def letter_for(name="Инженер сопровождения"):
    return ("Добрый день!\n\nОткликаюсь на позицию " + name + ".\n\n"
            + "Разбирал инциденты и автоматизировал рутину. " * 12
            + "\n\n" + SALARY
            + "\n\nМои проекты: https://lazysagotku.github.io\n\n"
            + "Готов обсудить детали.\n\nС уважением,\nИван Архипов")


def find(**changes):
    item = {
        "id": "555", "name": "Инженер сопровождения", "status": "analyzed",
        "score": 85, "found_at": NOW.isoformat(), "analyzed_at": NOW.isoformat(),
        "description": "Поддержка систем, Linux, SQL", "experience": "От 3 до 6 лет",
        "opinion": "Рекомендую", "verdict": "сильный матч", "blockers": "",
        "resume": "Специалист сопровождения систем", "letter_kind": "claude",
        "letter": letter_for(),
    }
    item.update(changes)
    return item


class ResumeChoiceTests(unittest.TestCase):
    def test_english_support_title_does_not_get_developer_resume(self):
        # «Senior Support Engineer» ловился на слово engineer из списка ролей
        # разработчика: в исключениях были только русские слова.
        titles = [
            "Senior Support Engineer [АПК СОРМ, МТС Веб Сервисы]",
            "Technical Support Engineer",
            "Site Reliability Engineer",
            "System Administrator",
            "L2 Support Engineer",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertEqual(profile.resume_for("support", title, ""),
                                 "Специалист сопровождения систем")

    def test_developer_titles_keep_developer_resume(self):
        self.assertEqual(profile.resume_for("dev", "Python-разработчик", ""),
                         "Python-разработчик")
        self.assertEqual(profile.resume_for("dev", "C# Developer", ""),
                         "C#/.NET-разработчик")


class RecheckTests(unittest.TestCase):
    def test_fresh_card_is_not_rechecked(self):
        fresh = find(checked_at=NOW.isoformat())
        self.assertEqual(recheck.stale_first([fresh], now=NOW), [])

    def test_oldest_check_goes_first(self):
        old = find(id="1", checked_at=(NOW - timedelta(days=9)).isoformat())
        newer = find(id="2", checked_at=(NOW - timedelta(days=2)).isoformat())
        order = [item["id"] for item in recheck.stale_first([newer, old], now=NOW)]
        self.assertEqual(order, ["1", "2"])

    def test_archived_vacancy_is_dropped(self):
        fields = recheck.verdict_for(find(), {"archived_at": "5 октября"})
        self.assertEqual(fields["status"], "dropped")
        self.assertIn("архиве", fields["drop_reason"])

    def test_single_network_miss_keeps_the_card(self):
        fields = recheck.verdict_for(find(), None)
        self.assertNotIn("status", fields)
        self.assertEqual(fields["check_misses"], 1)

    def test_third_miss_drops_the_card(self):
        fields = recheck.verdict_for(find(check_misses=2), None)
        self.assertEqual(fields["status"], "dropped")
        self.assertEqual(fields["check_misses"], 3)


class ApplyGuardTests(unittest.TestCase):
    def test_unknown_question_blocks_the_application(self):
        form = {"checked_at": NOW.isoformat(), "has_letter": True, "note": "",
                "questions": [{"question": "Почему вы хотите к нам?", "kind": "text"}]}
        with patch("scout.apply.store.application", return_value=None):
            verdict = apply.decide(find(), form, now=NOW)
        self.assertFalse(verdict["ok"])
        # Причина называет сам вопрос, чтобы человек сразу видел, что ответить.
        self.assertIn("Почему вы хотите к нам?", verdict["reason"])

    def test_known_salary_question_is_answered_from_policy(self):
        form = {"checked_at": NOW.isoformat(), "has_letter": True, "note": "",
                "questions": [{"question": "Ваши зарплатные ожидания?", "kind": "text"}]}
        with patch("scout.apply.store.application", return_value=None):
            verdict = apply.decide(find(), form, now=NOW)
        self.assertTrue(verdict["ok"], verdict["reason"])
        self.assertIn("200 000", verdict["answers"][0]["suggestion"])

    def test_second_application_is_refused(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        with patch("scout.apply.store.application", return_value={"outcome": "sent"}):
            verdict = apply.decide(find(), form, now=NOW)
        self.assertFalse(verdict["ok"])
        self.assertIn("уже отправлен", verdict["reason"])

    def test_dry_run_never_opens_a_browser(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        with patch("scout.apply.store.get_find", return_value=find()), \
             patch("scout.response_form.known", return_value=form), \
             patch("scout.apply.store.application", return_value=None), \
             patch("scout.apply.store.log_application") as journal, \
             patch("scout.apply.live_allowed", return_value=True):
            result = apply.apply_one("555", dry_run=True)
        self.assertFalse(result["sent"])
        self.assertTrue(result["would_send"])
        self.assertEqual(journal.call_args.args[2], "planned")

    def test_live_requires_setting_even_with_flag(self):
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        with patch("scout.apply.store.get_find", return_value=find()), \
             patch("scout.response_form.known", return_value=form), \
             patch("scout.apply.store.application", return_value=None), \
             patch("scout.apply.store.log_application"), \
             patch("scout.apply.live_allowed", return_value=False):
            result = apply.apply_one("555", dry_run=False)
        self.assertFalse(result["sent"])
        self.assertEqual(result["mode"], "dry-run")


class LoginRelayTests(unittest.TestCase):
    def setUp(self):
        hh_login.clear()

    def tearDown(self):
        hh_login.clear()

    def test_digits_are_a_code_and_text_is_a_captcha(self):
        self.assertEqual(hh_login.classify("4821"), "code")
        self.assertEqual(hh_login.classify(" 48 21 "), "code")
        self.assertEqual(hh_login.classify("собака"), "text")
        self.assertEqual(hh_login.classify("ab12"), "text")

    def test_answer_without_request_is_refused(self):
        self.assertFalse(hh_login.deliver("1234")["taken"])

    def test_captcha_answer_is_taken_and_code_is_awaited(self):
        hh_login.ask("captcha")
        outcome = hh_login.deliver("собака")
        self.assertTrue(outcome["taken"])
        self.assertIn("жду код", outcome["note"])

    def test_code_stage_refuses_plain_text(self):
        hh_login.ask("code")
        self.assertFalse(hh_login.deliver("не помню")["taken"])
        self.assertTrue(hh_login.deliver("9137")["taken"])


if __name__ == "__main__":
    unittest.main()
