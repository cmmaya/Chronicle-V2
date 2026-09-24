"""BU130 - Google account sign-in (OAuth desktop flow), network mocked."""
import base64
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests

from src import paths
from src import secrets
from src.calendar_sync import google_auth as ga

INSTALLED = {
    "installed": {
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": "GOCSPX-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}
REFRESH = "1//refresh-token-value"
ACCESS = "ya29.access-token-value"


def _id_token(email):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def _response(status, body):
    response = mock.Mock(status_code=status)
    response.json.return_value = body
    return response


class _FakeKeyring:
    def __init__(self):
        self.store = {}
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
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {paths.DATA_DIR_ENV: self._tmp.name})
        self._env.start()
        os.environ.pop(ga.CLIENT_ID_ENV, None)
        os.environ.pop(ga.CLIENT_SECRET_ENV, None)
        self.keyring = _FakeKeyring()
        self._kr = mock.patch.dict("sys.modules", {"keyring": self.keyring})
        self._kr.start()
        self._res = mock.patch.object(paths, "resource_dir", return_value=paths.Path(self._tmp.name) / "res")
        self._res.start()
        ga._forget_access_token()
        self.client = ga.parse_client(INSTALLED, ga.SOURCE_CHRONICLE)

    def tearDown(self):
        ga._forget_access_token()
        self._res.stop()
        self._kr.stop()
        self._env.stop()
        self._tmp.cleanup()

    def stored_refresh(self):
        return self.keyring.store.get((secrets.KEYRING_SERVICE, secrets.GOOGLE_REFRESH_TOKEN_USER))

    def connect(self, source=ga.SOURCE_CHRONICLE):
        secrets.set_google_refresh_token(REFRESH)
        ga._update_prefs(**{ga.PREF_EMAIL: "me@example.com", ga.PREF_SOURCE: source})


class ClientTests(_Base):
    def test_installed_client_accepted(self):
        client = ga.parse_client(INSTALLED, ga.SOURCE_CUSTOM)
        self.assertEqual(client.client_id, "123.apps.googleusercontent.com")
        self.assertNotIn("GOCSPX", repr(client))

    def test_web_client_rejected(self):
        with self.assertRaisesRegex(ga.GoogleAuthError, "Desktop app"):
            ga.parse_client({"web": INSTALLED["installed"]}, ga.SOURCE_CUSTOM)

    def test_missing_field_rejected(self):
        data = {"installed": dict(INSTALLED["installed"], token_uri="")}
        with self.assertRaisesRegex(ga.GoogleAuthError, "token_uri"):
            ga.parse_client(data, ga.SOURCE_CUSTOM)

    def test_chronicle_client_unavailable_then_from_env(self):
        self.assertIsNone(ga.load_client(ga.SOURCE_CHRONICLE))
        os.environ[ga.CLIENT_ID_ENV] = "id"
        os.environ[ga.CLIENT_SECRET_ENV] = "secret"
        client = ga.load_client(ga.SOURCE_CHRONICLE)
        self.assertEqual((client.client_id, client.token_uri), ("id", ga.DEFAULT_TOKEN_URI))

    def test_import_custom_client_copies_valid_file(self):
        src = paths.Path(self._tmp.name) / "client_secret.json"
        src.write_text(json.dumps(INSTALLED), encoding="utf-8")
        self.assertIsNone(ga.load_client(ga.SOURCE_CUSTOM))
        ga.import_custom_client(src)
        self.assertEqual(ga.load_client(ga.SOURCE_CUSTOM).source, ga.SOURCE_CUSTOM)

    def test_import_rejects_web_client_without_copying(self):
        src = paths.Path(self._tmp.name) / "client_secret.json"
        src.write_text(json.dumps({"web": INSTALLED["installed"]}), encoding="utf-8")
        with self.assertRaises(ga.GoogleAuthError):
            ga.import_custom_client(src)
        self.assertFalse(paths.custom_google_client_path().exists())


class ConsentUrlTests(_Base):
    def test_url_parameters(self):
        flow = ga.SignInFlow(self.client)
        url = flow.authorization_url("http://127.0.0.1:5555")
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["code_challenge"], flow.code_challenge)
        self.assertEqual(query["state"], flow.state)
        self.assertEqual(query["access_type"], "offline")
        self.assertEqual(query["prompt"], "consent")
        self.assertEqual(
            query["scope"].split(),
            ["openid", "email", "https://www.googleapis.com/auth/calendar.events"],
        )


class CallbackTests(_Base):
    def test_wrong_state_rejected(self):
        flow = ga.SignInFlow(self.client)
        with self.assertRaises(ga.GoogleAuthError) as ctx:
            flow.check_callback({"state": "other", "code": "c"})
        self.assertNotIsInstance(ctx.exception, ga.SignInCancelled)

    def test_access_denied_is_cancelled(self):
        flow = ga.SignInFlow(self.client)
        with self.assertRaisesRegex(ga.SignInCancelled, "cancelled"):
            flow.check_callback({"state": flow.state, "error": "access_denied"})


