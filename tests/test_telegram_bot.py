import io
import json
import unittest
from datetime import datetime, timezone
from urllib.error import URLError

from scout.telegram_bot import TelegramClient, TelegramError, recent_private_starts


class FakeResponse:
    def __init__(self, payload):
        self.data = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self.data

    def __exit__(self, *_):
        return False


class TelegramBotTests(unittest.TestCase):
    def test_get_me_returns_only_bot_identity(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"ok": True, "result": {"id": 23, "username": "private_scout_bot", "first_name": "Scout"}})

        bot = TelegramClient("123:test-token", opener=opener)
        self.assertEqual(bot.get_me(), {"id": 23, "username": "private_scout_bot"})
        self.assertEqual(requests[0].get_method(), "POST")

    def test_get_updates_passes_offset_and_limits_update_types(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"ok": True, "result": []})

        bot = TelegramClient("123:test-token", opener=opener)
        self.assertEqual(bot.get_updates(offset=8, timeout=10), [])
        payload = json.loads(requests[0].data)
        self.assertEqual(payload["offset"], 8)
        self.assertEqual(payload["allowed_updates"], ["message"])

    def test_send_message_protects_content(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"ok": True, "result": {"message_id": 5}})

        TelegramClient("123:test-token", opener=opener).send_message(42, "Проверка")
        payload = json.loads(requests[0].data)
        self.assertEqual(payload["chat_id"], 42)
        self.assertTrue(payload["protect_content"])

    def test_errors_never_echo_token(self):
        def opener(request, timeout):
            raise URLError("network unavailable")

        bot = TelegramClient("123:very-secret", opener=opener)
        with self.assertRaises(TelegramError) as caught:
            bot.get_me()
        self.assertNotIn("very-secret", str(caught.exception))
        self.assertNotIn("very-secret", repr(caught.exception))

    def test_send_photo_uses_png_upload_and_protects_content(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"ok": True, "result": {"message_id": 5}})

        bot = TelegramClient("123:test-token", opener=opener)
        bot.send_photo(42, b"\x89PNG\r\n\x1a\nimage", caption="Экран входа hh")
        body = requests[0].data
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b"42", body)
        self.assertIn(b'name="protect_content"', body)
        self.assertIn(b"true", body)
        self.assertIn(b"\x89PNG\r\n\x1a\nimage", body)

    def test_send_photo_rejects_non_png(self):
        bot = TelegramClient("123:test-token", opener=lambda *_args, **_kwargs: None)
        with self.assertRaises(ValueError):
            bot.send_photo(42, b"not-an-image")

    def test_start_candidates_require_recent_private_human_message(self):
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        stamp = int(now.timestamp())
        updates = [
            {"update_id": 1, "message": {"date": stamp, "text": "/start", "chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}},
            {"update_id": 2, "message": {"date": stamp, "text": "/start", "chat": {"id": -100, "type": "group"}, "from": {"id": 88, "is_bot": False}}},
            {"update_id": 3, "message": {"date": stamp - 1000, "text": "/start", "chat": {"id": 43, "type": "private"}, "from": {"id": 43, "is_bot": False}}},
            {"update_id": 4, "message": {"date": stamp, "text": "/start", "chat": {"id": 44, "type": "private"}, "from": {"id": 44, "is_bot": True}}},
        ]
        self.assertEqual(recent_private_starts(updates, now=now), [42])


if __name__ == "__main__":
    unittest.main()
