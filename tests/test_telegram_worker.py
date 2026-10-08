import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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
        self.assertIsNone(allowed_command(update(age=300), 42, now=NOW))
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


if __name__ == "__main__":
    unittest.main()
