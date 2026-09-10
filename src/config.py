"""Configuration settings for Chronicle application."""

# Assistant Agent Settings
ASSISTANT_AGENTS = {
    # Default agent ID
    "default": "research_helper",
    
    # Available agents: id -> {label, model, system_instruction}
    "agents": {
        "chronicle_assistant": {
            "label": "Chronicle Assistant",
            "model": "google/gemini-2.5-flash",
            "system_instruction": (
                "You are a helpful meeting assistant. Answer questions based only on the provided Chronicle evidence, "
                "which may include transcripts, summaries, screenshots, and assistant conversation history. "
                "If the evidence does not contain enough information to answer a question, state clearly that the evidence "
                "is insufficient and suggest what additional information would be needed. When referencing evidence, "
                "include the session name and timestamp (HH:MM:SS) when available. Stay focused on the meeting content."
            ),
        },
        "concise_helper": {
            "label": "Concise Helper",
            "model": "google/gemini-2.5-flash",
            "system_instruction": (
                "You are a brief and direct meeting assistant. Provide short, accurate answers based only on "
                "the transcript. If unclear, ask a clarifying question. Prioritize key facts over elaboration."
            ),
        },
        "research_helper": {
            "label": "Research Helper",
            "model": "google/gemini-2.5-flash",
            "system_instruction": (
                "You are a research assistant. If the question is about meeting transcripts, summaries, "
                "or session content, answer based on the provided context. If the question is about general "
                "knowledge or not related to the meeting transcripts, answer using your own knowledge base. "
                "Clearly indicate when you're using general knowledge vs transcript content."
            ),
        },
    },
}


# Summarization Settings
SUMMARIZATION = {
    # Model configuration
    "model": "google/gemini-2.5-flash",
    
    # Custom instructions for the summarization AI
    # These will be prepended to the system prompt for all summaries
    "custom_instructions": (
        "You are a meeting assistant that creates structured summaries from meeting transcripts. "
        "Focus on accuracy and clarity. Use only information explicitly present in the transcript."
    ),
    
    # API settings
    "max_tokens": 2500,
    "temperature": 0.0,
    "top_p": 0.2,
}


# Session Settings
SESSION = {
    # Auto-generate summary after session stops
    # When True, a summary will be generated automatically when the session is stopped
    "auto_summary_after_stop": True,
}


# Audio Capture Resilience Settings
# These govern how the system-audio (loopback) recorder recovers from a broken
# capture stream - the classic failure being a default-output-device change when
# a Zoom/Meet/Teams call ends, which silently kills the WASAPI loopback stream.
AUDIO_CAPTURE = {
    # Supervisor (fix 1): rebuild the loopback stream instead of dying.
    # Consecutive failed record() calls before the stream is torn down and rebuilt.
    "loopback_max_consecutive_errors": 5,
    # Exponential backoff between rebuild attempts (seconds).
    "loopback_backoff_initial": 1.0,
    "loopback_backoff_max": 30.0,
    # A record() call that returns no frames for this long means the stream is
    # stale even though it never raised - force a rebuild.
    "loopback_silent_stall_seconds": 20.0,

    # Watchdog (fix 2): an external thread that restarts a wedged/dead recorder.
    "watchdog_enabled": True,
    "watchdog_interval_seconds": 15.0,
    # System recorder is considered stalled if no raw frames have arrived for this
    # long (loopback delivers zero-frames continuously even during silence, so any
    # real gap is a fault).
    "watchdog_system_stall_seconds": 40.0,
    # Safety rails so a permanently broken device can't restart-loop forever.
    "watchdog_max_restarts": 30,
    "watchdog_restart_cooldown_seconds": 20.0,
}


# Model Selection Settings
ALLOWED_MODELS = [
    # OpenAI
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "openai/gpt-5-mini",
    "openai/gpt-5",
    "openai/o3",
    # Anthropic
    "anthropic/claude-3.5-haiku",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-opus-4.5",
    # Google
    "google/gemma-3-12b-it",
    "google/gemma-3-27b-it",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
    # DeepSeek
    "deepseek/deepseek-chat-v3.2",
    "deepseek/deepseek-v3.1-terminus",
    "deepseek/deepseek-v3.2-exp",
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v3.2-speciale",
    # Qwen
    "qwen/qwen3-14b",
    "qwen/qwen3-32b",
    "qwen/qwen3.5-27b",
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3.5-397b-a17b",
    # Moonshot AI
    "moonshotai/kimi-k2",
    "moonshotai/kimi-k2-thinking",
    "moonshotai/kimi-k2.5",
    # Z.ai (GLM)
    "z-ai/glm-4.5-air",
    "z-ai/glm-4.5",
    "z-ai/glm-5.1",
    # Mistral AI
    "mistralai/ministral-8b",
    "mistralai/mistral-small-3.2",
    "mistralai/magistral-medium",
    "mistralai/mistral-large",
]

# Sampling temperature for the Any Session answer call (BU090). Lower than the
# client default (0.7) so the machine-parsed metadata trailer is emitted
# reliably. Tune here rather than in code; do not set to 0.0 (some models in
# ALLOWED_MODELS degenerate into repetition). Drop to 0.1 if the trailer is
# missing or malformed in more than ~5% of responses across the models in use.
ANY_SESSION_TEMPERATURE = 0.2

# (Unused since BU093.) Previously the minimum score gap between the top routed
# session and the runner-up before an Any Session answer offered a direct scope
# switch. BU093 always offers the best single guess and adds a "choose another
# session" path, so there is no ambiguity threshold to tune. Kept for reference.
SCOPE_OFFER_MARGIN = 0.15

DEFAULT_MODEL = "deepseek/deepseek-v3.2"

# Internal storage for selected model
_selected_model = DEFAULT_MODEL


def get_selected_model() -> str:
    """Get the currently selected model ID.
    
    Returns:
        The currently selected model ID (e.g., 'google/gemini-2.5-flash').
    """
    return _selected_model


def set_selected_model(model_id: str) -> bool:
    """Set the selected model ID.
    
    Args:
        model_id: The model ID to select (must be in ALLOWED_MODELS).
        
    Returns:
        True if the model was set successfully, False if invalid model ID.
    """
    global _selected_model
    if model_id in ALLOWED_MODELS:
        _selected_model = model_id
        return True
    return False
