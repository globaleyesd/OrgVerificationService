import unittest

from app.retrieval import SEARCH_SQL, WITHHELD_SQL, search_params

LEVELS = ["employee", "super"]


class RetrievalTests(unittest.TestCase):
    def test_low_clearance_params_exclude_high_level(self):
        p = search_params("employee", LEVELS, [0.1, 0.2], 20, 0.25)
        self.assertEqual(p["levels"], ["employee"])

    def test_unknown_user_gets_empty_filter(self):
        p = search_params("ghost", LEVELS, [0.1], 20, 0.25)
        self.assertEqual(p["levels"], [])

    def test_sql_filters_on_clearance_and_uses_placeholders(self):
        self.assertIn("clearance_level = ANY(%(levels)s)", SEARCH_SQL)
        self.assertNotIn("{", SEARCH_SQL)
        self.assertNotIn("'", SEARCH_SQL.replace("::vector", ""))

    def test_withheld_query_returns_count_only(self):
        self.assertIn("count(*)", WITHHELD_SQL)
        self.assertNotIn("text", WITHHELD_SQL.lower().replace("not (", ""))

    def test_embedding_is_numeric_only(self):
        p = search_params("super", LEVELS, [1, 2.5], 5, 0.1)
        self.assertEqual(p["embedding"], "[1.0,2.5]")


if __name__ == "__main__":
    unittest.main()
