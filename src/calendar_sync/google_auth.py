"""Google account sign-in for the Calendar integration (BU130).

OAuth 2.0 for installed apps: the consent page opens in the default browser
and Google redirects to a one-shot server on ``http://127.0.0.1:<random
port>``. PKCE (``S256``) protects the code exchange.

Two OAuth clients are supported:

- ``chronicle``: shipped as ``resource_dir()/google_oauth_client.json`` (added
  at build time), or in development the ``CHRONICLE_GOOGLE_CLIENT_ID`` /
  ``CHRONICLE_GOOGLE_CLIENT_SECRET`` environment variables.
- ``custom``: a "Desktop app" ``client_secret.json`` the user imported,
  copied to ``data_dir()/google_client.json``.

The refresh token lives only in the credential store (``src.secrets``); the
access token only in memory. The connected email and the client source are
kept in ``preferences.json``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets as std_secrets
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from src import paths
from src import secrets as app_secrets

logger = logging.getLogger(__name__)

SOURCE_CHRONICLE = "chronicle"
SOURCE_CUSTOM = "custom"

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = ("openid", "email", CALENDAR_SCOPE)

CLIENT_ID_ENV = "CHRONICLE_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "CHRONICLE_GOOGLE_CLIENT_SECRET"

DEFAULT_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

PREF_EMAIL = "google_account_email"
PREF_SOURCE = "google_client_source"

SIGN_IN_TIMEOUT_SECONDS = 180
HTTP_TIMEOUT_SECONDS = 15
# Refresh this long before Google's stated expiry.
EXPIRY_MARGIN_SECONDS = 60

_REQUIRED_CLIENT_FIELDS = ("client_id", "client_secret", "auth_uri", "token_uri")

_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Chronicle</title></head>"
    "<body style='font-family:sans-serif;text-align:center;margin-top:4em'>"
    "<h2>{title}</h2><p>You can close this tab and return to Chronicle.</p></body></html>"
)


class GoogleAuthError(Exception):
    """Sign-in or token refresh failed; the message is meant for the user."""


class SignInCancelled(GoogleAuthError):
    """The user cancelled on the consent screen or in Chronicle."""


class GoogleAuthExpired(GoogleAuthError):
    """No usable refresh token: the user must reconnect their Google account."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str = field(repr=False)
    auth_uri: str
    token_uri: str
    source: str


# ---------------------------------------------------------------- clients

def parse_client(data: object, source: str) -> OAuthClient:
    """An :class:`OAuthClient` from a Google ``client_secret.json`` document."""
    if not isinstance(data, dict):
        raise GoogleAuthError("The client file is not a Google OAuth client JSON file.")
    if "installed" not in data:
        if "web" in data:
            raise GoogleAuthError(
                "This is a \"Web application\" client. Create a \"Desktop app\" "
                "OAuth client in Google Cloud and import that file instead."
            )
        raise GoogleAuthError("The client file has no \"installed\" (Desktop app) client.")
    installed = data["installed"]
    if not isinstance(installed, dict):
        raise GoogleAuthError("The client file's \"installed\" block is not valid.")
    missing = [
        name for name in _REQUIRED_CLIENT_FIELDS
        if not isinstance(installed.get(name), str) or not installed[name].strip()
    ]
    if missing:
        raise GoogleAuthError(f"The client file is missing: {', '.join(missing)}.")
    client = OAuthClient(
        client_id=installed["client_id"].strip(),
        client_secret=installed["client_secret"].strip(),
        auth_uri=installed["auth_uri"].strip(),
        token_uri=installed["token_uri"].strip(),
        source=source,
    )
    app_secrets.register_secret(client.client_secret)
    return client


def _read_client_file(path: Path, source: str) -> OAuthClient:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoogleAuthError(f"Could not read the client file: {type(exc).__name__}.") from None
    return parse_client(data, source)


def load_client(source: str) -> Optional[OAuthClient]:
    """The OAuth client for ``source``, or None when it is not available.

    Raises :class:`GoogleAuthError` when a client file exists but is invalid.
    """
    if source == SOURCE_CHRONICLE:
        path = paths.bundled_google_client_path()
        if path.is_file():
            return _read_client_file(path, source)
        client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
        client_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
        if client_id and client_secret:
            return parse_client(
                {"installed": {
                    "client_id": client_id, "client_secret": client_secret,
                    "auth_uri": DEFAULT_AUTH_URI, "token_uri": DEFAULT_TOKEN_URI,
                }},
                source,
            )
        return None
    if source == SOURCE_CUSTOM:
        path = paths.custom_google_client_path()
        return _read_client_file(path, source) if path.is_file() else None
    raise ValueError(f"Unknown OAuth client source: {source!r}")


