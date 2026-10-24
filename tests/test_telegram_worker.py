import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scout import telegram_worker
from scout.telegram_worker import allowed_command, handle_update, start_if_configured


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def update(chat_id=42, sender_id=42, chat_type="private", text="/status", age=0):
    return {"update_id": 5, "message": {
        "date": int(NOW.timestamp()) - age, "text": text,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": sender_id, "is_bot": False},
    }}


class TelegramWorkerTests(unittest.TestCase):
    def test_only_fresh_commands_from_bound_private_chat_are_accepted(self):
        self.assertEqual(allowed_command(update(), 42, now=NOW), "/status")
        self.assertIsNone(allowed_command(update(chat_id=99), 42, now=NOW))
        self.assertIsNone(allowed_command(update(sender_id=99), 42, now=NOW))
        self.assertIsNone(allowed_command(update(chat_type="group"), 42, now=NOW))
        # Пять минут - своя команда, окно шире её намеренно: прежние 120 секунд
        # теряли команду человека при каждом перезапуске сервиса.
        self.assertEqual(allowed_command(update(age=300), 42, now=NOW), "/status")
        self.assertIsNone(
            allowed_command(update(age=telegram_worker.FRESH_SECONDS + 60), 42, now=NOW))
        self.assertIsNone(allowed_command(update(text="123456"), 42, now=NOW))

    def test_status_is_sent_only_to_bound_chat(self):
        sent = []
        result = handle_update(update(), 42, send=lambda chat, text: sent.append((chat, text)),
                               status=lambda: {"running": True, "last_error": None},
                               preview=lambda: [], now=NOW)
        self.assertTrue(result)
        self.assertEqual(sent[0][0], 42)
        self.assertIn("работает", sent[0][1])

    def test_status_reports_safe_preparation_failure(self):
        sent = []
        handle_update(update(), 42, send=lambda chat, text: sent.append(text),
                      status=lambda: {"running": False, "preparing": False,
                                      "last_prepared": 0, "last_prepare_error": "OSError"},
                      preview=lambda: [], now=NOW)
        self.assertIn("OSError", sent[0])

    def test_unbound_sender_cannot_trigger_status_or_preview(self):
        sent = []
        result = handle_update(update(chat_id=99), 42,
                               send=lambda chat, text: sent.append((chat, text)),
                               status=lambda: {"running": True}, preview=lambda: [], now=NOW)
        self.assertFalse(result)
        self.assertEqual(sent, [])

    def test_token_without_bound_chat_does_not_start_polling(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:token", "TELEGRAM_CHAT_ID": ""}):
            self.assertFalse(start_if_configured())

    def test_bound_chat_can_start_preparation(self):
        sent = []
        prepared = []
        result = handle_update(update(text="/prepare"), 42,
                               send=lambda chat, text: sent.append(text),
                               status=lambda: {}, preview=lambda: [], now=NOW,
                               prepare=lambda: prepared.append(True) or {"started": True})
        self.assertTrue(result)
        self.assertEqual(prepared, [True])
        self.assertIn("запущена", sent[0])

    def test_unbound_chat_cannot_start_preparation(self):
        prepared = []
        result = handle_update(update(chat_id=99, text="/prepare"), 42,
                               send=lambda chat, text: None,
                               status=lambda: {}, preview=lambda: [], now=NOW,
                               prepare=lambda: prepared.append(True))
        self.assertFalse(result)
        self.assertEqual(prepared, [])

    def test_questions_command_shows_suggestions_without_submission(self):
        sent = []
        answer = {"number": 1, "question": "Зарплатные ожидания?",
                  "status": "suggested", "suggestion": "170 000-200 000 рублей на руки"}
        result = handle_update(update(text="/questions"), 42,
                               send=lambda chat, text: sent.append(text),
                               status=lambda: {}, preview=lambda: [], now=NOW,
                               questions=lambda: [{"id": "123456", "name": "Python-разработчик",
                                                   "answers": [answer]}])
        self.assertTrue(result)
        self.assertIn("Зарплатные ожидания?", sent[0])
        self.assertIn("черновик", sent[0].lower())


class FreshnessWindowTests(unittest.TestCase):
    """Окно свежести: своё пропускаем, просроченное нет."""

    def test_command_within_the_window_passes(self):
        # 23.10 на прежних 120 секундах терялись /login и /status: сервис
        # перезапускался, и команда человека молча пропадала.
        got = telegram_worker.allowed_command(update(age=300), 42, now=NOW)
        self.assertEqual(got, "/status")

    def test_command_past_the_window_is_refused(self):
        got = telegram_worker.allowed_command(
            update(age=telegram_worker.FRESH_SECONDS + 60), 42, now=NOW)
        self.assertIsNone(got)


class RefusalReasonTests(unittest.TestCase):
    """Причина отклонения обязана называть конкретную проверку."""

    def test_foreign_chat(self):
        reason = telegram_worker.refusal_reason(update(chat_id=99), 42, now=NOW)
        self.assertIn("чужой чат", reason)

    def test_group_chat(self):
        reason = telegram_worker.refusal_reason(update(chat_type="group"), 42, now=NOW)
        self.assertIn("не личный", reason)

    def test_clock_skew_is_tolerated(self):
        # Отметку ставит сервер Telegram, сравнивают с часами этой машины.
        # 24.10 команды отбрасывались при расхождении в одну секунду.
        self.assertEqual(allowed_command(update(age=-1), 42, now=NOW), "/status")
        self.assertEqual(
            allowed_command(update(age=-telegram_worker.CLOCK_SKEW_SECONDS + 5), 42, now=NOW),
            "/status")

    def test_far_future_stamp_is_still_refused(self):
        self.assertIsNone(
            allowed_command(update(age=-telegram_worker.CLOCK_SKEW_SECONDS - 60), 42, now=NOW))

    def test_stale_message_names_the_age(self):
        reason = telegram_worker.refusal_reason(
            update(age=telegram_worker.FRESH_SECONDS + 5), 42, now=NOW)
        self.assertIn("старше окна", reason)

    def test_edited_message_has_no_message_field(self):
        # Правка сообщения приходит как edited_message, и фильтр её не видит.
        reason = telegram_worker.refusal_reason({"update_id": 7, "edited_message": {}}, 42, now=NOW)
        self.assertIn("нет message", reason)

    def test_unknown_text_says_so(self):
        reason = telegram_worker.refusal_reason(update(text="привет"), 42, now=NOW)
        self.assertIn("не команда", reason)


if __name__ == "__main__":
    unittest.main()
