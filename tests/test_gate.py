import unittest

from app.gate import ALWAYS_ALLOWED, blocked_when_off


class GateTests(unittest.TestCase):
    def test_real_endpoints_blocked_when_off(self):
        for p in ("/api/ask", "/api/upload", "/api/settings/public", "/api/costs/estimate", "/api/anything-new"):
            self.assertTrue(blocked_when_off(p), p)

    def test_only_health_status_and_switch_are_open(self):
        self.assertEqual(ALWAYS_ALLOWED, {"/api/health", "/api/service/status", "/api/service/on", "/api/service/off"})
        for p in ALWAYS_ALLOWED:
            self.assertFalse(blocked_when_off(p), p)

    def test_trailing_slash_and_lookalikes_do_not_bypass(self):
        self.assertFalse(blocked_when_off("/api/service/status/"))
        self.assertTrue(blocked_when_off("/api/service/status/extra"))
        self.assertTrue(blocked_when_off("/api/service"))
        self.assertTrue(blocked_when_off("/api/health2"))
        self.assertTrue(blocked_when_off("/api"))

    def test_static_page_is_not_gated(self):
        for p in ("/", "/index.html", "/favicon.ico", "/apiary"):
            self.assertFalse(blocked_when_off(p), p)


if __name__ == "__main__":
    unittest.main()
