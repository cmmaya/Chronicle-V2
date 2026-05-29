"""Summary generation using OpenRouter API."""
import logging
import json
import os
from typing import Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)


class SummaryError(Exception):
    """Exception raised for summary generation errors."""
    pass


class ModelType(Enum):
    """Available AI models for summarization."""
    MINIMAX_M2_1 = "minimax/minimax-m2.1"
    DEEPSEEK_V3 = "deepseek/deepseek-chat"
    DEEPSEEK_Coder = "deepseek/deepseek-coder"
    GEMINI_FLASH = "google/gemini-2.0-flash-001"
    GEMINI_2_5_FLASH = "google/gemini-2.5-flash"
    
    @property
    def display_name(self) -> str:
        """Human-readable model name."""
        names = {
            ModelType.MINIMAX_M2_1: "MiniMax M2.1",
            ModelType.DEEPSEEK_V3: "DeepSeek Chat",
            ModelType.DEEPSEEK_Coder: "DeepSeek Coder",
            ModelType.GEMINI_FLASH: "Gemini Flash 2.0",
            ModelType.GEMINI_2_5_FLASH: "Gemini 2.5 Flash",
        }
        return names.get(self, self.value)


class SummaryGenerator:
    """Generate meeting summaries using OpenRouter API.
    
    Supports multiple template types and AI models for flexible
    summary generation from meeting transcripts.
    """
    
    DEFAULT_MODEL = ModelType.GEMINI_2_5_FLASH
    FALLBACK_MODEL = ModelType.DEEPSEEK_V3
    DEFAULT_MAX_TOKENS = 2500
    DEFAULT_TEMPERATURE = 0.0
    DEFAULT_TOP_P = 0.2
    
    def __init__(self, 
                 api_key: str = None,
                 db=None,
                 model: ModelType = None,
                 max_tokens: int = None,
                 temperature: float = None,
                 top_p: float = None,
                 api_url: str = None,
                 custom_instructions: str = None):
        """Initialize summary generator.
        
        Args:
            api_key: OpenRouter API key (defaults to OPENROUTER_API_KEY env var)
            db: Database instance for storing summaries (optional)
            model: AI model to use (defaults to Gemini 2.5 Flash)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0-1.0)
            top_p: Nucleus sampling parameter (0.0-1.0)
            api_url: OpenRouter API URL (defaults to env var or standard URL)
            custom_instructions: Custom instructions to prepend to prompts
        """
        # Load API key from .env if not provided
        if api_key is None:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.getenv("OPENROUTER_API_KEY")
        
        self.api_key = api_key
        
        # Load API URL from .env or use default
        if api_url is None:
            api_url = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
        self.api_url = api_url
        
        self.db = db
        self.model = model or self.DEFAULT_MODEL
        self.max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS
        self.temperature = temperature if temperature is not None else self.DEFAULT_TEMPERATURE
        self.top_p = top_p if top_p is not None else self.DEFAULT_TOP_P
        
        # Load custom instructions from config if not provided
        if custom_instructions is None:
            custom_instructions = self._load_custom_instructions()
        self.custom_instructions = custom_instructions
        
        # Lazy import to handle missing requests library gracefully
        self._requests = None
    
    @property
    def requests(self):
        """Lazy-load requests library."""
        if self._requests is None:
            try:
                import requests as req
                self._requests = req
            except ImportError:
                raise SummaryError(
                    "requests library required for API calls. "
                    "Install with: pip install requests"
                )
        return self._requests
    
    def _load_custom_instructions(self) -> str:
        """Load custom instructions from config file.
        
        Returns:
            Custom instructions string, or empty string if not configured
        """
        try:
            from .. import config
            summarization_config = getattr(config, 'SUMMARIZATION', {})
            return summarization_config.get('custom_instructions', '')
        except ImportError:
            return ''
    
    def _call_api(self, messages: List[Dict[str, str]], use_fallback: bool = False) -> tuple:
        """Call OpenRouter API with messages.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            use_fallback: If True, use fallback model (DeepSeek) on failure
            
        Returns:
            Tuple of (API response text, model value used)
            
        Raises:
            SummaryError: If API call fails
        """
        # Determine which model to use
        model = self.FALLBACK_MODEL if use_fallback else self.model
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/chronicle",
            "X-Title": "Chronicle Meeting Summarizer"
        }
        
        payload = {
            "model": model.value,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature
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
            
            if "choices" not in data or not data["choices"]:
                raise SummaryError("Invalid API response: no choices returned")
            
            # Log which model was used
            logger.info(f"API call successful using {model.display_name}")
            return data["choices"][0]["message"]["content"], model.value
            
        except self.requests.exceptions.Timeout:
            if not use_fallback:
                logger.warning("Primary model timed out, trying fallback...")
                return self._call_api(messages, use_fallback=True)
            raise SummaryError("API request timed out")
        except self.requests.exceptions.HTTPError as e:
            error_msg = f"API HTTP error: {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_msg += f" - {error_data.get('error', {}).get('message', '')}"
            except Exception:
                pass
            
            # Try fallback on error (except for auth errors)
            if not use_fallback and e.response.status_code not in (401, 403):
                logger.warning(f"Primary model failed ({error_msg}), trying fallback...")
                return self._call_api(messages, use_fallback=True)
            
            raise SummaryError(error_msg)
        except self.requests.exceptions.RequestException as e:
            if not use_fallback:
                logger.warning(f"Primary model failed ({str(e)}), trying fallback...")
                return self._call_api(messages, use_fallback=True)
            raise SummaryError(f"API request failed: {str(e)}")
        except json.JSONDecodeError:
            raise SummaryError("Invalid JSON response from API")
    
    def generate(self, 
                 transcript: str,
                 template,
                 context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate summary from transcript using specified template.
        
        Args:
            transcript: Raw transcript text
            template: SummaryTemplate to use
            context: Optional context (session name, participants, etc.)
            
        Returns:
            Dictionary with summary results
            
        Raises:
            SummaryError: If generation fails
        """
        if not transcript or not transcript.strip():
            raise SummaryError("Transcript cannot be empty")
        
        if not self.api_key:
            raise SummaryError("API key required for summary generation")
        
        # Combine custom instructions with template system prompt
        system_prompt = template.system_prompt
        if self.custom_instructions:
            system_prompt = f"{self.custom_instructions}\n\n{system_prompt}"
        
        # Format messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": template.format_user_message(transcript, context)}
        ]
        
        try:
            result, model_used = self._call_api(messages)
            
            summary = {
                "content": result,
                "template_type": template.template_type.value,
                "model_used": model_used,
                "fields": template.fields
            }
            
            logger.info(f"Generated {template.template_type.value} summary using {model_used}")
            return summary
            
        except SummaryError:
            raise
        except Exception as e:
            raise SummaryError(f"Summary generation failed: {str(e)}")
    
    def generate_key_points(self, 
                            transcript: str,
                            context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate key points summary.
        
        Args:
            transcript: Raw transcript text
            context: Optional context
            
        Returns:
            Summary dictionary
        """
        from .templates import TemplateRegistry, TemplateType
        template = TemplateRegistry.get_template(TemplateType.KEY_POINTS)
        return self.generate(transcript, template, context)
    
    def generate_action_items(self, 
                               transcript: str,
                               context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate action items summary.
        
        Args:
            transcript: Raw transcript text
            context: Optional context
            
        Returns:
            Summary dictionary
        """
        from .templates import TemplateRegistry, TemplateType
        template = TemplateRegistry.get_template(TemplateType.ACTION_ITEMS)
        return self.generate(transcript, template, context)
    
    def generate_decisions(self, 
                          transcript: str,
                          context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate decisions summary.
        
        Args:
            transcript: Raw transcript text
            context: Optional context
            
        Returns:
            Summary dictionary
        """
        from .templates import TemplateRegistry, TemplateType
        template = TemplateRegistry.get_template(TemplateType.DECISIONS)
        return self.generate(transcript, template, context)
    
    def generate_full_summary(self, 
                              transcript: str,
                              context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate comprehensive full summary.
        
        Args:
            transcript: Raw transcript text
            context: Optional context
            
        Returns:
            Summary dictionary
        """
        from .templates import TemplateRegistry, TemplateType
        template = TemplateRegistry.get_template(TemplateType.FULL)
        return self.generate(transcript, template, context)
    
    def generate_all(self, 
                     transcript: str,
                     context: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        """Generate all types of summaries in a single API call.
        
        Args:
            transcript: Raw transcript text
            context: Optional context
            
        Returns:
            Dictionary with all summary types (single API call)
        """
        from .templates import TemplateRegistry, TemplateType
        
        # Use the general transcript template for single API call
        template = TemplateRegistry.get_template(TemplateType.GENERAL_TRANSCRIPT)
        
        try:
            result = self.generate(transcript, template, context)
            
            # Return the single result containing all fields
            return {
                'overview': {
                    'content': result['content'],
                    'template_type': result['template_type'],
                    'model_used': result['model_used'],
                    'fields': result['fields']
                },
                'key_points': {
                    'content': result['content'],
                    'template_type': result['template_type'],
                    'model_used': result['model_used'],
                    'fields': result['fields']
                },
                'action_items': {
                    'content': result['content'],
                    'template_type': result['template_type'],
                    'model_used': result['model_used'],
                    'fields': result['fields']
                },
                'decisions': {
                    'content': result['content'],
                    'template_type': result['template_type'],
                    'model_used': result['model_used'],
                    'fields': result['fields']
                },
                'open_questions': {
                    'content': result['content'],
                    'template_type': result['template_type'],
                    'model_used': result['model_used'],
                    'fields': result['fields']
                }
            }
            
        except SummaryError as e:
            logger.error(f"Summary generation failed: {e}")
            return {
                'overview': {"error": str(e)},
                'key_points': {"error": str(e)},
                'action_items': {"error": str(e)},
                'decisions': {"error": str(e)},
                'open_questions': {"error": str(e)}
            }
    
    def store_summary(self, 
                      session_id: int, 
                      summary_type: str, 
                      content: str,
                      model_used: str) -> Optional[int]:
        """Store summary in database.
        
        Args:
            session_id: Session ID
            summary_type: Type of summary (key_points, action_items, etc.)
            content: Summary content
            model_used: AI model used
            
        Returns:
            Summary ID if stored, None if no database
        """
        if not self.db:
            logger.warning("No database configured, summary not stored")
            return None
        
        try:
            summary_id = self.db.add_summary(
                session_id=session_id,
                summary_type=summary_type,
                content=content,
                model_used=model_used
            )
            logger.info(f"Stored {summary_type} summary for session {session_id}")
            return summary_id
        except Exception as e:
            logger.error(f"Failed to store summary: {e}")
            return None
    
    def generate_and_store(self, 
                          transcript: str,
                          session_id: int,
                          summary_type: str = "full",
                          context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate summary and store in database.
        
        Args:
            transcript: Raw transcript text
            session_id: Session ID for storage
            summary_type: Type of summary to generate
            context: Optional context
            
        Returns:
            Summary dictionary including stored ID
        """
        from .templates import TemplateRegistry, TemplateType
        
        type_map = {
            "key_points": TemplateType.KEY_POINTS,
            "action_items": TemplateType.ACTION_ITEMS,
            "decisions": TemplateType.DECISIONS,
            "full": TemplateType.FULL,
        }
        
        template_type = type_map.get(summary_type, TemplateType.FULL)
        template = TemplateRegistry.get_template(template_type)
        
        result = self.generate(transcript, template, context)
        
        # Store in database
        summary_id = self.store_summary(
            session_id=session_id,
            summary_type=summary_type,
            content=result["content"],
            model_used=result["model_used"]
        )
        
        result["session_id"] = session_id
        if summary_id:
            result["summary_id"] = summary_id
        
        return result
