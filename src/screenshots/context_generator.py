"""Screenshot context generator using AI to describe screenshots."""
import logging
import os
import base64
from typing import Optional

from ..config import get_selected_model
from ..storage.database import Database

logger = logging.getLogger(__name__)


class ScreenshotContextGeneratorError(Exception):
    """Base exception for screenshot context generation errors."""
    pass


class ModelDoesNotSupportImagesError(ScreenshotContextGeneratorError):
    """Raised when the selected model does not support image input."""
    pass


class ScreenshotContextGenerator:
    """Generate AI context for screenshots using the selected OpenRouter model.
    
    This service analyzes a screenshot and generates a concise text description
    that can be used as context for the assistant.
    """
    
    # Models known to support vision/image input in OpenRouter
    VISION_CAPABLE_MODELS = {
        "google/gemini-2.0-flash-exp",
        "google/gemini-2.0-flash",
        "google/gemini-2.5-flash",
        "google/gemini-2.5-flash-lite",
        "google/gemini-2.5-pro",
        "anthropic/claude-3.5-sonnet",
        "anthropic/claude-3.5-haiku",
        "anthropic/claude-opus-4.5",
        "openai/gpt-5",
        "openai/gpt-5-mini",
        "qwen/qwen3-14b",
        "qwen/qwen3-32b",
    }
    
    # Fallback: check if model name contains certain patterns
    VISION_PATTERNS = ["gemini", "claude", "gpt-5", "qwen3", "vision"]
    
    def __init__(self, database: Optional[Database] = None):
        """Initialize the screenshot context generator.
        
        Args:
            database: Database instance for storing generated context.
                     If not provided, a new instance will be created.
        """
        self._database = database
    
    @property
    def database(self) -> Database:
        """Lazy-load database instance."""
        if self._database is None:
            self._database = Database()
        return self._database
    
    def _model_supports_vision(self, model_id: str) -> bool:
        """Check if the given model supports image input.
        
        Args:
            model_id: The model identifier to check.
            
        Returns:
            True if the model supports vision, False otherwise.
        """
        # Check exact matches first
        if model_id in self.VISION_CAPABLE_MODELS:
            return True
        
        # Check patterns in model ID
        model_lower = model_id.lower()
        for pattern in self.VISION_PATTERNS:
            if pattern in model_lower:
                return True
        
        return False
    
    def _encode_image(self, image_path: str) -> str:
        """Encode an image file as base64.
        
        Args:
            image_path: Path to the image file.
            
        Returns:
            Base64-encoded image data URI.
            
        Raises:
            FileNotFoundError: If the image file doesn't exist.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Screenshot file not found: {image_path}")
        
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        
        # Determine mime type from extension
        ext = os.path.splitext(image_path)[1].lower()
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        mime_type = mime_types.get(ext, "image/png")
        
        return f"data:{mime_type};base64,{image_data}"
    
    def _build_prompt(self, session_metadata: Optional[str] = None, 
                      transcript_excerpt: Optional[str] = None) -> str:
        """Build the prompt for context generation.
        
        Args:
            session_metadata: Optional session metadata (e.g., session name, date).
            transcript_excerpt: Optional transcript excerpt near the screenshot.
            
        Returns:
            Formatted prompt string.
        """
        prompt = "Describe this screenshot concisely in 1-3 sentences. "
        prompt += "Focus on the main content, UI elements, and any text visible."
        
        if session_metadata:
            prompt += f"\n\nSession context: {session_metadata}"
        
        if transcript_excerpt:
            prompt += f"\n\nNearby transcript: {transcript_excerpt}"
        
        return prompt
    
    def generate_context(
        self,
        screenshot_path: str,
        session_metadata: Optional[str] = None,
        transcript_excerpt: Optional[str] = None,
        store: bool = True
    ) -> str:
        """Generate AI context for a screenshot.
        
        Args:
            screenshot_path: Path to the screenshot image file.
            session_metadata: Optional session metadata (e.g., session name).
            transcript_excerpt: Optional transcript excerpt near the screenshot.
            store: If True, store the generated context in the database.
            
        Returns:
            The generated context string.
            
        Raises:
            ModelDoesNotSupportImagesError: If the selected model doesn't support images.
            FileNotFoundError: If the screenshot file doesn't exist.
            ScreenshotContextGeneratorError: If context generation fails.
        """
        model_id = get_selected_model()
        
        if not self._model_supports_vision(model_id):
            raise ModelDoesNotSupportImagesError(
                f"The selected model '{model_id}' does not support image input. "
                f"Please select a vision-capable model in Settings > Assistant Model. "
                f"Supported models include: google/gemini-2.5-flash, anthropic/claude-3.5-haiku, etc."
            )
        
        try:
            # Encode the image
            image_data = self._encode_image(screenshot_path)
            
            # Build the prompt
            prompt = self._build_prompt(session_metadata, transcript_excerpt)
            
            # Create messages with image
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data}}
                    ]
                }
            ]
            
            # Use the existing OpenRouter client
            from ..assistant.openrouter_client import OpenRouterClient
            
            client = OpenRouterClient(model=model_id)
            context = client.chat(messages=messages)
            
            # Store in database if requested and we have a screenshot ID
            if store:
                self._store_context(screenshot_path, context)
            
            return context
            
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to generate screenshot context: {e}")
            raise ScreenshotContextGeneratorError(f"Failed to generate context: {e}")
    
    def _store_context(self, screenshot_path: str, context: str) -> None:
        """Store the generated context in the database.
        
        Args:
            screenshot_path: Path to the screenshot.
            context: The generated context string.
        """
        try:
            # Find the screenshot by path
            screenshot = self.database.get_screenshot_by_filepath(screenshot_path)
            
            if screenshot:
                screenshot_id = screenshot["id"]
                self.database.update_screenshot_context(screenshot_id, context)
                logger.info(f"Stored context for screenshot {screenshot_id}")
            else:
                logger.warning(f"Screenshot not found in database: {screenshot_path}")
        except Exception as e:
            logger.error(f"Failed to store screenshot context: {e}")
