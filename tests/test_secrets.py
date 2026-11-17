"""Key Vault values must not appear in output, journals, or error text."""

import io
import json
import os
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from helpers import make_interp

from agen.errors import AgenRuntimeError
from agen.secrets import SecretStore

SECRET = "super-secret-value"
TOKEN = "test-token-not-a-secret-name"


class FakeVault:
    def __init__(self):
        self.calls = []
        self.fail_401 = True

    def __call__(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append(url)
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            raise AssertionError("token was not sent as a bearer token")
        if self.fail_401:
            self.fail_401 = False
            raise AgenRuntimeError(f"HTTP 401 from {url}", retryable=False)
        if url.endswith("/secrets?api-version=7.4"):
            return json.dumps({"value": [{"id": "https://demo.vault.azure.net/secrets/API-KEY"}]})
        if url.endswith("/secrets/API-KEY?api-version=7.4"):
            return json.dumps({"value": SECRET})
        if url.endswith("/secrets/AZURE_OPENAI_KEY?api-version=7.4"):
            raise AgenRuntimeError(f"HTTP 404 from {url}", retryable=False)
        if url.endswith("/secrets/AZURE-OPENAI-KEY?api-version=7.4"):
            return json.dumps({"value": SECRET})
        raise AgenRuntimeError(f"HTTP 404 from {url}", retryable=False)


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.vault = FakeVault()
        self.store = SecretStore(token_provider=lambda: TOKEN, transport=self.vault)
        self.store.install()
        self._saved = {}

    def tearDown(self):
        for key in ("API-KEY", "API_KEY", "AZURE-OPENAI-KEY", "AZURE_OPENAI_KEY", "AGEN_KEYVAULT_NAME"):
            if key in self._saved:
                os.environ[key] = self._saved[key]
            else:
                os.environ.pop(key, None)

    def remember(self, *keys):
        for key in keys:
            if key in os.environ:
                self._saved[key] = os.environ[key]

    def test_rejects_a_vault_that_is_not_azure(self):
        with self.assertRaises(AgenRuntimeError):
            self.store.open("https://evil.example/steal")
        self.assertEqual(self.vault.calls, [])

    def test_get_redacts_and_maps_underscores_to_hyphens(self):
        secret = self.store.get("demo", "AZURE_OPENAI_KEY")
        self.assertEqual(str(secret), 'secret("AZURE-OPENAI-KEY")')
        self.assertNotIn(SECRET, str(secret))
        self.assertEqual(secret._value, SECRET)

    def test_load_sets_environment_and_journal_keeps_only_the_name(self):
        self.remember("API-KEY", "API_KEY")
        self.assertEqual(self.store.load("demo"), 1)
        self.assertEqual(os.environ["API-KEY"], SECRET)
        self.assertEqual(os.environ["API_KEY"], SECRET)

        source = textwrap.dedent("""
            let creds = KeyVaultCreds("demo")
            workflow remember() {
                step save {
                    handle = creds.get("API-KEY")
                }
                return handle
            }
            let out = remember()
        """)
        with tempfile.TemporaryDirectory() as state:
            interp = make_interp(state)
            interp.secrets._transport = self.vault
            interp.secrets._token_provider = lambda: TOKEN
            buf = io.StringIO()
            with redirect_stdout(buf):
                interp.run(__import__("agen").parse(source))
            text = next(Path(state, "runs").glob("*.json")).read_text(encoding="utf-8")
            events = Path(state, "events.jsonl").read_text(encoding="utf-8")
            shown = buf.getvalue() + text + events + str(interp.globals.vars["out"])
            self.assertNotIn(SECRET, shown)
            self.assertNotIn(TOKEN, shown)
            self.assertIn("API-KEY", text)
            self.assertEqual(str(interp.globals.vars["out"]), 'secret("API-KEY")')

    def test_vault_json_records_the_name_without_contacting_azure(self):
        self.remember("API-KEY", "AGEN_KEYVAULT_NAME")
        os.environ["API-KEY"] = "from-dotenv"
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "vault.json").write_text('{"vault": "demo"}', encoding="utf-8")
            self.assertTrue(self.store.activate_file(directory))
        self.assertEqual(self.vault.calls, [])
        self.assertEqual(os.environ["API-KEY"], "from-dotenv")
        self.assertEqual(os.environ["AGEN_KEYVAULT_NAME"], "demo")

    def test_error_text_does_not_include_the_token(self):
        def boom(method, url, body=None, headers=None, timeout=None):
            raise AgenRuntimeError("HTTP 500 from vault", retryable=True)

        store = SecretStore(token_provider=lambda: TOKEN, transport=boom)
        with self.assertRaises(AgenRuntimeError) as ctx:
            store.get("demo", "API-KEY")
        self.assertNotIn(TOKEN, str(ctx.exception))


class ProgramTests(unittest.TestCase):
    def test_class_is_available_and_print_is_redacted(self):
        vault = FakeVault()
        with tempfile.TemporaryDirectory() as state:
            interp = make_interp(state)
            interp.secrets._transport = vault
            interp.secrets._token_provider = lambda: TOKEN
            buf = io.StringIO()
            with redirect_stdout(buf):
                interp.run(__import__("agen").parse(textwrap.dedent("""
                    let creds = KeyVaultCreds("demo")
                    print(creds.get("API-KEY"))
                    print(creds.creds())
                """)))
        shown = buf.getvalue()
        self.assertIn('secret("API-KEY")', shown)
        self.assertNotIn(SECRET, shown)
        self.assertNotIn(TOKEN, shown)


if __name__ == "__main__":
    unittest.main()
