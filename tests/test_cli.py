import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from app import cli
from app.credstore import FileCredentialStore

ROOT = Path(__file__).resolve().parent.parent


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
        cfg["credentials"]["path"] = str(self.tmp / "creds")
        (self.tmp / "config.yaml").write_text(yaml.safe_dump(cfg))
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.store = FileCredentialStore(self.tmp / "creds")

    def tearDown(self):
        os.chdir(self.cwd)

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_service_on_and_off_from_the_command_line_need_the_switch_password(self):
        from unittest import mock
        from app.config import load_config
        from app.service_switch import ServiceSwitch
        cfg = load_config(require_secrets=False)
        ServiceSwitch.from_config(cfg, self.store).set_password("a-good-switch-password")
        with mock.patch("getpass.getpass", return_value="wrong-password"):
            code, out = self.run_cli("service", "on")
        self.assertEqual((code, out.strip()), (1, "Wrong password."))
        with mock.patch("getpass.getpass", return_value="a-good-switch-password"):
            self.assertEqual(self.run_cli("service", "on"), (0, "The service is now on.\n"))
            self.assertEqual(self.run_cli("service", "status")[1], "The service is on.\n")
            self.assertEqual(self.run_cli("service", "off")[1], "The service is now off.\n")
        self.assertEqual(self.run_cli("service", "sideways")[0], 2)

    def test_create_demo_users_makes_eileen_super_and_allminuseileen_employee(self):
        code, out = self.run_cli("create-demo-users")
        self.assertEqual(code, 0)
        self.assertEqual(self.store.read("users/eileen")["role"], "super")
        self.assertEqual(self.store.read("users/eileen")["display_name"], "Eileen")
        self.assertEqual(self.store.read("users/allminuseileen")["role"], "employee")
        self.assertEqual(self.store.read("users/allminuseileen")["display_name"], "AllMinusEileen")

    def test_set_llm_key_saves_it_in_the_credentials_store_and_the_app_uses_it(self):
        from unittest import mock
        from app.config import load_config
        from app.llm import use_stored_key
        with mock.patch("getpass.getpass", return_value="not-a-key"):
            self.assertEqual(self.run_cli("set-llm-key")[0], 1)
        with mock.patch("getpass.getpass", return_value="sk-ant-test-key "):
            code, out = self.run_cli("set-llm-key")
        self.assertEqual(code, 0)
        self.assertNotIn("sk-ant", out)
        self.assertEqual(self.store.read("llm_api_key"), {"api_key": "sk-ant-test-key"})
        cfg = load_config(require_secrets=False)
        cfg.llm.provider, cfg.secrets.llm.api_key = "anthropic", ""
        use_stored_key(cfg, self.store)
        self.assertEqual(cfg.secrets.llm.api_key, "sk-ant-test-key")
        cfg.llm.provider, cfg.secrets.llm.api_key = "local", ""
        use_stored_key(cfg, self.store)
        self.assertEqual(cfg.secrets.llm.api_key, "")

    def test_passwords_are_shown_once_and_stored_only_as_hashes(self):
        _, out = self.run_cli("create-demo-users")
        pws = re.findall(r"password: (\S+)", out)
        self.assertEqual(len(pws), 4)                       # Eileen, AllMinusEileen, Mark, AllMinusMark
        self.assertEqual(len(set(pws)), 4)
        for pw in pws:
            self.assertGreaterEqual(len(pw), 12)
            for name in ("users/eileen", "users/allminuseileen", "users/mark", "users/allminusmark"):
                self.assertNotIn(pw, str(self.store.read(name)))

    def test_generated_passwords_actually_sign_in(self):
        _, out = self.run_cli("create-demo-users")
        pws = dict(re.findall(r"(Eileen|AllMinusEileen)\s+role: \S+\s+password: (\S+)", out))
        from app.auth import UserStore
        users = UserStore(self.store, ["employee", "super"])
        self.assertEqual(users.verify("eileen", pws["Eileen"]).role, "super")
        self.assertEqual(users.verify("allminuseileen", pws["AllMinusEileen"]).role, "employee")

    def test_running_again_does_not_overwrite_without_reset(self):
        self.run_cli("create-demo-users")
        before = self.store.read("users/eileen")
        code, out = self.run_cli("create-demo-users")
        self.assertIn("already exists", out)
        self.assertNotIn("password:", out)
        self.assertEqual(self.store.read("users/eileen"), before)
        _, out = self.run_cli("create-demo-users", "--reset")
        self.assertEqual(len(re.findall(r"password: (\S+)", out)), 4)
        self.assertNotEqual(self.store.read("users/eileen"), before)

    def test_list_users_shows_names_and_roles_but_no_hashes(self):
        self.run_cli("create-demo-users")
        _, out = self.run_cli("list-users")
        self.assertIn("eileen", out); self.assertIn("allminuseileen", out)
        self.assertIn("super", out); self.assertIn("employee", out)
        self.assertNotIn("scrypt", out)

    def test_unknown_command_prints_usage(self):
        code, out = self.run_cli("bogus")
        self.assertEqual(code, 2)
        self.assertIn("create-demo-users", out)


