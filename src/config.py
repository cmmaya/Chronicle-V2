"""Configuration settings for Chronicle application."""

# The one version number (BU126): the .exe version resource, the installer
# name, the setup wizard and Settings > About all read it from here.
APP_VERSION = "1.0.0"

# Transcripts are raw speech-to-text. Without this, models read them literally
# and report "not specified" for anything phrased awkwardly. Shared by every
# prompt that answers from a transcript.
_ASR_NOTE = (
    "The transcript is automatic speech-to-text: expect cut-off sentences, "
    "missing or misheard words, and loose spoken phrasing. Match on meaning, "
    "not exact wording, and read neighbouring lines together - one point is "
    "often spread across several."
)

# What counts as finding the answer, so "not in the transcript" is only said
# after a real search.
_SEARCH_RULE = (
    "The context answers the question if it states the answer, paraphrases "
    "it, or mentions it in a partial or garbled form - look for all three "
    "before deciding it does not."
)

# Evidence rules for the evidence-only agents. Chronicle Assistant and Concise
# Helper differ only in the answer-style line appended to this.
_EVIDENCE_ONLY_RULES = (
    "You are a meeting assistant. Answer only from the session context you "
    "are given: transcripts, summaries, screenshots and earlier conversation. "
    + _ASR_NOTE + "\n\n"
    "- " + _SEARCH_RULE + "\n"
    "- When an answer rests on a partial or garbled passage, say it is "
    "inferred and quote the passage.\n"
    "- Cite the session name and timestamp (HH:MM:SS) when available.\n"
    "- If the context still does not answer the question, say so and name "
    "the information that is missing. If the question itself is ambiguous, "
    "ask one clarifying question instead of guessing.\n\n"
)

# Assistant Agent Settings
ASSISTANT_AGENTS = {
    # Default agent ID
    "default": "research_helper",
    
    # Available agents: id -> {label, model, system_instruction}
    "agents": {
        "chronicle_assistant": {
            "label": "Chronicle Assistant",
            "model": "google/gemini-3.8-flash",
            "system_instruction": _EVIDENCE_ONLY_RULES + (
                "Answer style: complete. Full sentences, with the supporting "
                "details and a citation for each point."
            ),
        },
        "concise_helper": {
            "label": "Concise Helper",
            "model": "google/gemini-3.8-flash",
            "system_instruction": _EVIDENCE_ONLY_RULES + (
                "Answer style: brief and direct. The key facts in as few "
                "words as possible, no elaboration."
            ),
        },
        "research_helper": {
            "label": "Research Helper",
            "model": "google/gemini-3.8-flash",
            "system_instruction": (
                "You are a research assistant for recorded meetings and lectures. You may be given "
                "session context: transcripts, summaries and screenshots. " + _ASR_NOTE + "\n\n"
                "1. Search the context first. " + _SEARCH_RULE + " Cite the session name and "
                "timestamp (HH:MM:SS) when available.\n"
                "2. Answer from your own knowledge only when that search finds nothing, when no "
                "context was given, or when the question is plainly not about the session.\n"
                "3. Label each part of the answer with its source: \"From the transcript\", "
                "\"Inferred from the transcript\" (it rests on a partial or garbled passage - quote "
                "it), or \"General knowledge\"."
            ),
        },
    },
}


# Summarization Settings
SUMMARIZATION = {
    # Model configuration
    "model": "google/gemini-3.8-flash",
    
    # Custom instructions for the summarization AI
    # These will be prepended to the system prompt for all summaries
    "custom_instructions": (
        "You are a meeting assistant that creates structured summaries from meeting transcripts. "
        "Focus on accuracy and clarity. Use only information explicitly present in the transcript."
    ),
    
    # API settings
    "max_tokens": 8000,
    "temperature": 0.0,
    "top_p": 0.2,
}


# Session Settings
SESSION = {
    # Auto-generate summary after session stops
    # When True, a summary will be generated automatically when the session is stopped
    "auto_summary_after_stop": True,
    # Specific Session questions send the session's whole transcript (BU144)
    # when it is at most this many characters (about 4-5 hours of speech);
    # longer sessions are searched. 0 always searches.
    "full_transcript_max_chars": 300000,
}

