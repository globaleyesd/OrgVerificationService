"""Static checks on the web pages. They catch the kind of mistake a browser test only finds by accident."""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
PAGES = ("index.html", "ask.html", "add.html", "review.html", "signin.html")


def chrome_ids():
    """ids that common.js adds to EVERY page (header, sign-in card, offline card, dialogs)."""
    js = (WEB / "common.js").read_text()
    parts = re.findall(r"const CHROME_(?:TOP|DIALOGS) = `(.*?)`;", js, re.S)
    return re.findall(r'\bid="([^"]+)"', "".join(parts))


class PageTests(unittest.TestCase):
    def test_served_pages_load_scripts_and_styles_by_content_version(self):
        import tempfile
        from app.pages import with_versions
        for page in PAGES:
            html = with_versions((WEB / page).read_text(), WEB)
            self.assertFalse(re.search(r'(src|href)="[\w-]+\.(js|css)"', html), page)   # every asset carries ?v=
        d = Path(tempfile.mkdtemp())
        (d / "a.js").write_text("one")
        first = with_versions('<script src="a.js"></script><script src="gone.js"></script>', d)
        (d / "a.js").write_text("two")
        second = with_versions('<script src="a.js"></script>', d)
        self.assertIn('src="gone.js"', first)                    # a missing file is left alone
        self.assertNotEqual(first.split('"')[1], second.split('"')[1])   # new contents, new address

    def test_no_duplicate_ids_on_any_page(self):
        shared = chrome_ids()
        self.assertEqual(len(shared), len(set(shared)), "duplicate id inside the shared chrome")
        for page in PAGES:
            own = re.findall(r'\bid="([^"]+)"', (WEB / page).read_text())
            allids = shared + own
            dupes = sorted({i for i in allids if allids.count(i) > 1})
            self.assertEqual(dupes, [], f"{page}: duplicate ids {dupes}")

    def test_every_script_and_style_is_a_separate_file_that_exists(self):
        for page in PAGES:
            html = (WEB / page).read_text()
            self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)[^>]*>", f"{page}: inline script")
            self.assertNotIn("<style", html, f"{page}: inline style block")
            self.assertNotRegex(html, r'\sstyle="', f"{page}: inline style attribute")
            for ref in re.findall(r'(?:src|href)="([^"#]+\.(?:js|css))"', html):
                self.assertTrue((WEB / ref).exists(), f"{page}: missing {ref}")

    def test_scripts_do_not_build_html_from_data(self):
        for f in WEB.glob("*.js"):
            code = f.read_text()
            self.assertNotIn("document.write", code, f.name)
            self.assertNotRegex(code, r"\.innerHTML\s*=", f.name)
            self.assertNotIn("eval(", code, f.name)

    def test_every_id_a_script_looks_up_exists_in_its_page_or_the_shared_chrome(self):
        shared = set(chrome_ids())
        for page, scripts in {"ask.html": ("common.js", "ask.js"), "add.html": ("common.js", "add.js"), "index.html": ("common.js", "home.js"), "review.html": ("common.js", "review.js"), "signin.html": ("common.js", "signin.js")}.items():
            html_ids = set(re.findall(r'\bid="([^"]+)"', (WEB / page).read_text())) | shared
            for js in scripts:
                for ref in set(re.findall(r'\$\("#([A-Za-z][\w-]*)"\)', (WEB / js).read_text())):
                    # ids created at run time by the scripts themselves
                    if ref in ("samples",):
                        continue
                    self.assertIn(ref, html_ids, f"{js} looks up #{ref} but {page} has no such element")

    def test_signing_in_happens_only_on_the_sign_in_screen(self):
        js = (WEB / "common.js").read_text()
        top = re.search(r"const CHROME_TOP = `(.*?)`;", js, re.S).group(1)
        self.assertNotIn("<form", top); self.assertNotIn("<input", top)          # no inline sign-in on any page
        self.assertIn('if (!settings.user && opts.page !== "signin") { goSignIn(); return; }', js)   # signed out: go to /signin
        self.assertIn('if (r.status === 401) { goSignIn();', js)                    # any 401 from the server: same
        for page in PAGES:
            html = (WEB / page).read_text()
            if page == "signin.html":
                self.assertIn('id="sppicker"', html); self.assertRegex(html, r'<form id="spform"[^>]*hidden')
                self.assertNotIn("placeholder=", html)
            else:
                self.assertNotIn('type="password"', html, page); self.assertNotIn('autocomplete="username"', html, page)

    def test_switching_user_and_signing_out_happen_only_on_the_sign_in_screen(self):
        js = (WEB / "common.js").read_text()
        top = re.search(r"const CHROME_TOP = `(.*?)`;", js, re.S).group(1)
        for gone in ('id="signout"', 'id="switchuser"', "Switch user", "Sign out"):
            self.assertNotIn(gone, top)
        for page in PAGES:
            html = (WEB / page).read_text()
            if page == "signin.html":
                self.assertIn('id="spout"', html)                  # its own Sign out
            else:
                self.assertNotIn("Sign out", html, page); self.assertNotIn("switch user", html.lower(), page)

    def test_pages_have_no_service_switch_button(self):   # turning projects on and off moved to the Control Center
        js = (WEB / "common.js").read_text()
        for gone in ('id="svcbtn"', 'id="onform"', 'id="svcdlg"', "/control/on", "/control/off"):
            self.assertNotIn(gone, js)


class OneFailedRequestNeverHidesTheSiteTests(unittest.TestCase):
    def test_the_offline_screen_needs_the_server_to_really_be_off(self):
        js = (WEB / "common.js").read_text(encoding="utf-8")
        api = js[js.index("async function api("):js.index("async function getStatus(")]
        i, j = api.index("if (await serverRunning())"), api.index("showOffline(offMessage())")
        self.assertLess(i, j)                                   # first ask whether the server is really off
        self.assertIn("throw new Error(\"The server didn't answer this request in time. Please try again.\")", api)
        self.assertIn('fetch("/control/status")', js)

    def test_ai_errors_are_not_turned_into_the_offline_screen(self):
        main = (WEB.parent / "main.py").read_text(encoding="utf-8")
        i = main.index('log.warning("AI service problem: %s", e)')
        self.assertIn("status_code=503", main[i:i + 400]); self.assertNotIn("status_code=502", main[i:i + 400])


if __name__ == "__main__":
    unittest.main()
