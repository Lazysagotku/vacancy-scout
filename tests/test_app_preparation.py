import unittest
from unittest.mock import patch

from scout.app import prepare_letters


class AppPreparationTests(unittest.TestCase):
    def test_endpoint_only_starts_separate_preparation(self):
        with patch("scout.app.runner.prepare_letters", return_value={"started": True, "limit": 4}) as start:
            self.assertEqual(prepare_letters(limit=4), {"started": True, "limit": 4})
        start.assert_called_once_with(4)


if __name__ == "__main__":
    unittest.main()
