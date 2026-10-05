import unittest
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.costs import GIB, RangeError, Usage, estimate, parse_range

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
CFG = Config()


def lines(res):
    return {l["key"]: l["usd"] for l in res["lines"]}


class RangeTests(unittest.TestCase):
    def test_parses_iso_with_z(self):
        s, e = parse_range("2026-09-30T00:00:00Z", "2026-10-01T00:00:00Z", 366, NOW)
        self.assertEqual((e - s).total_seconds(), 86400)

    def test_end_before_start_rejected(self):
        with self.assertRaises(RangeError):
            parse_range("2026-10-01T00:00:00Z", "2026-09-30T00:00:00Z", 366, NOW)

    def test_garbage_rejected(self):
        with self.assertRaises(RangeError):
            parse_range("yesterday", "now", 366, NOW)

    def test_too_long_rejected(self):
        with self.assertRaises(RangeError):
            parse_range("2025-01-01T00:00:00Z", "2026-10-01T00:00:00Z", 366, NOW)

    def test_future_end_is_clamped_to_now(self):
        s, e = parse_range("2026-10-01T00:00:00Z", "2026-10-05T00:00:00Z", 366, NOW)
        self.assertEqual(e, NOW)

    def test_start_in_future_rejected(self):
        with self.assertRaises(RangeError):
            parse_range("2026-10-03T00:00:00Z", "2026-10-05T00:00:00Z", 366, NOW)


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.start = NOW - timedelta(hours=10)

    def test_compute_priced_by_uptime_not_period(self):
        r = estimate(self.start, NOW, Usage(uptime_seconds=5 * 3600), CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertAlmostEqual(lines(r)["compute"], 5 * 0.0168, places=4)

    def test_uptime_cannot_exceed_period(self):
        r = estimate(self.start, NOW, Usage(uptime_seconds=99 * 3600), CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertAlmostEqual(lines(r)["compute"], 10 * 0.0168, places=4)

    def test_ip_and_disk_billed_for_whole_period(self):
        r = estimate(self.start, NOW, Usage(), CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertAlmostEqual(lines(r)["public_ip"], 10 * 0.005, places=4)
        self.assertAlmostEqual(lines(r)["disk"], 20 * 0.08 * 10 / 730, places=4)

    def test_storage_prorated(self):
        r = estimate(self.start, NOW, Usage(storage_bytes=2 * GIB), CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertAlmostEqual(lines(r)["storage"], 2 * 0.023 * 10 / 730, places=4)

    def test_ai_priced_per_model(self):
        u = Usage(llm_tokens={"claude-haiku-4-5-20251001": (2_000_000, 1_000_000)})
        r = estimate(self.start, NOW, u, CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertAlmostEqual(lines(r)["ai:claude-haiku-4-5-20251001"], 2 * 1.0 + 1 * 5.0, places=4)

    def test_unknown_model_warns_and_costs_zero(self):
        u = Usage(llm_tokens={"mystery-model": (1_000_000, 1_000_000)})
        r = estimate(self.start, NOW, u, CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertEqual(lines(r)["ai:mystery-model"], 0)
        self.assertTrue(any("mystery-model" in w for w in r["warnings"]))

    def test_total_is_sum_of_lines_and_not_included_listed(self):
        u = Usage(uptime_seconds=3600, llm_tokens={"claude-sonnet-5-5": (1000, 1000)}, storage_bytes=GIB)
        r = estimate(self.start, NOW, u, CFG.costs.rates, CFG.costs.llm_prices_per_mtok, 20)
        self.assertAlmostEqual(r["total_usd"], sum(l["usd"] for l in r["lines"]), places=3)
        self.assertTrue(r["not_included"])


if __name__ == "__main__":
    unittest.main()


class AiCostTests(unittest.TestCase):
    def test_hosted_models_are_priced_local_ones_are_free_and_unknown_ones_flagged(self):
        from app.costs import ai_costs
        r = ai_costs({"claude-x": (1_000_000, 100_000), "qwen3:4b": (5_000_000, 1_000_000), "claude-mystery": (10, 10)},
                     {"claude-x": [3.0, 15.0], "qwen3:4b": [0, 0]})
        by = {m["model"]: m for m in r["models"]}
        self.assertAlmostEqual(by["claude-x"]["usd"], 3.0 + 1.5)
        self.assertEqual((by["qwen3:4b"]["usd"], by["qwen3:4b"]["local"]), (0.0, True))
        self.assertEqual((by["claude-mystery"]["usd"], by["claude-mystery"]["priced"]), (0.0, False))
        self.assertAlmostEqual(r["total_usd"], 4.5)
        self.assertTrue(ai_costs({"gemma3:4b": (1, 1)}, {})["models"][0]["local"])     # Ollama names are local
        self.assertTrue(ai_costs({"phi4-mini": (1, 1)}, {})["models"][0]["local"])     # even without a :tag
        for hosted in ("claude-sonnet-5-5", "anthropic.claude-x", "us.anthropic.claude-x", "amazon.nova-pro"):
            self.assertFalse(ai_costs({hosted: (1, 1)}, {})["models"][0]["priced"], hosted)   # never shown as free