# Documents a user adds to a session (BU146): extracted text kept in the
# database, used as extra context for that session's Specific Session answers.
SESSION_DOCUMENTS = {
    "max_per_session": 5,
    "max_file_bytes": 2 * 1024 * 1024,
    "allowed_extensions": [".txt", ".md", ".pdf", ".docx"],
    # Extracted text outside these bounds is refused (BU147): below the
    # minimum a scanned PDF "worked" but holds nothing to answer from; above
    # the maximum it would be cut silently.
    "min_text_chars": 40,
    "max_text_chars": 400000,
    # Specific Session prompts (BU149): documents that together fit this are
    # sent whole, in the cached part of the prompt; otherwise each document
    # contributes a per-question excerpt of at most excerpt_chars_per_document.
    "whole_total_max_chars": 60000,
    "excerpt_chars_per_document": 4000,
}


# Screenshot capture settings (BU110)
SCREENSHOT = {
    # System-wide capture shortcut, active only while a session is recording
    # or paused. Win+Shift+S can't be used: Windows reserves it for the
    # Snipping Tool (see import_clipboard_snips instead).
    "global_hotkey": "Ctrl+Alt+S",
    # While a session is live, save any image placed on the clipboard (such as
    # a Win+Shift+S snip) as a session screenshot. Off by default because it
    # also catches every other image you copy.
    "import_clipboard_snips": False,
}


