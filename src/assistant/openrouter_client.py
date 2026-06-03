"""Minimal OpenRouter chat client for assistant answers."""
import logging
import os
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


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
            api_key: OpenRouter API key (defaults to OPENROUTER_API_KEY env var)
            model: Model ID to use (e.g., "google/gemini-2.5-flash")
            temperature: Sampling temperature (0.0-2.0), defaults to 0.7
            api_url: API endpoint URL (defaults to OpenRouter standard URL)
        """
        # Load API key from environment if not provided
        if api_key is None:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.getenv("OPENROUTER_API_KEY")
        
        if not api_key:
            raise MissingAPIKeyError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY environment variable "
                "or pass api_key parameter."
            )
        
        self.api_key = api_key
        self.model = model
        self.temperature = temperature if temperature is not None else self.DEFAULT_TEMPERATURE
        self.api_url = api_url or os.getenv("OPENROUTER_API_URL", self.DEFAULT_API_URL)
        
        # Lazy import for requests library
        self._requests = None
    
    @property
    def requests(self):
        """Lazy-load requests library."""
        if self._requests is None:
            try:
                import requests as req
                self._requests = req
            except ImportError:
                raise APIRequestError(
                    "requests library required for API calls. "
                    "Install with: pip install requests"
                )
        return self._requests
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
        """Send a chat completion request and return the assistant's response.
        
        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                     Should include system, user, and optionally assistant messages.
            model: Model ID (overrides instance default if provided)
            temperature: Sampling temperature (overrides instance default if provided)
            
        Returns:
            The assistant's response text only.
            
        Raises:
            MissingAPIKeyError: If API key is not configured.
            APIRequestError: If the HTTP request fails.
            InvalidResponseError: If the response is malformed or missing expected data.
        """
        if not messages:
            raise InvalidResponseError("Messages list cannot be empty")
        
        model = model or self.model
        if not model:
            raise InvalidResponseError("Model is required. Pass model parameter or set at initialization.")
        
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
        
        try:
            response = self.requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Validate response structure
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
            
        except self.requests.exceptions.Timeout:
            raise APIRequestError("API request timed out")
        except self.requests.exceptions.HTTPError as e:
            error_msg = f"HTTP {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg += f": {error_data.get('error', {}).get('message', '')}"
            except Exception:
                error_msg += f": {str(e)}"
            raise APIRequestError(error_msg)
        except self.requests.exceptions.RequestException as e:
            raise APIRequestError(f"API request failed: {str(e)}")
        except ValueError as e:
            raise InvalidResponseError(f"Failed to parse API response: {str(e)}")
