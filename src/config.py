"""Configuration settings for Chronicle application."""

# Assistant Agent Settings
ASSISTANT_AGENTS = {
    # Default agent ID
    "default": "chronicle_assistant",
    
    # Available agents: id -> {label, model, system_instruction}
    "agents": {
        "chronicle_assistant": {
            "label": "Chronicle Assistant",
            "model": "google/gemini-2.5-flash",
            "system_instruction": (
                "You are a helpful meeting assistant. Answer questions based only on the provided meeting transcript. "
                "If the transcript does not contain enough information to answer a question, say so clearly and "
                "suggest what additional information would be needed. Stay focused on the meeting content."
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
    "auto_summary_after_stop": False,
}
