"""BU122 - OpenRouter API key in the Windows credential store."""
import logging
import os
import unittest
from unittest import mock

import httpx

from src import secrets

KEY = "sk-or-v1-0123456789abcdefSECRET9876"


class _FakeKeyring:
    """In-memory stand-in for the keyring module."""

    def __init__(self, stored=None):
        self.store = {}
        if stored:
            self.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USER)] = stored
        from keyring.errors import PasswordDeleteError
        self._delete_error = PasswordDeleteError

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise self._delete_error("not found")
        del self.store[(service, user)]


class _Base(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(secrets.ENV_VAR, None)
        # .env must not leak the developer's real key into these tests.
        self._dotenv = mock.patch("dotenv.load_dotenv", return_value=False)
        self._dotenv.start()
        self.keyring = _FakeKeyring()
        self._kr = mock.patch.dict("sys.modules", {"keyring": self.keyring})
        self._kr.start()

    def tearDown(self):
        self._kr.stop()
        self._dotenv.stop()
        self._env.stop()


class LookupTests(_Base):
    def test_environment_wins_over_keyring(self):
        self.keyring.set_password(secrets.KEYRING_SERVICE, secrets.KEYRING_USER, "from-keyring")
        os.environ[secrets.ENV_VAR] = "from-env"
        self.assertEqual(secrets.get_api_key(), "from-env")
        self.assertEqual(secrets.key_source(), "environment")

    def test_keyring_used_when_no_environment(self):
        self.keyring.set_password(secrets.KEYRING_SERVICE, secrets.KEYRING_USER, "from-keyring")
        self.assertEqual(secrets.get_api_key(), "from-keyring")
        self.assertEqual(secrets.key_source(), "keyring")

    def test_no_key_anywhere(self):
        self.assertIsNone(secrets.get_api_key())
        self.assertIsNone(secrets.key_source())

    def test_set_and_clear(self):
        secrets.set_api_key(f"  {KEY} ")
        self.assertEqual(self.keyring.store[("Chronicle", "openrouter")], KEY)
        self.assertTrue(secrets.has_saved_key())
        secrets.clear_api_key()
        self.assertFalse(secrets.has_saved_key())
        secrets.clear_api_key()  # removing twice is not an error

    def test_set_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            secrets.set_api_key("   ")

    def test_broken_keyring_backend_reads_as_no_key(self):
        broken = mock.Mock()
        broken.get_password.side_effect = RuntimeError("no backend")
        with mock.patch.dict("sys.modules", {"keyring": broken}):
            self.assertIsNone(secrets.get_api_key())

    def test_mask_shows_last_four_only(self):
        masked = secrets.mask(KEY)
        self.assertTrue(masked.endswith("9876"))
        self.assertNotIn(KEY[:-4], masked)


class MissingKeyMessageTests(_Base):
    def test_openrouter_client_points_to_settings(self):
        from src.assistant.openrouter_client import MissingAPIKeyError, OpenRouterClient
        with self.assertRaises(MissingAPIKeyError) as ctx:
            OpenRouterClient()
        self.assertIn("Settings > API Key", str(ctx.exception))
        self.assertNotIn("environment variable", str(ctx.exception))

    def test_summary_generator_points_to_settings(self):
        from src.summarization.generator import SummaryError, SummaryGenerator
        with self.assertRaises(SummaryError) as ctx:
            SummaryGenerator().generate("some transcript", mock.Mock())
        self.assertIn("Settings > API Key", str(ctx.exception))

    def test_both_clients_use_the_keyring_key(self):
        from src.assistant.openrouter_client import OpenRouterClient
        from src.summarization.generator import SummaryGenerator
        self.keyring.set_password(secrets.KEYRING_SERVICE, secrets.KEYRING_USER, KEY)
        self.assertEqual(OpenRouterClient().api_key, KEY)
        self.assertEqual(SummaryGenerator().api_key, KEY)


class CheckApiKeyTests(unittest.TestCase):
    def _check_with(self, **kwargs):
        with mock.patch("httpx.get", **kwargs) as get:
            result = secrets.check_api_key(KEY)
        return result, get

    def _response(self, status):
        return httpx.Response(status, request=httpx.Request("GET", secrets.KEY_INFO_URL))

    def test_ok(self):
        (ok, message), get = self._check_with(return_value=self._response(200))
        self.assertTrue(ok)
        self.assertIn("works", message)
        self.assertEqual(get.call_args.args[0], secrets.KEY_INFO_URL)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertLessEqual(get.call_args.kwargs["timeout"], 15)

    def test_invalid_key(self):
        (ok, message), _ = self._check_with(return_value=self._response(401))
        self.assertFalse(ok)
        self.assertIn("rejected", message)

    def test_no_credits(self):
        (ok, message), _ = self._check_with(return_value=self._response(402))
        self.assertFalse(ok)
        self.assertIn("no credits", message)

    def test_offline_is_not_reported_as_invalid(self):
        (ok, message), _ = self._check_with(side_effect=httpx.ConnectError("no route"))
        self.assertFalse(ok)
        self.assertIn("internet connection", message)
        self.assertNotIn("rejected", message)

    def test_timeout(self):
        (ok, message), _ = self._check_with(side_effect=httpx.ReadTimeout("slow"))
        self.assertFalse(ok)
        self.assertIn("in time", message)

    def test_unexpected_status(self):
        (ok, message), _ = self._check_with(return_value=self._response(500))
        self.assertFalse(ok)
        self.assertIn("500", message)

    def test_empty_key_needs_no_request(self):
        with mock.patch("httpx.get") as get:
            ok, _ = secrets.check_api_key("  ")
        self.assertFalse(ok)
        get.assert_not_called()


class NeverLoggedTests(_Base):
    def test_key_never_in_logs(self):
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("probe").info("start")
            secrets.set_api_key(KEY)
            secrets.get_api_key()
            secrets.clear_api_key()
            with mock.patch("httpx.get", side_effect=httpx.ConnectError(f"fail {KEY}")):
                secrets.check_api_key(KEY)
        self.assertFalse(any(KEY in line for line in logs.output))

    def test_request_errors_are_redacted(self):
        from src.assistant.openrouter_client import APIRequestError, OpenRouterClient
        client = OpenRouterClient(api_key=KEY, model="m")
        leaking = httpx.ConnectError(f"headers: Authorization: Bearer {KEY}")
        with mock.patch("httpx.Client.post", side_effect=leaking):
            with self.assertRaises(APIRequestError) as ctx:
                client.chat([{"role": "user", "content": "hi"}])
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIn(KEY[-4:], str(ctx.exception))

    def test_summary_request_errors_are_redacted(self):
        import requests
        from src.summarization.generator import SummaryError, SummaryGenerator
        gen = SummaryGenerator(api_key=KEY)
        leaking = requests.exceptions.ConnectionError(f"Bearer {KEY}")
        with mock.patch("requests.post", side_effect=leaking), \
                self.assertLogs("src.summarization.generator", level="WARNING") as logs:
            with self.assertRaises(SummaryError) as ctx:
                gen._call_api([{"role": "user", "content": "hi"}])
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertFalse(any(KEY in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
