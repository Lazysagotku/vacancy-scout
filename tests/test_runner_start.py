import unittest
from unittest.mock import patch

from scout import runner


class RunnerStartTests(unittest.TestCase):
    def test_start_returns_without_reentering_state_lock(self):
        class GuardLock:
            entered = False

            def __enter__(self):
                if self.entered:
                    raise RuntimeError("state lock reentered")
                self.entered = True

            def __exit__(self, *_):
                self.entered = False

        runner._state.running = False
        with patch.object(runner, "_lock", GuardLock()), patch.object(runner.store, "set_setting"), patch.object(runner.store, "get_setting", return_value="180"), patch.object(runner.threading, "Thread") as thread:
            result = runner.start()
        self.assertTrue(result["running"])
        thread.return_value.start.assert_called_once()
        runner._state.running = False

    def test_repeated_start_does_not_reenter_state_lock(self):
        class GuardLock:
            entered = False

            def __enter__(self):
                if self.entered:
                    raise RuntimeError("state lock reentered")
                self.entered = True

            def __exit__(self, *_):
                self.entered = False

        runner._state.running = True
        try:
            with patch.object(runner, "_lock", GuardLock()), patch.object(runner.store, "get_setting", return_value="180"):
                result = runner.start()
            self.assertTrue(result["running"])
        finally:
            runner._state.running = False
