import tempfile
import unittest
from pathlib import Path

from app.access import can_ask
from app.config import ConfigError, UI, load_config
from app.pages import CSP, PAGES

LEVELS = ["employee", "super"]


class AskAccessTests(unittest.TestCase):
    def test_signed_in_roles_in_the_list_can_ask(self):
        ui = UI(demo_mode=False, allow_mock=False)
        self.assertTrue(can_ask("super", ui))
        self.assertTrue(can_ask("employee", ui))

    def test_nobody_signed_in_cannot_ask_even_in_demo_mode(self):
        for demo in (True, False):
            ui = UI(demo_mode=demo, allow_mock=demo)
            self.assertFalse(can_ask(None, ui))
            self.assertFalse(can_ask("hacker", ui))
            self.assertFalse(can_ask("", ui))

    def test_ask_can_be_limited_to_one_role(self):
        ui = UI(demo_mode=False, allow_mock=False, ask_roles=["super"])
        self.assertTrue(can_ask("super", ui))
        self.assertFalse(can_ask("employee", ui))

    def test_empty_ask_roles_means_nobody(self):
        self.assertFalse(can_ask("super", UI(demo_mode=False, allow_mock=False, ask_roles=[])))


class ConfigRuleTests(unittest.TestCase):
    def _cfg(self, text):
        c = Path(tempfile.mkdtemp()) / "c.yaml"
        c.write_text(text)
        return c

    def test_mock_not_allowed_outside_demo(self):
        with self.assertRaises(ConfigError):
            load_config(self._cfg("ui:\n  demo_mode: false\n  allow_mock: true\n"), "nope.yaml", require_secrets=False)

    def test_ask_roles_must_be_real_levels(self):
        with self.assertRaises(ConfigError):
            load_config(self._cfg("ui:\n  ask_roles: [boss]\n"), "nope.yaml", require_secrets=False)

    def test_old_mode_settings_are_rejected(self):
        for old in ("ui:\n  default_mode: consumer\n", "ui:\n  modes_by_role: {}\n"):
            with self.assertRaises(ConfigError):
                load_config(self._cfg(old), "nope.yaml", require_secrets=False)


class PagesTests(unittest.TestCase):
    def test_pages_map_to_html_files_that_exist(self):
        web = Path(__file__).resolve().parent.parent / "app" / "web"
        self.assertEqual(set(PAGES), {"/", "/ask", "/add", "/review", "/signin"})
        for f in PAGES.values():
            self.assertTrue((web / f).exists(), f)

    def test_csp_forbids_inline_code(self):
        self.assertNotIn("unsafe-inline", CSP)
        self.assertNotIn("unsafe-eval", CSP)
        self.assertIn("frame-ancestors 'none'", CSP)


if __name__ == "__main__":
    unittest.main()