def import_custom_client(path) -> OAuthClient:
    """Validate a user's ``client_secret.json`` and copy it to the data folder."""
    client = _read_client_file(Path(path), SOURCE_CUSTOM)
    target = paths.custom_google_client_path()
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(Path(path).read_bytes())
    os.replace(tmp, target)
    logger.info("Custom Google OAuth client imported")
    return client


# ---------------------------------------------------------------- preferences

def _read_prefs() -> dict:
    path = paths.preferences_path()
    try:
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except (OSError, ValueError) as exc:
        logger.warning("Could not read preferences: %s", exc)
    return {}


def _update_prefs(**changes) -> None:
    """Set (or, for None, remove) keys in ``preferences.json``, atomically."""
    prefs = _read_prefs()
    for key, value in changes.items():
        if value is None:
            prefs.pop(key, None)
        else:
            prefs[key] = value
    path = paths.preferences_path()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".preferences-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(prefs, f)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------------------------------------------------------------- tokens

_token_lock = threading.Lock()
_access_token: Optional[Tuple[str, float]] = None  # (token, monotonic expiry)


def _remember_access_token(payload: dict) -> str:
    global _access_token
    token = payload["access_token"]
    app_secrets.register_secret(token)
    expires_in = float(payload.get("expires_in") or 3600)
    _access_token = (token, time.monotonic() + expires_in - EXPIRY_MARGIN_SECONDS)
    return token


def _forget_access_token() -> None:
    global _access_token
    _access_token = None


def _error_code(response: requests.Response) -> str:
    try:
        return str(response.json().get("error") or "")
    except ValueError:
        return ""


