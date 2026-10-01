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


class ByServiceTests(unittest.TestCase):
    """The dashboard's daily split by service (app.cost_services)."""

    def run_it(self, meters, days=5, status=None):
        from app.cost_services import by_service
        end = NOW
        start = (end - timedelta(days=days - 1)).replace(hour=0)
        prices = dict(CFG.costs.llm_prices_per_mtok, **{"qwen3:4b": [0, 0], "claude-x": [3.0, 15.0]})
        return by_service(meters, start, end, rates=CFG.costs.rates, llm_prices=prices, root_volume_gb=20,
                          heartbeat_seconds=300, status=status or {"server": {"state": "running", "detail": ""}})

    def meters(self, **kw):
        from app.cost_services import Meters
        day = NOW.replace(hour=0)
        base = dict(beats_by_day={day: 12}, tokens_by_day={}, documents=[], first_record=day)
        base.update(kw)
        return Meters(**base)

    def test_days_before_the_project_existed_cost_nothing(self):
        r = self.run_it(self.meters())
        self.assertEqual([d["total_usd"] for d in r["daily"][:4]], [0, 0, 0, 0])   # only today has records
        self.assertGreater(r["daily"][4]["total_usd"], 0)
        self.assertEqual(r["days"], 5)

    def test_costs_land_on_their_service_and_day(self):
        day = NOW.replace(hour=0)
        r = self.run_it(self.meters(tokens_by_day={day: {"claude-x": (1_000_000, 0), "qwen3:4b": (5_000_000, 5_000_000)}}))
        today = r["daily"][-1]["usd"]
        self.assertAlmostEqual(today["hosted_ai"], 3.0)
        self.assertAlmostEqual(today["server"], 1.0 * CFG.costs.rates.instance_hourly_usd)   # 12 beats x 300 s = 1 h
        self.assertNotIn("local_ai", today)                                                  # priced at zero: no cost
        self.assertAlmostEqual(r["total_usd"], sum(s["usd"] for s in r["services"]), places=3)

    def test_listed_when_costing_a_cent_or_running(self):
        status = {"server": {"state": "running", "detail": ""}, "local_ai": {"state": "idle", "detail": "up"},
                  "hosted_ai": {"state": "off", "detail": ""}, "database": {"state": "running", "detail": ""}}
        r = self.run_it(self.meters(), status=status)
        by_key = {s["key"]: s for s in r["services"]}
        self.assertTrue(by_key["server"]["costing"]); self.assertFalse(by_key["database"]["costing"])
        self.assertIn("local_ai", by_key); self.assertNotIn("hosted_ai", by_key)          # off and free: not listed
        self.assertEqual(r["running_now"], 3)
        self.assertEqual([s["key"] for s in r["series"]], [k for k in ("server", "disk", "public_ip") if k in [s["key"] for s in r["services"] if s["costing"]]])
        self.assertTrue(r["services"][0]["costing"])                                          # costing services first

    def test_unlisted_ollama_models_are_free_and_raise_no_warning(self):
        day = NOW.replace(hour=0)
        r = self.run_it(self.meters(tokens_by_day={day: {"gemma3:4b": (1000, 1000), "mystery-model": (1000, 0)}}))
        self.assertEqual(r["warnings"], ["No price configured for model 'mystery-model', so its usage is not costed"])

    def test_no_records_yet_means_no_cost(self):
        r = self.run_it(self.meters(beats_by_day={}, first_record=None))
        self.assertEqual(r["total_usd"], 0); self.assertIsNone(r["tracking_since"])


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
