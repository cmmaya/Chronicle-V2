"""Screenshot context generator using AI to describe screenshots."""
import logging
import os
import base64
from typing import Optional

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
        "qwen/qwen3.5-flash-02-23",
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
    
    def _build_prompt(self, summary: Optional[str] = None, 
                      transcript_excerpt: Optional[str] = None) -> str:
        """Build the prompt for context generation.
        
        Args:
            summary: Session summary.
            transcript_excerpt: Transcript excerpt from around the screenshot time.
            
        Returns:
            Formatted prompt string.
        """
        prompt = """You are analyzing a screenshot captured during a recorded session.

You will receive:

- A screenshot image.
- A session summary.
- A transcript excerpt from approximately 40 seconds around the screenshot timestamp.

Your task is to create concise contextual metadata that will help future AI systems understand why this screenshot matters and retrieve it when answering questions about the session.

Instructions:

- Use the transcript as the primary source of truth.
- Use the session summary for broader context.
- Use the image to identify important visual information.
- Extract only meaningful visible text.
- Focus on what was happening at the moment the screenshot was taken.
- Do not speculate or invent details.
- Keep the summary concise but information-dense.
- Return ONLY valid JSON.
- Do not include markdown or code fences.

Return exactly this schema:

{
  "summary": "",
  "visible_text": [],
  "keywords": []
}

Field requirements:

summary:
A concise paragraph (50-150 words) describing:
- What is visible on screen.
- What was being discussed.
- Why the screenshot is relevant to the conversation.
- The most important entities, topics, or actions occurring at that moment.

visible_text:
Important text visible in the screenshot that may help future retrieval. Exclude insignificant UI elements.

keywords:
5-15 keywords or short phrases useful for semantic search. Include people, products, projects, topics, technologies, websites, documents, and concepts when relevant.

The JSON must always be valid and all fields must always be present."""
        
        if summary:
            prompt += f"\n\nSESSION SUMMARY:\n{summary}"
        
        if transcript_excerpt:
            prompt += f"\n\nTRANSCRIPT EXCERPT:\n{transcript_excerpt}"
        
        return prompt
    
    def _parse_response(self, response: str) -> dict:
        """Parse the JSON response from the model.
        
        Args:
            response: The raw response string from the model.
            
        Returns:
            Dictionary with the parsed fields.
        """
        import json
        import re
        
        # Try to extract JSON from the response
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # Try to find JSON array or object in the response
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                # Ensure all required fields exist
                return {
                    "summary": result.get("summary", ""),
                    "visible_text": result.get("visible_text", []),
                    "keywords": result.get("keywords", [])
                }
            except json.JSONDecodeError:
                pass
        
        # If JSON parsing fails, create a simple response
        return {
            "summary": response[:500] if response else "",
            "visible_text": [],
            "keywords": []
        }
    
    # Default vision model - hardcoded for screenshot context generation
    DEFAULT_VISION_MODEL = "qwen/qwen3.5-flash-02-23"
    FALLBACK_VISION_MODEL = "google/gemini-2.5-flash"
    
    def generate_context(
        self,
        screenshot_path: str,
        summary: Optional[str] = None,
        transcript_excerpt: Optional[str] = None,
        store: bool = True
    ) -> dict:
        """Generate AI context for a screenshot.
        
        Args:
            screenshot_path: Path to the screenshot image file.
            summary: Meeting summary.
            transcript_excerpt: Transcript excerpt from around the screenshot time.
            store: If True, store the context in the database.
            
        Returns:
            Dictionary with fields: image_description, discussion_context, 
            relationship_to_conversation, key_entities, confidence
            
        Raises:
            ModelDoesNotSupportImagesError: If no vision-capable model is available.
            FileNotFoundError: If the screenshot file doesn't exist.
            ScreenshotContextGeneratorError: If context generation fails.
        """
        # Try primary model first, then fallback
        model_id = self.DEFAULT_VISION_MODEL
        
        try:
            # Try primary model
            context = self._generate_with_model(
                screenshot_path, summary, transcript_excerpt, store, model_id
            )
            return context
        except Exception as primary_error:
            # Try fallback model
            try:
                model_id = self.FALLBACK_VISION_MODEL
                context = self._generate_with_model(
                    screenshot_path, summary, transcript_excerpt, store, model_id
                )
                return context
            except Exception:
                # Both models failed, raise the original error
                raise primary_error
    
    def _generate_with_model(
        self,
        screenshot_path: str,
        summary: Optional[str],
        transcript_excerpt: Optional[str],
        store: bool,
        model_id: str
    ) -> dict:
        """Generate context using a specific model."""
        try:
            # Encode the image
            image_data = self._encode_image(screenshot_path)
            
            # Build the prompt
            prompt = self._build_prompt(summary, transcript_excerpt)
            
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
            response = client.chat(messages=messages)
            
            # Parse the JSON response
            context = self._parse_response(response)
            
            # Store in database if requested and we have a screenshot ID
            if store:
                self._store_context(screenshot_path, context)
            
            return context
            
        except FileNotFoundError:
            raise
        except Exception as e:
            error_msg = str(e)
            # Check for specific OpenRouter errors
            if "404" in error_msg or "No endpoints found" in error_msg:
                raise ScreenshotContextGeneratorError(
                    f"The selected model '{model_id}' does not support image input on OpenRouter."
                ) from e
            logger.error(f"Failed to generate screenshot context: {e}")
            raise ScreenshotContextGeneratorError(f"Failed to generate context: {e}")
    
    def _store_context(self, screenshot_path: str, context: dict) -> None:
        """Store the generated context in the database.
        
        Args:
            screenshot_path: Path to the screenshot.
            context: Dictionary with fields: summary, visible_text, keywords
        """
        try:
            # Find the screenshot by path
            screenshot = self.database.get_screenshot_by_filepath(screenshot_path)
            
            if screenshot:
                screenshot_id = screenshot["id"]
                
                # Store the structured fields
                import json
                visible_text_json = json.dumps(context.get('visible_text', []))
                keywords_json = json.dumps(context.get('keywords', []))
                
                self.database.update_screenshot_ai_context(
                    screenshot_id,
                    context.get('summary', ''),
                    visible_text_json,
                    keywords_json
                )
                logger.info(f"Stored AI context for screenshot {screenshot_id}")
            else:
                logger.warning(f"Screenshot not found in database: {screenshot_path}")
        except Exception as e:
            logger.error(f"Failed to store screenshot context: {e}")
