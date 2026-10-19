"""The OpenRouter API key: one lookup for every caller (BU122).

Lookup order: the ``OPENROUTER_API_KEY`` environment variable (or ``.env``,
for development and CI), then the Windows Credential Manager through
``keyring`` (service ``Chronicle``, user ``openrouter``). The app only ever
writes the key to the credential store, never to a plain file, and never logs
it.

The Google Calendar refresh token (BU130) follows the same rule, under user
``google_calendar_refresh_token``.

Note: this module is ``src.secrets``. Never put ``src/`` itself on
``sys.path``, or it would shadow the standard library ``secrets`` module.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)

ENV_VAR = "OPENROUTER_API_KEY"
KEYRING_SERVICE = "Chronicle"
KEYRING_USER = "openrouter"
GOOGLE_REFRESH_TOKEN_USER = "google_calendar_refresh_token"
KEY_INFO_URL = "https://openrouter.ai/api/v1/key"
CHECK_TIMEOUT_SECONDS = 10

MISSING_KEY_MESSAGE = (
    "No OpenRouter API key is set. Add it in Settings > API Key."
)


def _from_environment() -> Optional[str]:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    return (os.getenv(ENV_VAR) or "").strip() or None


def _from_keyring() -> Optional[str]:
    try:
        import keyring
        return (keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or "").strip() or None
    except Exception as exc:  # noqa: BLE001 - no usable backend is not fatal
        logger.warning("Could not read the API key from the credential store: %s", type(exc).__name__)
        return None


def get_api_key() -> Optional[str]:
    """The key from the environment / ``.env`` first, else the credential store."""
    return _from_environment() or _from_keyring()


def key_source() -> Optional[str]:
    """Where :func:`get_api_key` finds the key: ``"environment"``, ``"keyring"`` or None."""
    if _from_environment():
        return "environment"
    if _from_keyring():
        return "keyring"
    return None


def has_saved_key() -> bool:
    """True when a key is stored in the credential store."""
    return _from_keyring() is not None


def set_api_key(key: str) -> None:
    """Save ``key`` in the Windows Credential Manager."""
    key = (key or "").strip()
    if not key:
        raise ValueError("The API key is empty.")
    import keyring
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
    logger.info("OpenRouter API key saved to the credential store")


def clear_api_key() -> None:
    """Remove the key from the credential store (a missing key is not an error)."""
    import keyring
    from keyring.errors import PasswordDeleteError
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:
        return
    logger.info("OpenRouter API key removed from the credential store")


def get_google_refresh_token() -> Optional[str]:
    """The stored Google refresh token, or None (BU130)."""
    try:
        import keyring
        token = (keyring.get_password(KEYRING_SERVICE, GOOGLE_REFRESH_TOKEN_USER) or "").strip() or None
    except Exception as exc:  # noqa: BLE001 - no usable backend is not fatal
        logger.warning("Could not read the Google token from the credential store: %s", type(exc).__name__)
        return None
    register_secret(token)
    return token


def set_google_refresh_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("The refresh token is empty.")
    import keyring
    keyring.set_password(KEYRING_SERVICE, GOOGLE_REFRESH_TOKEN_USER, token)
    register_secret(token)
    logger.info("Google refresh token saved to the credential store")


def clear_google_refresh_token() -> None:
    """Remove the refresh token (a missing token is not an error)."""
    import keyring
    from keyring.errors import PasswordDeleteError
    try:
        keyring.delete_password(KEYRING_SERVICE, GOOGLE_REFRESH_TOKEN_USER)
    except PasswordDeleteError:
        return
    logger.info("Google refresh token removed from the credential store")


# Tokens and client secrets seen in this process, masked by redact().
_known_secrets: Set[str] = set()


def register_secret(value: Optional[str]) -> None:
    """Have :func:`redact` mask ``value`` from now on."""
    if value:
        _known_secrets.add(value)


def mask(key: Optional[str]) -> str:
    """Show only the last 4 characters, e.g. ``••••••••a1b2``."""
    if not key:
        return ""
    return "•" * 8 + key[-4:]


def redact(text: str, key: Optional[str] = None) -> str:
    """``text`` with the API key (the given one, else the current one) and any
    registered Google token or client secret removed."""
    text = str(text)
    for secret in {key, get_api_key(), *_known_secrets}:
        if secret and secret in text:
            text = text.replace(secret, mask(secret))
    return text


def check_api_key(key: str) -> Tuple[bool, str]:
    """One authenticated call to OpenRouter's key-info endpoint (spends no tokens).

    Returns ``(ok, message)`` with a message meant for the user.
    """
    key = (key or "").strip()
    if not key:
        return False, "Enter an API key first."
    import httpx
    try:
        response = httpx.get(
            KEY_INFO_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=CHECK_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return False, "OpenRouter did not answer in time. Check your internet connection and try again."
    except httpx.RequestError:
        return False, "Could not reach OpenRouter. Check your internet connection and try again."

    status = response.status_code
    if status == 200:
        return True, "The API key works."
    if status == 401:
        return False, "OpenRouter rejected this API key. Check that it was copied completely."
    if status == 402:
        return False, "The API key is valid but the account has no credits left."
    return False, f"OpenRouter returned an unexpected response (HTTP {status}). Try again later."