class DocumentCommandTests(CliTests):
    def setUp(self):
        super().setUp()
        from app.embeddings import HashingEmbedder
        from app.files import LocalFileStore
        from app.repo import MemoryRepository
        from app.services import Services
        self.repo = MemoryRepository()
        self.sv = Services(self.repo, HashingEmbedder(384), LocalFileStore(self.tmp / "files"), None)

    def run_with(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args), services=self.sv)
        return code, out.getvalue()

    def test_ingest_a_folder_at_a_chosen_level_and_at_the_default(self):
        code, out = self.run_with("ingest", str(ROOT / "samples" / "employee"), "--level", "employee")
        self.assertEqual((code, out.count("added")), (0, 3))
        self.assertEqual({d.level for d in self.repo.list_documents()}, {"employee"})
        code, out = self.run_with("ingest", str(ROOT / "samples" / "super"))
        self.assertEqual((code, out.count("added")), (0, 4))
        self.assertEqual(sorted({d.level for d in self.repo.list_documents()}), ["employee", "super"])

    def test_ingest_skips_unsupported_files_and_reports_refusals(self):
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "a.exe").write_bytes(b"MZ")
        (self.tmp / "bin" / "empty.txt").write_bytes(b"")
        code, out = self.run_with("ingest", str(self.tmp / "bin"))
        self.assertEqual(code, 1)
        self.assertIn("skipped", out); self.assertIn("refused", out)

    def test_list_and_relabel(self):
        self.run_with("ingest", str(ROOT / "samples" / "super"))
        _, out = self.run_with("list-documents")
        self.assertIn("super", out)
        doc = self.repo.list_documents()[0]
        self.assertEqual(self.run_with("set-document-level", str(doc.id), "employee")[0], 0)
        self.assertEqual(self.repo.list_documents()[0].level, "employee")
        self.assertEqual(self.run_with("set-document-level", "999", "employee")[0], 1)
        self.assertEqual(self.run_with("set-document-level", str(doc.id), "boss")[0], 1)

    def test_reindex_keeps_documents_and_levels(self):
        self.run_with("ingest", str(ROOT / "samples" / "employee"), "--level", "employee")
        before = [(d.id, d.level) for d in self.repo.list_documents()]
        code, out = self.run_with("reindex")
        self.assertEqual((code, out.count("reindexed")), (0, len(before)))
        self.assertEqual([(d.id, d.level) for d in self.repo.list_documents()], before)


class DocsMatchCliTests(unittest.TestCase):
    def test_every_cli_command_in_the_docs_exists(self):
        real = {"set-switch-password", "create-demo-users", "set-user-password", "list-users", "ingest", "list-documents", "set-document-level", "reindex", "service", "set-llm-key", "ai-costs"}
        used = set()
        for f in list((ROOT / "docs").glob("*.md")) + [ROOT / "README.md", ROOT / "deploy" / "README.md"]:
            used |= set(re.findall(r"app\.cli ([a-z-]+)", f.read_text()))
        self.assertTrue(used)
        self.assertTrue(used <= real, used - real)


if __name__ == "__main__":
    unittest.main()
