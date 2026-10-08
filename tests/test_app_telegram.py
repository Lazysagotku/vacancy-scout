import unittest
from unittest.mock import patch

from scout.app import on_startup


class AppTelegramTests(unittest.TestCase):
    def test_startup_starts_bound_telegram_worker(self):
        with patch("scout.app.store.init"), patch("scout.app.runner.resume_if_enabled"), \
             patch("scout.telegram_worker.start_if_configured") as start:
            on_startup()
        start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
