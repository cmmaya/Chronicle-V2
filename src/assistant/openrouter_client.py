"""Minimal OpenRouter chat client for assistant answers."""
import asyncio
import json
import logging
import os
import threading
from typing import Callable, List, Dict, Optional

from ..config import MODEL_PROVIDER_ROUTING, get_selected_model
from ..secrets import MISSING_KEY_MESSAGE, get_api_key, redact

logger = logging.getLogger(__name__)

# One pooled client for the whole process. A client per call paid a fresh TCP +
# TLS handshake on every answer; a shared one keeps the connection alive.
# httpx.Client is thread-safe, so the answer threads can all use it (an
# AsyncClient could not be shared: every thread runs its own event loop).
_shared_client = None
_shared_client_lock = threading.Lock()


def _get_shared_client():
    global _shared_client
    with _shared_client_lock:
        if _shared_client is None:
            import httpx
            _shared_client = httpx.Client(
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=4,
                                    keepalive_expiry=120.0),
            )
        return _shared_client


class OpenRouterClientError(Exception):
    """Base exception for OpenRouter client errors."""
    pass


class MissingAPIKeyError(OpenRouterClientError):
    """Raised when API key is missing."""
    pass


class APIRequestError(OpenRouterClientError):
    """Raised when API request fails."""
    pass


class InvalidResponseError(OpenRouterClientError):
    """Raised when API response is malformed."""
    pass


class OpenRouterClient:
    """Minimal OpenRouter chat client for assistant answers.
    
    This is a focused client for making chat completions, used only by
    the assistant answer functionality.
    """
    
    DEFAULT_API_URL = "https://openrouter.ai/api/v1/chat/completions"
    DEFAULT_TEMPERATURE = 0.7
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        api_url: Optional[str] = None
    ):
        """Initialize the OpenRouter client.
        
        Args:
            api_key: OpenRouter API key (defaults to secrets.get_api_key())
            model: Model ID to use (e.g., "google/gemini-2.5-flash")
            temperature: Sampling temperature (0.0-2.0), defaults to 0.7
            api_url: API endpoint URL (defaults to OpenRouter standard URL)
        """
        if api_key is None:
            api_key = get_api_key()

        if not api_key:
            raise MissingAPIKeyError(MISSING_KEY_MESSAGE)
        
        self.api_key = api_key
        self.model = model
        self.temperature = temperature if temperature is not None else self.DEFAULT_TEMPERATURE
        self.api_url = api_url or os.getenv("OPENROUTER_API_URL", self.DEFAULT_API_URL)

    
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """Send a chat completion request and return the assistant's response (synchronous)."""
        if not messages:
            raise InvalidResponseError("Messages list cannot be empty")
        
        model = model or self.model or get_selected_model()
        if not model:
            raise InvalidResponseError("Model is required. Pass model parameter, set at initialization, or ensure a model is selected in settings.")
        
        temperature = temperature if temperature is not None else self.temperature
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chronicle",
            "X-Title": "Chronicle Assistant"
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }
        if model in MODEL_PROVIDER_ROUTING:
            payload["provider"] = MODEL_PROVIDER_ROUTING[model]
        
        try:
            import httpx
            response = _get_shared_client().post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()

            data = response.json()
            
            if "choices" not in data:
                raise InvalidResponseError("Invalid API response: missing 'choices' field")
            
            if not data["choices"]:
                raise InvalidResponseError("Invalid API response: no choices returned")
            
            choice = data["choices"][0]
            if "message" not in choice:
                raise InvalidResponseError("Invalid API response: missing 'message' in choice")
            
            message = choice["message"]
            if "content" not in message:
                raise InvalidResponseError("Invalid API response: missing 'content' in message")
            
            return message["content"]
            
        except httpx.TimeoutException:
            raise APIRequestError("API request timed out")
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg += f": {error_data.get('error', {}).get('message', '')}"
            except Exception:
                error_msg += f": {redact(e, self.api_key)}"
            raise APIRequestError(error_msg)
        except httpx.RequestError as e:
            raise APIRequestError(f"API request failed: {redact(e, self.api_key)}")
        except ValueError as e:
            raise InvalidResponseError(f"Failed to parse API response: {str(e)}")

    async def chat_async(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """``chat`` off the event loop, on the shared pooled connection."""
        return await asyncio.to_thread(self.chat, messages, model, temperature)

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        on_delta: Optional[Callable[[str], None]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """Stream a chat completion; return the whole text.

        ``on_delta`` is called with each text fragment, on the calling thread,
        as it arrives - so the UI can show the answer while it is written.
        ``reasoning_effort`` ("low" etc.) asks reasoning models to think less;
        OpenRouter ignores it for models that do not reason.
        """
        if not messages:
            raise InvalidResponseError("Messages list cannot be empty")

        model = model or self.model or get_selected_model()
        if not model:
            raise InvalidResponseError("Model is required. Pass model parameter, set at initialization, or ensure a model is selected in settings.")

        temperature = temperature if temperature is not None else self.temperature
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chronicle",
            "X-Title": "Chronicle Assistant"
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if model in MODEL_PROVIDER_ROUTING:
            payload["provider"] = MODEL_PROVIDER_ROUTING[model]
        # Not for Claude: there the field turns extended thinking *on*.
        if reasoning_effort and not model.startswith("anthropic/"):
            payload["reasoning"] = {"effort": reasoning_effort}

        parts: List[str] = []
        try:
            import httpx
            with _get_shared_client().stream(
                "POST", self.api_url, headers=headers, json=payload, timeout=60
            ) as response:
                if response.status_code >= 400:
                    response.read()
                response.raise_for_status()
                for line in response.iter_lines():
                    # Blank lines and ": OPENROUTER PROCESSING" keep-alives.
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        message = chunk["error"].get("message", "") if isinstance(chunk["error"], dict) else str(chunk["error"])
                        raise APIRequestError(f"Stream error: {message}")
                    for choice in chunk.get("choices") or []:
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            parts.append(text)
                            if on_delta is not None:
                                try:
                                    on_delta(text)
                                except Exception as e:  # noqa: BLE001
                                    logger.debug("on_delta raised: %s", e)
        except httpx.TimeoutException:
            raise APIRequestError("API request timed out")
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg += f": {error_data.get('error', {}).get('message', '')}"
            except Exception:
                error_msg += f": {redact(e, self.api_key)}"
            raise APIRequestError(error_msg)
        except httpx.RequestError as e:
            raise APIRequestError(f"API request failed: {redact(e, self.api_key)}")

        text = "".join(parts)
        if not text:
            raise InvalidResponseError("Invalid API response: empty stream")
        return text

    async def chat_stream_async(self, *args, **kwargs) -> str:
        """``chat_stream`` off the event loop (``on_delta`` fires on the worker thread)."""
        return await asyncio.to_thread(self.chat_stream, *args, **kwargs)