# Live Q&A question detection (BU114)
#
# Model choice here was measured, not guessed: session 46 ("Class 1 - RM P2",
# 188 chunks, 2h08m) was replayed against 21 hand-labelled question-like
# utterances. Accuracy did not track price.
#
#   model                       | rhetorical called genuine | wrongly answerable
#   google/gemini-3.8-flash     | 0/6                       | 0/11
#   qwen/qwen3-14b              | 3/5                       | 2/13
#   google/gemini-2.5-flash     | 6/7                       | 5/28
#
# gemini-3.8-flash missed none of the 21 and produced no false "answerable"
# calls, so it is the default. qwen3-14b is the cheap option. 2.5-flash is
# offered because the user asked for it and is documented as the weak middle;
# gemma-3-12b-it and 2.5-flash-lite measured badly enough not to offer at all.
#
# The detector model is independent of the answering model: answers always go
# through the chat agent and get_selected_model().
LIVE_QA = {
    "detector_model": "google/gemini-3.8-flash",

    # Model for live answers in the Transcripts window, separate from the chat
    # panel's selected model so a heavier chat model does not slow them down.
    # Empty falls back to get_selected_model(). Answers are streamed, and
    # answer_reasoning_effort asks reasoning models to think less (ignored by
    # models that do not reason); empty leaves the model's default.
    "answer_model": "anthropic/claude-haiku-5.5",
    # Offered in the transcript window's settings menu. An empty id means the
    # chat panel's selected model.
    "answer_models": [
        {
            "id": "anthropic/claude-haiku-5.5",
            "label": "Claude Haiku 5.5",
            "note": "Fast, no hidden reasoning",
        },
        {
            "id": "google/gemini-3.8-flash",
            "label": "Gemini 3.8 Flash",
            "note": "Reasons before answering - slower",
        },
        {
            "id": "",
            "label": "Chat model",
            "note": "Whatever the chat panel uses",
        },
    ],
    "answer_reasoning_effort": "low",
    # Backstop against a runaway answer; the prompts already ask for two
    # sentences. Kept generous: a tight cap cuts answers off mid-sentence and
    # eats a reasoning model's thinking budget. 0 sends no cap.
    "answer_max_tokens": 500,

    # Offered in the BU115 picker, each with the note shown at the point of
    # choice so the tradeoff is visible where it is made.
    "detector_models": [
        {
            "id": "google/gemini-3.8-flash",
            "label": "Gemini 3.8 Flash",
            "note": "Most accurate - $0.19/session",
        },
        {
            "id": "qwen/qwen3-14b",
            "label": "Qwen3 14B",
            "note": "Cheapest usable - $0.03/session, some false positives",
        },
        {
            "id": "google/gemini-2.5-flash",
            "label": "Gemini 2.5 Flash",
            "note": "Weak - $0.04/session, marks rhetorical as genuine",
        },
        {
            "id": "anthropic/claude-haiku-5.5",
            "label": "Claude Haiku 5.5",
            "note": "Fast - not yet replayed on session 46, accuracy unmeasured",
        },
    ],

    # Transcript chunks of context the detector sees (1-5). A question often
    # starts in one chunk and finishes in the next, so 1 under-detects.
    "window_chunks": 3,
    "window_chunks_range": (1, 5),

    # The spend cap. Session event rates in the database range from 1.5 to
    # 15.4 chunks/min, so per-chunk detection would cost a busy meeting ~9x a
    # lecture. One call per interval makes spend a function of wall-clock
    # instead, and structurally cuts the duplicate firings that overlapping
    # windows produce.
    "min_detect_interval_seconds": 15,

    "min_confidence": 0.8,
    "max_detections_per_session": 40,

    # How much of the transcript leading up to a question a live answer is
    # given as evidence. During a live session the search index is not built
    # yet (it is built at Stop), and keyword search cannot get from "which
    # instrument do I own" to "I have a ukulele", so this block is where the
    # answer is found. ~8000 chars is roughly the last 9 minutes of speech,
    # about 2k tokens per answer.
    "answer_context_chars": 8000,

    # BU116: how a live answer is written. A separate axis from the detection
    # modes, which decide *whether* a question is answered; these decide *how*.
    # The wording lives here so it can be tuned without touching code.
    #
    #   transcripts - check the session evidence first, and label a
    #                 general-knowledge fallback when it does not answer
    #   general     - answer directly, no evidence check
    "answer_mode": "transcripts",
    "answer_modes": [
        {
            "id": "transcripts",
            "label": "Transcripts",
            "note": "Answer from the session first; a general-knowledge "
                    "fallback is labelled as one",
        },
        {
            "id": "general",
            "label": "General",
            "note": "Answer from general knowledge alone; no transcript is "
                    "sent (fastest)",
        },
    ],

    # The system instruction the live answer runs with, replacing the chat
    # agent's own - that one is written for a chat pane and produces
    # paragraphs, and the answers rail is a ~440 px card. The length rule is
    # in the prompt, not only in the docs, because the docs do not reach the
    # model.
    "answer_instructions": {
        "transcripts": (
            "You answer a question during a live meeting or lecture. Your "
            "answer is shown in a small card next to the transcript.\n\n"
            + _ASR_NOTE + "\n\n"
            "Check the session evidence you were given first. It answers the "
            "question if it states the answer, paraphrases it, or mentions it "
            "in a partial or garbled form - look for all three before deciding "
            "it does not.\n"
            "- If it answers the question, reply with exactly one line:\n"
            "  From transcripts: <answer>\n"
            "- Otherwise, reply with exactly these two lines:\n"
            "  From transcripts: No answer found\n"
            "  General knowledge: <answer>\n\n"
            "Markdown is rendered: use **bold**, `code` and - bullets; write math in $...$ with simple LaTeX. "
            "Keep the answer under 25 words, or a short list when the "
            "question genuinely needs one. Give the answer itself, not the "
            "evidence behind it: no quoting or retelling what the speaker "
            "said, no parenthetical detail. No preamble, no restating the "
            "question, no hedging about being an AI, no closing offer of "
            "further help. Never emit a line other than the ones above."
        ),
        "general": (
            "You answer a question during a live meeting or lecture. Your "
            "answer is shown in a small card next to the transcript.\n\n"
            "Answer from your own knowledge. Any transcript or reference "
            "material sent with the question is background about where the "
            "question came from, not the subject of it, and is not to be "
            "checked or cited.\n\n"
            "Markdown is rendered: use **bold**, `code` and - bullets; write math in $...$ with simple LaTeX. "
            "Keep the answer under 25 words, or a short list when the "
            "question genuinely needs one. No label or prefix, no preamble, no "
            "restating the question, no hedging about being an AI, no "
            "closing offer of further help."
        ),
    },

    # BU151: Transcripts mode when the displayed session has documents. Three
    # evidence tiers instead of two, each labelled so the user can see which
    # one answered; the closing "Documents used:" line is how the service
    # tells the card which documents to badge. Without documents the plain
    # "transcripts" instruction above is used, unchanged.
    "answer_instruction_with_documents": (
        "You answer a question during a live meeting or lecture. Your "
        "answer is shown in a small card next to the transcript.\n\n"
        "You have two sources of evidence: the session transcript, and the "
        "documents the user added to this session (each headed [D<id>] with "
        "its file name). Check them in that order. " + _ASR_NOTE + " A source "
        "answers the question if it states the answer, paraphrases it, or "
        "mentions it in a partial or garbled form - look for all three before "
        "deciding it does not.\n"
        "- If the transcript answers the question, reply with exactly one "
        "line:\n"
        "  From transcripts: <answer>\n"
        "- If it does not but a document does, reply with exactly these "
        "lines:\n"
        "  From transcripts: No answer found\n"
        "  From documents: <answer>\n"
        "  Documents used: D<id>, D<id>\n"
        "- If neither does, reply with exactly these three lines:\n"
        "  From transcripts: No answer found\n"
        "  From documents: No answer found\n"
        "  General knowledge: <answer>\n\n"
        "Markdown is rendered: use **bold**, `code` and - bullets; write math in $...$ with simple LaTeX. "
        "Keep the answer under 25 words, or a short list when the "
        "question genuinely needs one. Give the answer itself, not the "
        "evidence behind it: no quoting or retelling what the speaker "
        "said, no parenthetical detail. No preamble, no restating the "
        "question, no hedging about being an AI, no closing offer of "
        "further help. Never emit a line other than the ones above."
    ),

    # Handled locally, with no model call: every model tested got these right,
    # so paying for them is pure waste.
    "discourse_blocklist": [
        "any questions",
        "any questions so far",
        "any other questions",
        "questions",
        "any answers",
        "make sense",
        "does that make sense",
        "any doubts",
        "all good",
        "everyone good",
        "are we good",
        "right",
        "ok",
        "okay",
        "yeah",
        "you know",
        "isn't it",
        "no",
        "yes",
    ],
}


