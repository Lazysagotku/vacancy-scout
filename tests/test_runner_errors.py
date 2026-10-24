"""Внутренняя ошибка разбора не должна выглядеть как проблема сети hh."""

import logging
import unittest
from unittest.mock import patch

from scout import runner
from scout.collector import CollectError


class AnalyzeBatchErrorTests(unittest.TestCase):
    def setUp(self):
        runner._state.last_error = None

    def test_internal_error_reports_type_without_network_advice(self):
        find = {"id": "1", "name": "AI инженер", "status": "new"}
        with patch("scout.runner.store.list_finds", return_value=[find]), \
             patch("scout.analysis.analyze_many", side_effect=ValueError("Неизвестный зарплатный трек: ai")):
            logging.disable(logging.CRITICAL)
            try:
                self.assertEqual(runner.analyze_batch(1), 0)
            finally:
                logging.disable(logging.NOTSET)
        message = runner._state.last_error or ""
        self.assertIn("ValueError", message)
        self.assertNotIn("VPN", message)
        self.assertNotIn("зарплатный трек", message)

    def test_collect_error_keeps_human_readable_network_hint(self):
        find = {"id": "1", "name": "Python-разработчик", "status": "new"}
        with patch("scout.runner.store.list_finds", return_value=[find]), \
             patch("scout.analysis.analyze_many",
                   side_effect=CollectError("net::ERR_CONNECTION_TIMED_OUT")):
            self.assertEqual(runner.analyze_batch(1), 0)
        self.assertIn("маршрут к hh", runner._state.last_error or "")


class SessionGuardTests(unittest.TestCase):
    """Цикл обязан звать человека, когда сессия hh слетела."""

    def test_live_session_calls_nobody(self):
        with patch("scout.runner.store.get_setting", return_value="1"),              patch("scout.collector.is_authorized", return_value=True),              patch("scout.hh_login.login") as login:
            runner._state.running = True
            runner._state.busy = False
            runner._ensure_session()
        login.assert_not_called()

    def test_dead_session_asks_the_human(self):
        with patch("scout.runner.store.get_setting", return_value="1"),              patch("scout.collector.is_authorized", return_value=False),              patch("scout.telegram_worker.channel",
                   return_value=(lambda *a, **k: None, lambda *a: None)),              patch("scout.hh_login.login", return_value={"ok": True}) as login:
            runner._state.running = True
            runner._state.busy = False
            runner._ensure_session()
        login.assert_called_once()

    def test_without_telegram_it_only_warns(self):
        with patch("scout.runner.store.get_setting", return_value="1"),              patch("scout.collector.is_authorized", return_value=False),              patch("scout.telegram_worker.channel", return_value=(None, None)),              patch("scout.hh_login.login") as login:
            runner._state.running = True
            runner._state.busy = False
            runner._ensure_session()
        login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
