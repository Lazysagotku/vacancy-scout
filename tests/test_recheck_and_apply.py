"""Перепроверка живости вакансий, подбор резюме и предохранители отклика."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from scout import apply, hh_login, profile, recheck

# Точка отсчёта берётся от текущего момента намеренно. Зашитая дата делала
# тесты свежести одноразовыми: ворота отправки пускают карточку моложе 72
# часов, и набор, зелёный 14 октября, падал 23-го без единой правки кода.
NOW = datetime.now()
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

    def test_short_list_card_is_checked_before_the_tail(self):
        # Видимая карточка проверялась два дня назад, невидимая - девять.
        # По старому правилу «самая протухшая вперёд» первой шла невидимая,
        # и короткий список оставался непроверенным.
        visible = find(id="vis", score=80, found_at=(NOW - timedelta(days=2)).isoformat(),
                       checked_at=(NOW - timedelta(days=2)).isoformat())
        tail = find(id="tail", score=80, found_at=(NOW - timedelta(days=40)).isoformat(),
                    checked_at=(NOW - timedelta(days=9)).isoformat())
        order = [item["id"] for item in recheck.stale_first([tail, visible], now=NOW)]
        self.assertEqual(order, ["vis", "tail"])

    def test_weak_card_is_not_treated_as_visible(self):
        # Балл ниже порога короткой выдачи - карточку человек не видит.
        weak = find(score=10, found_at=NOW.isoformat())
        self.assertFalse(recheck.on_short_list(weak, NOW))
        strong = find(score=80, found_at=NOW.isoformat())
        self.assertTrue(recheck.on_short_list(strong, NOW))

    def test_archived_vacancy_is_dropped(self):
        fields = recheck.verdict_for(find(), {"archived_at": "5 октября"})
        self.assertEqual(fields["status"], "dropped")
        self.assertIn("архиве", fields["drop_reason"])

    def test_unreachable_page_is_a_miss_not_a_live_vacancy(self):
        # Сборщик на недоступной вакансии отдаёт не None, а пустой разбор.
        # Проверка `details is None` его не ловила, и такая карточка
        # записывалась живой с обнулённым счётчиком промахов.
        empty = {"archived_at": "", "description": "", "title": ""}
        self.assertTrue(recheck.page_missed(empty))
        fields = recheck.verdict_for(find(), empty)
        self.assertEqual(fields["check_misses"], 1)
        self.assertNotIn("status", fields)

    def test_page_with_description_is_alive(self):
        alive = {"archived_at": "", "description": "Поддержка систем, Linux"}
        self.assertFalse(recheck.page_missed(alive))
        self.assertEqual(recheck.verdict_for(find(), alive)["check_misses"], 0)

    def test_every_page_is_saved_before_the_batch_ends(self):
        # Проверка VPN на последней карточке раньше выбрасывала труд по всем
        # предыдущим: запись шла после всей пачки.
        saved = []

        def fetch(ids, on_progress=None, on_item=None):
            on_item(ids[0], {"archived_at": "5 октября", "description": ""})
            raise RuntimeError("hh просит пройти проверку VPN")

        cards = [find(id="1"), find(id="2")]
        with patch("scout.recheck.stale_first", return_value=cards),              patch("scout.recheck.store.list_finds", return_value=cards),              patch("scout.recheck.store.update_find",
                   side_effect=lambda i, **f: saved.append((i, f["status"]))),              patch("scout.collector.fetch_details", side_effect=fetch):
            with self.assertRaises(RuntimeError):
                recheck.recheck(limit=2)
        self.assertEqual(saved, [("1", "dropped")])

    def test_single_network_miss_keeps_the_card(self):
        fields = recheck.verdict_for(find(), None)
        self.assertNotIn("status", fields)
        self.assertEqual(fields["check_misses"], 1)

    def test_third_miss_drops_the_card(self):
        fields = recheck.verdict_for(find(check_misses=2), None)
        self.assertEqual(fields["status"], "dropped")
        self.assertEqual(fields["check_misses"], 3)


class CloseStaleTests(unittest.TestCase):
    def old(self, **changes):
        return find(found_at=(NOW - timedelta(days=40)).isoformat(), **changes)

    def test_card_past_the_window_is_closed(self):
        tail = recheck.stale_tail([self.old()], now=NOW)
        self.assertEqual(len(tail), 1)

    def test_fresh_card_is_left_alone(self):
        self.assertEqual(recheck.stale_tail([find(found_at=NOW.isoformat())], now=NOW), [])

    def test_pinned_card_is_never_closed_by_age(self):
        # Закрепил человек руками - возраст такую карточку не трогает.
        self.assertEqual(recheck.stale_tail([self.old(pinned_at=NOW.isoformat())], now=NOW), [])

    def test_recently_confirmed_alive_card_is_kept(self):
        seen = (NOW - timedelta(days=2)).isoformat()
        self.assertEqual(recheck.stale_tail([self.old(checked_at=seen)], now=NOW), [])

    def test_long_ago_checked_card_is_closed_again(self):
        seen = (NOW - timedelta(days=30)).isoformat()
        self.assertEqual(len(recheck.stale_tail([self.old(checked_at=seen)], now=NOW)), 1)

    def test_dry_run_writes_nothing(self):
        with patch("scout.recheck.store.list_finds", return_value=[self.old()]),              patch("scout.recheck.store.update_find") as write:
            result = recheck.close_stale(now=NOW)
        self.assertEqual(result["closed"], 1)
        write.assert_not_called()

    def test_reason_names_the_age(self):
        with patch("scout.recheck.store.list_finds", return_value=[self.old()]),              patch("scout.recheck.store.update_find") as write:
            recheck.close_stale(dry_run=False, now=NOW)
        reason = write.call_args.kwargs["drop_reason"]
        self.assertIn("устарела", reason)
        self.assertIn("40 дн.", reason)


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


class AutoRoundTests(unittest.TestCase):
    """Автоматический круг откликов: порог, суточный лимит, слетевшая сессия."""

    def test_score_just_over_threshold_passes(self):
        from scout.application_plan import evaluate
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        # 61 проходит, 60 нет: решение Ивана «больше 60».
        self.assertTrue(evaluate(find(score=61), form, now=NOW)["eligible"])
        blocked = evaluate(find(score=60), form, now=NOW)
        self.assertFalse(blocked["eligible"])
        self.assertIn("ниже порога", " ".join(blocked["reasons"]))

    def test_strong_verdict_is_no_longer_required(self):
        # Раньше ворота требовали вердикт «сильный матч», и карточка с баллом
        # 65 не проходила никогда, даже с положительным мнением модели.
        from scout.application_plan import evaluate
        form = {"checked_at": NOW.isoformat(), "questions": [], "has_letter": True,
                "has_questions_flag": False, "note": ""}
        card = find(score=65, verdict="стоит посмотреть")
        self.assertTrue(evaluate(card, form, now=NOW)["eligible"])

    def test_daily_limit_stops_the_round(self):
        # Лимит теперь настройка, а не константа: проверяем против него же.
        with patch("scout.apply.live_allowed", return_value=True),              patch("scout.apply.daily_limit", return_value=25),              patch("scout.apply.store.sent_today", return_value=25),              patch("scout.apply.apply_one") as send:
            result = apply.auto_round(dry_run=False)
        self.assertIn("лимит", result["skipped"])
        send.assert_not_called()

    def test_lost_session_calls_the_human_once_and_continues(self):
        from scout.collector import CollectError
        calls = {"n": 0}

        def flaky(vacancy_id, dry_run=True):
            calls["n"] += 1
            if calls["n"] == 1:
                raise CollectError("Нет сессии hh - нужен вход")
            return {"sent": True, "mode": "live"}

        with patch("scout.apply.live_allowed", return_value=True),              patch("scout.apply.store.sent_today", return_value=0),              patch("scout.apply.ready_now", return_value=[{"id": "1"}]),              patch("scout.apply.apply_one", side_effect=flaky),              patch("scout.hh_login.login", return_value={"ok": True}) as login,              patch("time.sleep"):
            result = apply.auto_round(lambda *a, **k: None, lambda *a: None, dry_run=False)
        login.assert_called_once()
        self.assertEqual(result["sent"], 1)

    def test_failed_login_stops_the_round(self):
        from scout.collector import CollectError
        with patch("scout.apply.live_allowed", return_value=True),              patch("scout.apply.store.sent_today", return_value=0),              patch("scout.apply.ready_now", return_value=[{"id": "1"}, {"id": "2"}]),              patch("scout.apply.apply_one",
                   side_effect=CollectError("Нет сессии hh - нужен вход")),              patch("scout.hh_login.login", return_value={"ok": False}):
            result = apply.auto_round(lambda *a, **k: None, lambda *a: None, dry_run=False)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["results"][-1]["reason"], "вход не удался")


class LetterIsMandatoryTests(unittest.TestCase):
    """Отклик без сопроводительного не уходит ни при каких обстоятельствах."""

    class FakePage:
        def __init__(self, has_field):
            self.has_field = has_field
            self.filled = None

        def query_selector(self, selector):
            if not self.has_field:
                return None
            page = self

            class Field:
                def fill(self, text):
                    page.filled = text
            return Field()

        def evaluate(self, *args):
            return None

        def wait_for_timeout(self, ms):
            return None

    def test_missing_letter_field_stops_the_application(self):
        from scout.collector import CollectError
        page = self.FakePage(has_field=False)
        with self.assertRaises(CollectError) as caught:
            apply._fill_form(page, letter_for(), "Специалист сопровождения систем", [])
        self.assertIn("без письма", str(caught.exception))

    def test_letter_is_placed_when_the_field_exists(self):
        page = self.FakePage(has_field=True)
        apply._fill_form(page, letter_for(), "Специалист сопровождения систем", [])
        self.assertIn("Откликаюсь на позицию", page.filled)


class LoginAdvanceTests(unittest.TestCase):
    """Шаги без полей сценарий обязан проходить сам."""

    class Page:
        def __init__(self, controls):
            self.controls = controls
            self.clicked = []

        def evaluate(self, script, args):
            advance, skip = args
            import re
            ok, no = re.compile(advance, re.I), re.compile(skip, re.I)
            for text in self.controls:
                if ok.search(text) and not no.search(text):
                    self.clicked.append(text)
                    self.controls.remove(text)
                    return text
            return ""

    def press(self, controls):
        page = self.Page(list(controls))
        pressed = hh_login._advance(page, hh_login.ADVANCE, hh_login.SKIP)
        return pressed, page

    def test_login_button_is_pressed(self):
        pressed, _ = self.press(["Войти", "Зарегистрироваться"])
        self.assertEqual(pressed, "Войти")

    def test_registration_is_never_pressed(self):
        # У Ивана аккаунт есть; создавать второй сценарий не должен.
        pressed, _ = self.press(["Зарегистрироваться"])
        self.assertEqual(pressed, "")

    def test_employer_role_is_skipped(self):
        pressed, _ = self.press(["Я ищу сотрудником"])
        self.assertEqual(pressed, "")

    def test_nothing_to_press_returns_empty(self):
        pressed, _ = self.press(["Забыли пароль?"])
        self.assertEqual(pressed, "")


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
