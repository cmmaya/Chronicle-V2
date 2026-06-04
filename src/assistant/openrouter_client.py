"""Minimal OpenRouter chat client for assistant answers."""
import logging
import os
from typing import List, Dict, Optional

from ..config import get_selected_model

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
        
        try:
            import httpx
            with httpx.Client() as client:
                response = client.post(
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
                error_msg += f": {str(e)}"
            raise APIRequestError(error_msg)
        except httpx.RequestError as e:
            raise APIRequestError(f"API request failed: {str(e)}")
        except ValueError as e:
            raise InvalidResponseError(f"Failed to parse API response: {str(e)}")

    async def chat_async(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> str:
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
        
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
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
                error_msg += f": {str(e)}"
            raise APIRequestError(error_msg)
        except httpx.RequestError as e:
            raise APIRequestError(f"API request failed: {str(e)}")
        except ValueError as e:
            raise InvalidResponseError(f"Failed to parse API response: {str(e)}")

