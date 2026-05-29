"""Configuration settings for Chronicle application."""

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