class FlowTests(_Base):
    def _browser(self, params_for):
        """A fake browser that follows the redirect with ``params_for(flow_state)``."""
        def open_browser(url):
            query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            params = params_for(query["state"])
            threading.Thread(
                target=requests.get, args=(query["redirect_uri"],),
                kwargs={"params": params, "timeout": 5}, daemon=True,
            ).start()
            return True
        return open_browser

    def _token_reply(self, scope):
        return _response(200, {
            "access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3599,
            "scope": scope, "id_token": _id_token("me@example.com"),
        })

    def test_full_sign_in_stores_refresh_token_and_email(self):
        flow = ga.SignInFlow(self.client, open_browser=self._browser(lambda s: {"state": s, "code": "abc"}))
        reply = self._token_reply("openid email " + ga.CALENDAR_SCOPE)
        with mock.patch.object(ga.requests, "post", return_value=reply) as post:
            self.assertEqual(flow.run(), "me@example.com")
        sent = post.call_args.kwargs["data"]
        self.assertEqual((sent["code"], sent["grant_type"]), ("abc", "authorization_code"))
        self.assertIn("code_verifier", sent)
        self.assertEqual(self.stored_refresh(), REFRESH)
        self.assertEqual(ga.connection_state(), (True, "me@example.com", ga.SOURCE_CHRONICLE))
        self.assertEqual(ga.get_access_token(), ACCESS)

    def test_access_denied_through_browser_stores_nothing(self):
        flow = ga.SignInFlow(self.client, open_browser=self._browser(
            lambda s: {"state": s, "error": "access_denied"}))
        with mock.patch.object(ga.requests, "post") as post:
            with self.assertRaises(ga.SignInCancelled):
                flow.run()
        post.assert_not_called()
        self.assertIsNone(self.stored_refresh())

    def test_missing_calendar_scope_fails_and_stores_nothing(self):
        flow = ga.SignInFlow(self.client, open_browser=self._browser(lambda s: {"state": s, "code": "abc"}))
        with mock.patch.object(ga.requests, "post", return_value=self._token_reply("openid email")):
            with self.assertRaisesRegex(ga.GoogleAuthError, "Calendar permission was not granted"):
                flow.run()
        self.assertIsNone(self.stored_refresh())
        self.assertEqual(ga.connection_state(), (False, None, None))

    def test_cancel_stops_waiting(self):
        flow = ga.SignInFlow(self.client, open_browser=lambda url: True)
        threading.Timer(0.3, flow.cancel).start()
        with self.assertRaises(ga.SignInCancelled):
            flow.run()

    def test_timeout(self):
        flow = ga.SignInFlow(self.client, timeout=0.3, open_browser=lambda url: True)
        with self.assertRaisesRegex(ga.GoogleAuthError, "timed out"):
            flow.run()


class RefreshTests(_Base):
    def setUp(self):
        super().setUp()
        os.environ[ga.CLIENT_ID_ENV] = "id"
        os.environ[ga.CLIENT_SECRET_ENV] = "secret"

    def test_refresh_then_cached(self):
        self.connect()
        reply = _response(200, {"access_token": ACCESS, "expires_in": 3599})
        with mock.patch.object(ga.requests, "post", return_value=reply) as post:
            self.assertEqual(ga.get_access_token(), ACCESS)
            self.assertEqual(ga.get_access_token(), ACCESS)
            self.assertEqual(post.call_count, 1)
            ga.get_access_token(force_refresh=True)
            self.assertEqual(post.call_count, 2)

    def test_invalid_grant_deletes_token(self):
        self.connect()
        reply = _response(400, {"error": "invalid_grant"})
        with mock.patch.object(ga.requests, "post", return_value=reply):
            with self.assertRaises(ga.GoogleAuthExpired):
                ga.get_access_token()
        self.assertIsNone(self.stored_refresh())
        self.assertFalse(ga.connection_state()[0])

    def test_not_connected_raises_expired(self):
        with self.assertRaises(ga.GoogleAuthExpired):
            ga.get_access_token()


class DisconnectTests(_Base):
    def test_clears_local_state_when_revoke_fails(self):
        self.connect()
        with mock.patch.object(ga.requests, "post", side_effect=requests.ConnectionError("down")):
            ga.disconnect()
        self.assertIsNone(self.stored_refresh())
        self.assertEqual(ga.connection_state(), (False, None, None))

    def test_revokes_refresh_token(self):
        self.connect()
        with mock.patch.object(ga.requests, "post", return_value=_response(200, {})) as post:
            ga.disconnect()
        self.assertEqual(post.call_args.args[0], ga.REVOKE_URL)
        self.assertEqual(post.call_args.kwargs["data"], {"token": REFRESH})


class RedactTests(_Base):
    def test_tokens_and_client_secret_redacted(self):
        self.connect()
        secrets.register_secret(ACCESS)
        text = secrets.redact(f"boom {REFRESH} {ACCESS} {INSTALLED['installed']['client_secret']}")
        for value in (REFRESH, ACCESS, INSTALLED["installed"]["client_secret"]):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
