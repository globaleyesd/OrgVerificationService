import tempfile
import unittest
from pathlib import Path

from app.config import Config, ConfigError, has_llm_key, load_config

GOOD_SECRETS = "llm:\n  api_key: abc\nadmin:\n  username: u\n  password: p\n"


def write(d, name, text):
    p = Path(d) / name
    p.write_text(text)
    return p


class ConfigTests(unittest.TestCase):
    def test_shipped_config_loads_without_secrets(self):
        cfg = load_config("config.yaml", "nope.yaml", require_secrets=False)
        self.assertEqual(cfg.clearance.levels, ["employee", "super"])

    def test_placeholder_secrets_refused(self):
        with tempfile.TemporaryDirectory() as d:
            s = write(d, "s.yaml", "admin:\n  username: u\n  password: CHANGE_ME\n")
            with self.assertRaises(ConfigError):
                load_config("config.yaml", s)

    def test_example_secrets_start_without_api_key(self):
        with tempfile.TemporaryDirectory() as d:
            s = write(d, "s.yaml", Path("secrets.example.yaml").read_text())
            self.assertFalse(has_llm_key(load_config("config.yaml", s)))
            s = write(d, "p.yaml", "llm:\n  api_key: CHANGE_ME\n")
            self.assertFalse(has_llm_key(load_config("config.yaml", s)))
            s = write(d, "b.yaml", "llm:\n  api_key:\n")
            self.assertFalse(has_llm_key(load_config("config.yaml", s)))

    def test_good_secrets_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            s = write(d, "s.yaml", GOOD_SECRETS)
            cfg = load_config("config.yaml", s)
            self.assertEqual(cfg.secrets.llm.api_key, "abc")

    def test_missing_secrets_file_allowed(self):
        self.assertFalse(has_llm_key(load_config("config.yaml", "does-not-exist.yaml")))

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "project_name: x\nllm:\n  modle_answer: typo\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_secrets_in_main_config_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "project_name: x\nsecrets:\n  llm:\n    api_key: oops\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_bad_default_upload_level_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "clearance:\n  levels: [a, b]\n  default_upload_level: zzz\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_overlap_must_be_smaller_than_chunk(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "ingestion:\n  chunk_size_chars: 100\n  chunk_overlap_chars: 100\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_local_override_merges_over_main_config(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "branding:\n  app_name: Public Name\n  theme:\n    accent: '#112233'\n")
            write(d, "config.local.yaml", "branding:\n  app_name: Local Name\n")
            cfg = load_config(c, "nope.yaml", require_secrets=False)
            self.assertEqual(cfg.branding.app_name, "Local Name")
            self.assertEqual(cfg.branding.theme.accent, "#112233")   # untouched sibling survives the merge

    def test_secrets_not_allowed_in_local_override(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "project_name: x\n")
            write(d, "config.local.yaml", "secrets:\n  llm:\n    api_key: oops\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_theme_colour_must_be_hex(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "branding:\n  theme:\n    accent: 'red; background:url(x)'\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_bedrock_needs_no_secrets_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "llm:\n  provider: bedrock\n")
            cfg = load_config(c, "does-not-exist.yaml")
            self.assertEqual(cfg.llm.provider, "bedrock")

    def test_credentials_backend_validated(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "credentials:\n  backend: cloud9\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_rates_must_not_be_negative(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "costs:\n  rates:\n    instance_hourly_usd: -1\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False)

    def test_s3_backend_needs_a_valid_bucket(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "credentials:\n  backend: s3\n")
            with self.assertRaises(ConfigError):
                load_config(c, "nope.yaml", require_secrets=False, env={})
            ok = write(d, "c2.yaml", "credentials:\n  backend: s3\n  s3_bucket: my-creds-bucket\n  s3_prefix: creds/\n")
            self.assertEqual(load_config(ok, "nope.yaml", require_secrets=False, env={}).credentials.s3_bucket, "my-creds-bucket")
            bad = write(d, "c3.yaml", "credentials:\n  backend: s3\n  s3_bucket: ok-bucket\n  s3_prefix: '../x'\n")
            with self.assertRaises(ConfigError):
                load_config(bad, "nope.yaml", require_secrets=False, env={})

    def test_environment_can_switch_the_credential_store_for_a_deployment(self):
        with tempfile.TemporaryDirectory() as d:
            c = write(d, "c.yaml", "project_name: x\n")
            env = {"APP_CREDENTIALS_BACKEND": "s3", "APP_CREDENTIALS_S3_BUCKET": "bucket-from-env"}
            cfg = load_config(c, "nope.yaml", require_secrets=False, env=env)
            self.assertEqual((cfg.credentials.backend, cfg.credentials.s3_bucket), ("s3", "bucket-from-env"))
            plain = load_config(c, "nope.yaml", require_secrets=False, env={})
            self.assertEqual(plain.credentials.backend, "file")

    def test_switch_mode_is_validated_and_can_be_set_by_the_deployment(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_config(write(d, "a.yaml", "project_name: x\n"), "n.yaml", require_secrets=False, env={}).service_switch.mode, "local")
            with self.assertRaises(ConfigError):
                load_config(write(d, "b.yaml", "service_switch:\n  mode: cloud\n"), "n.yaml", require_secrets=False, env={})
            cfg = load_config(write(d, "c.yaml", "project_name: x\n"), "n.yaml", require_secrets=False, env={"APP_SERVICE_SWITCH_MODE": "aws"})
            self.assertEqual(cfg.service_switch.mode, "aws")

    def test_document_cap_defaults_to_100_in_code_and_in_the_shipped_config(self):
        self.assertEqual(Config().ingestion.max_documents, 100)
        self.assertEqual(load_config("config.yaml", "nope.yaml", require_secrets=False, env={}).ingestion.max_documents, 100)


if __name__ == "__main__":
    unittest.main()
