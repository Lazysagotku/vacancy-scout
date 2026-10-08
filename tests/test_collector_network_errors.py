import unittest

from scout.collector import CollectError, _validate_search_url, short_error


class CollectorNetworkErrorTests(unittest.TestCase):
    def test_search_url_must_be_https_hh_domain(self):
        self.assertEqual(_validate_search_url("https://hh.ru/search/vacancy?text=python"),
                         "https://hh.ru/search/vacancy?text=python")
        for candidate in ("http://hh.ru/search/vacancy", "https://hh.ru.evil.example/",
                          "https://127.0.0.1/", "https://hh.ru:444/"):
            with self.subTest(candidate=candidate), self.assertRaises(CollectError):
                _validate_search_url(candidate)

    def test_timeout_does_not_assume_vpn_is_off(self):
        message = short_error(RuntimeError("Timeout 45000ms exceeded"))
        self.assertIn("маршрут", message)
        self.assertNotIn("выключен VPN", message)

    def test_network_error_does_not_assume_vpn_is_off(self):
        message = short_error(RuntimeError("net::ERR_CONNECTION_RESET"))
        self.assertIn("маршрут", message)
        self.assertNotIn("выключен VPN", message)


if __name__ == "__main__":
    unittest.main()
