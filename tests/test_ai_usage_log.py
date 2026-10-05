"""The AI_USAGE log line the Control Center reads for the live Claude API cost."""
import json
import unittest

from app.costs import ai_usage_line


class AiUsageLineTests(unittest.TestCase):
    PRICES = {"claude-haiku-4-5-20251001": [1.0, 5.0], "qwen3:4b": [0, 0]}

    def test_a_claude_call_is_priced_at_list_prices(self):
        line = ai_usage_line("claude-haiku-4-5-20251001", 3000, 300, self.PRICES, "answer")
        self.assertTrue(line.startswith("AI_USAGE {"))
        d = json.loads(line[len("AI_USAGE "):])
        self.assertEqual((d["model"], d["input_tokens"], d["output_tokens"], d["kind"]), ("claude-haiku-4-5-20251001", 3000, 300, "answer"))
        self.assertAlmostEqual(d["usd"], 3000 / 1e6 * 1.0 + 300 / 1e6 * 5.0, places=4)
        self.assertEqual((d["local"], d["priced"]), (False, True))
        self.assertNotIn("\n", line)                                   # one line, so the logs keep it whole

    def test_a_local_model_costs_nothing_and_a_cached_answer_logs_nothing(self):
        d = json.loads(ai_usage_line("qwen3:4b", 500, 50, self.PRICES, "suggest")[len("AI_USAGE "):])
        self.assertEqual((d["usd"], d["local"]), (0.0, True))
        self.assertIsNone(ai_usage_line("claude-haiku-4-5-20251001", 0, 0, self.PRICES, "answer"))

    def test_an_unpriced_paid_model_is_flagged_not_shown_as_free(self):
        d = json.loads(ai_usage_line("claude-new-model", 10, 10, self.PRICES, "answer")[len("AI_USAGE "):])
        self.assertEqual((d["local"], d["priced"]), (False, False))


if __name__ == "__main__":
    unittest.main()
