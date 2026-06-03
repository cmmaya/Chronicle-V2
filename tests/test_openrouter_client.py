"""Tests for OpenRouterClient."""
import pytest
import json
import os
import sys
from unittest.mock import Mock, patch, MagicMock

# Clear any cached imports before tests
for mod in list(sys.modules.keys()):
    if 'src.assistant' in mod:
        del sys.modules[mod]


class TestOpenRouterClient:
    """Test suite for OpenRouterClient."""
    
    def test_missing_api_key_raises_error(self):
        """Test that missing API key raises MissingAPIKeyError."""
        # Clear the environment variable if it exists
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict('sys.modules', {'dotenv': MagicMock()}):
                # Force reload to avoid cached imports
                if 'src.assistant.openrouter_client' in sys.modules:
                    del sys.modules['src.assistant.openrouter_client']
                
                from src.assistant.openrouter_client import OpenRouterClient, MissingAPIKeyError
                
                with pytest.raises(MissingAPIKeyError) as exc_info:
                    OpenRouterClient(api_key=None)
                
                assert "API key required" in str(exc_info.value)
    
    def test_missing_model_raises_error(self):
        """Test that missing model raises InvalidResponseError."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch.dict('sys.modules', {'dotenv': MagicMock()}):
                if 'src.assistant.openrouter_client' in sys.modules:
                    del sys.modules['src.assistant.openrouter_client']
                
                from src.assistant.openrouter_client import OpenRouterClient, InvalidResponseError
                
                client = OpenRouterClient(api_key="test-key")
                
                with pytest.raises(InvalidResponseError) as exc_info:
                    client.chat(messages=[{"role": "user", "content": "hello"}])
                
                assert "Model is required" in str(exc_info.value)
    
    def test_empty_messages_raises_error(self):
        """Test that empty messages list raises error."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch.dict('sys.modules', {'dotenv': MagicMock()}):
                if 'src.assistant.openrouter_client' in sys.modules:
                    del sys.modules['src.assistant.openrouter_client']
                
                from src.assistant.openrouter_client import OpenRouterClient, InvalidResponseError
                
                client = OpenRouterClient(api_key="test-key", model="test-model")
                
                with pytest.raises(InvalidResponseError) as exc_info:
                    client.chat(messages=[])
                
                assert "Messages list cannot be empty" in str(exc_info.value)
    
    def test_successful_response_parsing(self):
        """Test response parsing with mocked successful OpenRouter payload."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch.dict('sys.modules', {'dotenv': MagicMock()}):
                if 'src.assistant.openrouter_client' in sys.modules:
                    del sys.modules['src.assistant.openrouter_client']
                
                from src.assistant.openrouter_client import OpenRouterClient
                
                # Create mock requests
                mock_requests = MagicMock()
                
                # Mock successful response
                mock_response = Mock()
                mock_response.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": "Hello! How can I help you today?"
                            }
                        }
                    ]
                }
                mock_response.raise_for_status = Mock()
                mock_requests.post.return_value = mock_response
                
                client = OpenRouterClient(api_key="test-key", model="google/gemini-2.5-flash")
                client._requests = mock_requests
                
                messages = [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hi"}
                ]
                
                result = client.chat(messages)
                
                assert result == "Hello! How can I help you today?"
                mock_requests.post.assert_called_once()
    
    def test_missing_choices_raises_error(self):
        """Test malformed response (missing choices) raises InvalidResponseError."""
        # Use a real requests library for proper exception handling
        import requests
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch.dict('sys.modules', {'dotenv': MagicMock()}):
                if 'src.assistant.openrouter_client' in sys.modules:
                    del sys.modules['src.assistant.openrouter_client']
                
                from src.assistant.openrouter_client import OpenRouterClient, InvalidResponseError
                
                # Create mock requests using real exception classes
                mock_requests = MagicMock()
                mock_requests.exceptions = requests.exceptions
                
                # Mock response with missing choices
                mock_response = Mock()
                mock_response.json.return_value = {"error": "some error"}
                mock_response.raise_for_status = Mock()
                mock_requests.post.return_value = mock_response
                
                client = OpenRouterClient(api_key="test-key", model="test-model")
                client._requests = mock_requests
                
                with pytest.raises(InvalidResponseError) as exc_info:
                    client.chat(messages=[{"role": "user", "content": "test"}])
                
                assert "choices" in str(exc_info.value)
    
    def test_http_error_raises_api_error(self):
        """Test HTTP error raises APIRequestError."""
        import requests
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with patch.dict('sys.modules', {'dotenv': MagicMock()}):
                if 'src.assistant.openrouter_client' in sys.modules:
                    del sys.modules['src.assistant.openrouter_client']
                
                from src.assistant.openrouter_client import OpenRouterClient, APIRequestError
                
                # Create mock requests using real exception classes
                mock_requests = MagicMock()
                mock_requests.exceptions = requests.exceptions
                
                # Mock HTTP error response
                mock_response = Mock()
                mock_response.status_code = 401
                mock_response.json.return_value = {"error": {"message": "Invalid API key"}}
                
                http_error = requests.exceptions.HTTPError(response=mock_response)
                mock_requests.post.side_effect = http_error
                
                client = OpenRouterClient(api_key="test-key", model="test-model")
                client._requests = mock_requests
                
                with pytest.raises(APIRequestError) as exc_info:
                    client.chat(messages=[{"role": "user", "content": "test"}])
                
                assert "401" in str(exc_info.value)


class TestClientCanBeImported:
    """Verify client can be imported without side effects."""
    
    def test_import_openrouter_client(self):
        """Test that the module can be imported."""
        from src.assistant import openrouter_client
        assert openrouter_client is not None
    
    def test_import_exceptions(self):
        """Test that exceptions can be imported."""
        from src.assistant import (
            OpenRouterClient,
            OpenRouterClientError,
            MissingAPIKeyError,
            APIRequestError,
            InvalidResponseError,
        )
        assert OpenRouterClient is not None
        assert issubclass(OpenRouterClientError, Exception)
        assert issubclass(MissingAPIKeyError, OpenRouterClientError)
        assert issubclass(APIRequestError, OpenRouterClientError)
        assert issubclass(InvalidResponseError, OpenRouterClientError)