# Speech-to-text Settings (Parakeet through onnx-asr)
TRANSCRIPTION = {
    "model": "nemo-parakeet-tdt-0.6b-v3",
    # None = full precision (~2.6 GB of RAM while loaded). "int8" needs only
    # ~0.75 GB, but on the AMD Ryzen 7 4800H it was measured on its encoder
    # changed ~50% of the words and returned empty text for whole sentences
    # (dynamic int8 loses accuracy on CPUs without AVX-VNNI). Only switch it
    # on after checking transcripts on the machine that will run it. Setup
    # (src/model_manager.py) downloads only the files for this setting, and
    # the engine loads exactly this variant - changing it means running the
    # model download again.
    "quantization": None,
    # ONNX Runtime threads per inference. None = up to 4, leaving cores free
    # for audio capture and the UI.
    "intra_threads": None,
    # The speech and embedding models are loaded when needed (a session
    # starts, a job runs, the assistant searches) and unloaded after this many
    # seconds with no recording and no work, returning their memory to the
    # system. None = keep them loaded once used.
    "idle_unload_seconds": 300,
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

    # Rate audio chunks are stored and transcribed at. Parakeet works at
    # 16 kHz, so capturing at 44.1/48 kHz and storing that only triples the
    # disk use. Resampling uses PyAV (FFmpeg); without it chunks are stored at
    # the device rate. None = always store at the device rate.
    "storage_sample_rate": 16000,
}


# OpenRouter provider routing per model. A listed model is sent to exactly
# these providers, with no fallback to others (the OpenRouter `provider`
# request field), so latency and behaviour stay predictable.
MODEL_PROVIDER_ROUTING = {
    "anthropic/claude-haiku-5.5": {"only": ["anthropic"], "allow_fallbacks": False},
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
    "anthropic/claude-haiku-5.5",
    "anthropic/claude-3.5-haiku",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-opus-4.5",
    # Google
    "google/gemma-3-12b-it",
    "google/gemma-3-27b-it",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
    "google/gemini-3.8-flash",
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

# Sampling temperature for every other answer call: Specific Session chat and
# live answers. The client default (0.7) lets the model drift off the evidence
# and into general knowledge; same floor caveat as above.
ANSWER_TEMPERATURE = 0.2

# (Unused since BU093.) Previously the minimum score gap between the top routed
# session and the runner-up before an Any Session answer offered a direct scope
# switch. BU093 always offers the best single guess and adds a "choose another
# session" path, so there is no ambiguity threshold to tune. Kept for reference.
SCOPE_OFFER_MARGIN = 0.15

DEFAULT_MODEL = "google/gemini-3.8-flash"

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
