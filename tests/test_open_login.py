"""Окно входа в hh не должно закрываться, когда человек начинает вход."""

import unittest
from unittest.mock import patch

from scout import collector


class FakePage:
    """Страница, которая роняет evaluate на навигации, как настоящая."""

    def __init__(self, script):
        self.script = list(script)
        self.waits = 0

    def goto(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, ms):
        self.waits += 1

    def evaluate(self, code):
        if not self.script:
            return True
        step = self.script.pop(0)
        if step == "navigating":
            raise RuntimeError("Execution context was destroyed")
        return step


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    def new_page(self):
        return self.pages[0]

    def close(self):
        self.closed = True


class OpenLoginTests(unittest.TestCase):
    def run_with(self, script):
        page = FakePage(script)
        context = FakeContext(page)

        class Chromium:
            def launch_persistent_context(self, **kwargs):
                return context

        class PW:
            chromium = Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("playwright.sync_api.sync_playwright", return_value=PW()):
            collector.open_login(wait_minutes=1)
        return page, context

    def test_navigation_error_does_not_close_the_window(self):
        # Нажатие «Войти» уничтожает контекст страницы. Раньше эта ошибка
        # ловилась общим except и закрывала браузер человеку в лицо.
        page, context = self.run_with(["navigating", "navigating", True])
        self.assertGreaterEqual(page.waits, 3)
        self.assertTrue(context.closed)

    def test_profile_menu_ends_the_wait(self):
        page, _ = self.run_with([True])
        self.assertEqual(page.waits, 1)

    def test_busy_profile_says_so(self):
        class Chromium:
            def launch_persistent_context(self, **kwargs):
                raise RuntimeError("ProcessSingleton: profile is in use")

        class PW:
            chromium = Chromium()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("playwright.sync_api.sync_playwright", return_value=PW()):
            with self.assertRaises(collector.CollectError) as caught:
                collector.open_login(wait_minutes=1)
        self.assertIn("Останови скаут", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
