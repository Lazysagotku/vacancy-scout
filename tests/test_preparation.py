import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from scout import analysis, runner


NOW = datetime(2026, 10, 1, 12, 0)


class PreparationTests(unittest.TestCase):
    def test_browser_analysis_does_not_generate_ai_letters(self):
        find = {"id": "123456", "name": "Python-разработчик", "status": "new"}
        with patch("scout.runner.store.list_finds", side_effect=[[find], []]), \
             patch("scout.analysis.analyze_many", return_value=1) as analyze, \
             patch("scout.analysis.peek_forms", return_value=0) as peek_forms, \
             patch("scout.runner.store.get_setting", return_value="30"):
            self.assertEqual(runner.analyze_batch(1), 1)
        self.assertEqual(analyze.call_args.kwargs["letter_budget"], 0)
        peek_forms.assert_not_called()

    def test_catch_up_ignores_old_and_archived_findings(self):
        fresh = {"id": "1", "status": "analyzed", "score": 90,
                 "found_at": NOW.isoformat(), "analyzed_at": NOW.isoformat(),
                 "description": "Python", "letter_kind": "template"}
        old = {**fresh, "id": "2", "found_at": (NOW - timedelta(days=8)).isoformat()}
        archived = {**fresh, "id": "3", "archived_at": NOW.isoformat()}
        with patch("scout.analysis.store.list_finds", return_value=[old, archived, fresh]), \
             patch("scout.analysis.write_letter", return_value=fresh) as write:
            self.assertEqual(analysis.catch_up_letters(10, now=NOW), 1)
        write.assert_called_once_with(fresh)

    def test_catch_up_rewrites_recent_ai_letter_that_fails_quality_gate(self):
        find = {"id": "1", "status": "analyzed", "score": 90,
                "found_at": NOW.isoformat(), "analyzed_at": NOW.isoformat(),
                "description": "Python", "letter_kind": "claude", "letter": "short"}
        with patch("scout.analysis.store.list_finds", return_value=[find]), \
             patch("scout.analysis.write_letter", return_value=find) as write:
            self.assertEqual(analysis.catch_up_letters(1, now=NOW), 1)
        write.assert_called_once_with(find)

    def test_manual_analysis_keeps_ai_for_separate_preparation(self):
        find = {"id": "123456", "name": "Python-разработчик", "description": "Python"}
        with patch("scout.analysis.store.get_find", return_value=find), \
             patch("scout.analysis._save", return_value=find) as save:
            analysis.analyze_find("123456", deep=False)
        self.assertFalse(save.call_args.kwargs["write_letter"])

    def test_preparation_starts_once_and_never_overlaps_browser_scan(self):
        runner._state.busy = False
        runner._state.preparing = False
        with patch("scout.writer.available", return_value=True), \
             patch("scout.runner.threading.Thread") as thread:
            first = runner.prepare_letters(3)
            second = runner.prepare_letters(3)
            self.assertTrue(first["started"])
            self.assertFalse(second["started"])
            thread.return_value.start.assert_called_once()
        runner._state.preparing = False
        runner._state.busy = True
        with patch("scout.writer.available", return_value=True):
            self.assertFalse(runner.prepare_letters(3)["started"])
        runner._state.busy = False

    def test_preparation_reports_ai_unavailable_without_starting(self):
        runner._state.preparing = False
        runner._state.busy = False
        with patch("scout.writer.available", return_value=False):
            result = runner.prepare_letters(3)
        self.assertFalse(result["started"])
        self.assertIn("Claude", result["reason"])

    def test_availability_exception_does_not_leave_preparing_stuck(self):
        runner._state.preparing = False
        runner._state.busy = False
        with patch("scout.writer.available", side_effect=OSError("offline")):
            result = runner.prepare_letters(3)
        self.assertFalse(result["started"])
        self.assertFalse(runner._state.preparing)


if __name__ == "__main__":
    unittest.main()
