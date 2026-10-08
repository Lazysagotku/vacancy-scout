import unittest
from urllib.parse import parse_qs, urlparse

from scout.search_sources import built_in_sources


class SearchSourcesTests(unittest.TestCase):
    def test_sources_cover_python_support_and_csharp(self):
        sources = built_in_sources()
        self.assertEqual([s.track for s in sources].count("python"), 3)
        self.assertEqual([s.track for s in sources].count("support"), 2)
        self.assertEqual([s.track for s in sources].count("csharp"), 1)

    def test_csharp_search_is_limited_to_one_to_three_years(self):
        source = next(s for s in built_in_sources() if s.track == "csharp")
        parsed = urlparse(source.url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "hh.ru")
        self.assertEqual(parse_qs(parsed.query)["experience"], ["between1And3"])

    def test_python_and_support_have_no_experience_filter(self):
        for source in built_in_sources():
            if source.track != "csharp":
                self.assertNotIn("experience", parse_qs(urlparse(source.url).query))


if __name__ == "__main__":
    unittest.main()