def _post(url: str, data: dict) -> requests.Response:
    try:
        return requests.post(url, data=data, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Google request failed: %s", type(exc).__name__)
        raise GoogleAuthError(
            "Could not reach Google. Check your internet connection and try again."
        ) from None


def get_access_token(force_refresh: bool = False) -> str:
    """A valid access token, refreshed when expired (or when ``force_refresh``,
    e.g. after a 401).

    Raises :class:`GoogleAuthExpired` when no account is connected or Google
    rejects the refresh token (revoked or expired); the stored token is then
    deleted.
    """
    with _token_lock:
        if not force_refresh and _access_token and time.monotonic() < _access_token[1]:
            return _access_token[0]
        refresh_token = app_secrets.get_google_refresh_token()
        if not refresh_token:
            raise GoogleAuthExpired("Reconnect your Google account.")
        source = _read_prefs().get(PREF_SOURCE) or SOURCE_CHRONICLE
        client = load_client(source)
        if client is None:
            raise GoogleAuthError("The Google OAuth client used to sign in is no longer available.")
        response = _post(client.token_uri, {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        if response.status_code != 200:
            code = _error_code(response)
            if code == "invalid_grant":
                _forget_access_token()
                app_secrets.clear_google_refresh_token()
                logger.info("Google refresh token rejected; removed from the credential store")
                raise GoogleAuthExpired("Reconnect your Google account.")
            raise GoogleAuthError(
                f"Google refused to refresh the sign-in (HTTP {response.status_code} {code})."
            )
        return _remember_access_token(response.json())


# ---------------------------------------------------------------- sign-in

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _email_from_id_token(id_token: Optional[str]) -> Optional[str]:
    """The ``email`` claim of an ID token received directly from Google's
    token endpoint over TLS (so its signature need not be checked)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims.get("email") or None
    except Exception:  # noqa: BLE001 - fall back to the userinfo endpoint
        return None


def _email_from_userinfo(access_token: str) -> Optional[str]:
    try:
        response = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return response.json().get("email") or None
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Could not read the Google account email: %s", type(exc).__name__)
    return None


class SignInFlow:
    """One browser sign-in. :meth:`run` blocks, so call it on a worker thread;
    :meth:`cancel` may be called from any thread."""

    def __init__(
        self,
        client: OAuthClient,
        timeout: float = SIGN_IN_TIMEOUT_SECONDS,
        open_browser: Callable[[str], object] = webbrowser.open,
    ):
        self.client = client
        self.timeout = timeout
        self._open_browser = open_browser
        self.state = std_secrets.token_urlsafe(32)
        self._verifier = std_secrets.token_urlsafe(64)
        self.code_challenge = _b64url(hashlib.sha256(self._verifier.encode("ascii")).digest())
        self._cancelled = threading.Event()
        self._params: Optional[Dict[str, str]] = None

    def cancel(self) -> None:
        self._cancelled.set()

    def authorization_url(self, redirect_uri: str) -> str:
        query = {
            "client_id": self.client.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self.client.auth_uri}?{urlencode(query)}"

    def check_callback(self, params: Dict[str, str]) -> str:
        """The authorization code from the redirect's query parameters."""
        if params.get("state") != self.state:
            raise GoogleAuthError("Sign-in failed: the response did not match this request.")
        error = params.get("error")
        if error == "access_denied":
            raise SignInCancelled("Sign-in was cancelled.")
        if error:
            raise GoogleAuthError(f"Google returned an error: {error}.")
        code = params.get("code")
        if not code:
            raise GoogleAuthError("Google did not return an authorization code.")
        return code

    def _make_handler(self):
        flow = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                parsed = urlparse(self.path)
                if parsed.path != "/" or flow._params is not None:
                    self.send_error(404)
                    return
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                flow._params = params
                ok = params.get("state") == flow.state and "code" in params
                title = "Signed in to Google" if ok else "Sign-in did not complete"
                body = _PAGE.format(title=title).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):  # noqa: A002
                # The default logs the request line, which holds the code.
                pass

        return _Handler

    def _wait_for_callback(self) -> Dict[str, str]:
        server = HTTPServer(("127.0.0.1", 0), self._make_handler())
        server.timeout = 0.25
        try:
            redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
            self.redirect_uri = redirect_uri
            if not self._open_browser(self.authorization_url(redirect_uri)):
                logger.warning("The default browser may not have opened the Google sign-in page")
            deadline = time.monotonic() + self.timeout
            while self._params is None:
                if self._cancelled.is_set():
                    raise SignInCancelled("Sign-in was cancelled.")
                if time.monotonic() >= deadline:
                    raise GoogleAuthError("Sign-in timed out. Try again.")
                server.handle_request()
            return self._params
        finally:
            server.server_close()

    def run(self) -> str:
        """Sign in and store the refresh token. Returns the account email."""
        code = self.check_callback(self._wait_for_callback())
        if self._cancelled.is_set():
            raise SignInCancelled("Sign-in was cancelled.")
        response = _post(self.client.token_uri, {
            "code": code,
            "client_id": self.client.client_id,
            "client_secret": self.client.client_secret,
            "code_verifier": self._verifier,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })
        if response.status_code != 200:
            raise GoogleAuthError(
                f"Google refused the sign-in (HTTP {response.status_code} {_error_code(response)})."
            )
        tokens = response.json()
        for name in ("access_token", "refresh_token"):
            if tokens.get(name):
                app_secrets.register_secret(tokens[name])
        granted = set(str(tokens.get("scope") or "").split())
        if CALENDAR_SCOPE not in granted:
            raise GoogleAuthError("Calendar permission was not granted.")
        if not tokens.get("refresh_token"):
            raise GoogleAuthError("Google did not return a refresh token. Try signing in again.")
        email = _email_from_id_token(tokens.get("id_token")) or _email_from_userinfo(tokens["access_token"])
        if not email:
            raise GoogleAuthError("Could not read the Google account email.")

        with _token_lock:
            app_secrets.set_google_refresh_token(tokens["refresh_token"])
            _remember_access_token(tokens)
        _update_prefs(**{PREF_EMAIL: email, PREF_SOURCE: self.client.source})
        logger.info("Google account connected (%s client)", self.client.source)
        return email


# ---------------------------------------------------------------- state

def connection_state() -> Tuple[bool, Optional[str], Optional[str]]:
    """``(connected, email, source)``; connected means a refresh token is stored."""
    prefs = _read_prefs()
    connected = app_secrets.get_google_refresh_token() is not None
    return connected, prefs.get(PREF_EMAIL), prefs.get(PREF_SOURCE)


def disconnect() -> None:
    """Revoke the grant at Google (best effort), then clear local credentials."""
    try:
        token = app_secrets.get_google_refresh_token()
        if token:
            response = requests.post(REVOKE_URL, data={"token": token}, timeout=HTTP_TIMEOUT_SECONDS)
            if response.status_code != 200:
                logger.warning("Google did not revoke the token (HTTP %s)", response.status_code)
    except Exception as exc:  # noqa: BLE001 - local cleanup must still happen
        logger.warning("Could not revoke the Google token: %s", type(exc).__name__)
    finally:
        with _token_lock:
            _forget_access_token()
            app_secrets.clear_google_refresh_token()
        _update_prefs(**{PREF_EMAIL: None, PREF_SOURCE: None})
        logger.info("Google account disconnected")
