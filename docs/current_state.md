# Current State

## Execution Status
- Current BU: none
- Next BU: none

## Target Architecture

The current development effort is focused on building a UI for session management and interaction. This includes:

1.  Displaying past sessions and their status (transcribed, summarized).
2.  Allowing users to trigger transcription and summarization for past sessions.
3.  Providing a UI to view summaries and screenshots.
4.  Integrating existing backend functionality for screenshots and summarization with the UI.
5.  Configuring the summarization agent to use a specific model and custom instructions.
6.  Implementing a global hotkey for interactive screen capture.

This work will be carried out in BUs 013-022.

## Completed BUs
BU003 - Audio Recording
BU004 - Screenshot Capture
BU005 - Parakeet Transcription
BU006 - Session Management
BU007 - Summary Generation
BU008 - Refactor Audio Capture for Dual Sources
BU009 - Chunked Audio Recording
BU010 - System Audio Capture
BU011 - Chunk-Based Transcription
BU012 - Fix Transcription Pipeline to Use Real Speech-to-Text Model
BU013 - Display Past Sessions in UI
BU014 - Add Session Status to UI
BU015 - Trigger Transcription from UI
BU016 - Configure Summarization Agent
BU017 - Trigger Summarization from UI
BU018 - Display Summary in UI
BU019 - Screenshot Button in UI
BU020 - Display Screenshots in UI
BU021 - Session Playback
BU022 - Session Deletion
BU023 - Overlapping Audio Chunking
BU024 - Transcription Deduplication and Context
BU025 - Mono Recording
BU026 - VAD Integration
BU027 - Live Transcription Callback
BU028 - Real-time Transcription in SessionManager
BU029 - Display Live Transcriptions in UI
BU030 - UI Layout for Live Transcriptions
BU031 - Live Transcription Checkbox
BU032 - Live Transcription Display
BU033 - Filtering Transcriptions
BU034 - Assistant Conversation Storage
BU035 - Assistant Agent Options Config
BU036 - Assistant OpenRouter Client
BU037 - Assistant Context Models
BU038 - Single Session Assistant Retrieval
BU039 - Assistant Database Search Methods
BU040 - Assistant Session Resolver
BU041 - Whitelisted Assistant Retrieval Tools
BU042 - Assistant Answer Service
BU043 - Assistant UI Panel Skeleton
BU044 - Wire Assistant Ask Action
BU045 - Assistant Clarification Flow UI
BU047 - Past Assistant Conversations UI
BU048 - Session-Scoped Conversation Persistence
BU049 - Assistant Context Loader
BU050 - Assistant Conversation Writeback
BU050-1 - New Chat Button
BU051 - Automatic Summary Setting
BU052 - Auto Summary Toggle Button
BU053 - Auto Summary On Stop
BU054 - Assistant Model Setting
BU055 - Model Selector UI
BU056 - 
BU057 - Screenshot AI Context Field
BU058 - Screenshot Context Generator
BU059 - Screenshot Context UI Button (Fixed - Selection Support)
BU060 - Pause Resume Session Lifecycle
BU061 - Pause Button UI
BU062 - Home Layout Shell
BU063 - Session Search Combobox
BU065 - Selected Session Scope Label
BU066 - Session Action Icon Row
BU071 - RAG Metadata Schema
BU072 - RAG Source Upsert Helpers
BU073 - Add RAG FTS Rebuild
BU074 - Add Unified FTS Search
BU075 - Expose Unified Search Tool
BU076 - Create RAG Result Models
BU077 - Add Current Session FTS Retrieval
BU078 - Replace Current Session Context Path
BU079 - Add Any Session Context Builder
BU080 - Wire Any Session Service Retrieval
BU082 - Tighten Chronicle Assistant Prompt
BU083 - Add Retrieval Tests
BU084 - Add Local Embedding Schema Placeholder
BU085 - Implement RAG Content Indexing
BU086 - Fix RAG FTS MATCH Clause
BU087 - Timestamp-Preserving Transcript Chunking

## In Progress BUs
None

## Blocked BUs
None

## Known Issues
- The UI does not yet exist for most features. The backend functionality is largely in place, but there is no way for a user to interact with it.
- Fixed: Live transcription session ending did not update transcription_status to 'transcribed' in database
- Fixed (BU086): `search_rag_fts` never applied a `WHERE rag_fts MATCH ?` clause, so every RAG search returned the first N chunks by rowid with `bm25()` = -0.0. Both Any Session and Specific Session retrieval ignored the user's question. Now sanitised, parameterised and BM25-ranked.
- Fixed (BU087): transcripts were concatenated into one string per session and split into 1000-char chunks, so no chunk had a usable timestamp (836 transcripts collapsed into 76 chunks). Chunks are now time-windowed (~60s / ~800 chars) and carry their own `start_timestamp`, `end_timestamp` and `source`; re-indexing the live database produced 577 chunks. `HH:MM:SS` citation and screenshot correlation now have real per-chunk time anchors.
- Existing databases pick up the new chunking via `reindex_all_sessions(db)` in `src/rag/indexer.py`; it is not yet wired to any UI trigger (BU091).

## Working Memory
- Fresh repository
- Requirements.txt contains PySide6==6.6.0, mss==10.0.0, parakeet-ctc==0.0.3, requests>=2.31.0, soundcard>=0.12.0, soundfile>=0.12.0, coqui-stt (conditional), openai-whisper
  - Note: openai-whisper requires PyTorch; install a compatible torch wheel before installing requirements.txt
- AudioRecorder class implemented and functional
- Microphone recording working with system default device
- Audio device selection issue resolved (was using non-existent device 7)
- Removed Windows-specific WASAPI code for Linux compatibility
- Audio file creation verified
- System audio capture using PulseAudio monitor sources
- Added get_start_time() and get_elapsed_time() methods for timestamp synchronization
- ScreenshotCapture class implemented with full screen and region capture
- SnippingOverlay widget for interactive region selection
- Screenshots saved as PNG with timestamp metadata
- Screenshot metadata stored in database screenshots table
- Keyboard shortcuts registered via QShortcut (Ctrl+Shift+S, Ctrl+Shift+R)
- ParakeetV3 transcription engine integrated - now raises error if model unavailable
- TranscriptionProcessor class for batch processing
- Transcript storage methods added to database
- Session class for meeting lifecycle management
- SessionManager class for session coordination and component wiring
- Timeline class for timestamp synchronization across components
- SummaryGenerator class with OpenRouter API integration
- Summary templates (key_points, action_items, decisions, full)
- Summaries stored in database summaries table
- Supports Gemini Flash and DeepSeek models
- AudioRecorder now accepts source parameter (mic or system)
- Audio files save to session_path/audio/<source>/ subdirectories
- Session creates audio/mic/ and audio/system/ directories on init
- Architecture ready for independent system audio recorder
- ChunkedAudioRecorder class with 10-second WAV chunk recording
- AudioChunk class for metadata (source, chunk_id, timestamps, file_path)
- DualSourceChunkedRecorder for simultaneous mic/system capture
- Metadata saved as JSON alongside each audio chunk
- SystemAudioRecorder class using soundcard loopback for system audio capture
- TranscriptionProcessor scans audio/mic/ and audio/system/ directories independently
- Chunk tracking to avoid re-transcription of processed chunks
- get_new_chunks() method returns only unprocessed audio chunks
- process_new_chunks() processes chunks incrementally
- Polling support for continuous transcription during recording
- start_polling() / stop_polling() for real-time chunk processing
- Transcribe now raises ModelLoadError instead of fallback to mock
- coqui-stt dependency added to requirements.txt
- Implemented status callback system in SessionManager for real-time UI updates
- Added _on_status_update in MainWindow to display status and errors
- Centralized all status updates through the callback system
- Session list UI added to MainWindow - displays past sessions with name, date, and status
- Added transcription_status and summary_status columns to sessions table
- UI now displays transcription and summarization status for each session
- Status is updated in database after transcription/summary generation
- Session list refreshes after processing to show updated status
- Added Transcribe button to Actions column for untranscribed sessions
- Button shows "Transcribing..." during processing and "Done" when complete
- Summary display via context menu or Actions dropdown when session has summary
- WebRTC VAD integrated into ChunkedAudioRecorder for speech detection
- Silent audio chunks are discarded before saving
- VAD checks audio at 16kHz with 30ms frames
- Configurable VAD aggressiveness (default mode 2)
- VAD settings UI added (Settings > VAD Settings) - threshold % and aggressiveness mode
- Rolling context of last 5 transcriptions maintained for improved accuracy
- Context passed as initial_prompt to transcription engine
- Duplicate text removal at chunk boundaries using overlap detection
- _deduplicate_transcription() removes repeated phrases between consecutive chunks
- LiveTranscriber class created in src/transcription/live.py for real-time transcription
- handle_live_transcription() method in SessionManager processes audio chunks
- live_transcription_callback passed to DualSourceChunkedRecorder in create_session and load_session
- Transcription results saved to database using db.add_transcript()
- Transcription results passed to live_transcription_ui_callback for UI updates
- Fixed QTimer thread safety issue: replaced QTimer.singleShot with QMetaObject.invokeMethod using Q_ARG for thread-safe UI updates from Python threading.Thread
- Implemented chat-like transcription view with QScrollArea and QVBoxLayout
- Transcriptions styled differently for mic (blue bubble) vs system (green bubble) audio
- Added add_transcription_to_view() method for adding styled transcription bubbles
- Added _create_transcription_view() method to create the scrollable transcription area
- Added search_transcripts(query, limit, session_id) for parameterized transcript search
- Added search_summaries(query, limit, session_id) for parameterized summary search
- Added find_sessions(query, limit) for searching sessions by name, summary, or transcript content
- All search methods use parameterized SQL to prevent injection
- AssistantRetrievalTools class created in src/assistant/tools.py
  - find_sessions(query, limit) - Search sessions by name, summary, or transcript
  - get_session_context(session_id, question, transcript_limit) - Get bounded session context
  - search_transcripts(query, limit, session_id) - Search transcripts by text
  - search_summaries(query, limit, session_id) - Search summaries by content
  - get_screenshots_near(session_id, timestamp, tolerance_seconds, limit) - Get screenshots near timestamp
- All tools validate inputs and cap limits at maximum values
- No raw SQL execution exposed to answer service
- Tests added in tests/test_assistant_tools.py (37 tests passing)
- Assistant UI panel skeleton added to main window with agent selector, scope control, question input, Ask/Detach buttons, and answer display
- AssistantAnswerService wired to Ask button in main window
- Ask button now calls service with question, agent_id, scope, active_session_id, and selected_session_id
- Error handling displays in answer area without crashing UI
- Conversation history now loaded into assistant context for follow-up questions
- Added ConversationTurn dataclass to context_models
- Added conversation_history field to AssistantContext
- Context retriever loads up to 10 recent conversation turns from database
- Service passes conversation_id through to context retrieval for follow-up questions
- Added ALLOWED_MODELS list and DEFAULT_MODEL constant to config.py
- Added get_selected_model() and set_selected_model() functions for model selection
- Added session search with autocomplete completer in main window
- Assistant scope modes are "Specific Session" / "Any Session" (BU088). Internal keys unchanged (`current` / `current_session`).
- `_scope_label` is a live mode indicator: `Scope: Specific Session - <name>` or `Scope: Any Session - all meetings`, updated on scope change, session selection and ESC
- Main and detached scope combos sync bidirectionally with a recursion guard; the detached window is no longer closed on a scope change
- Each assistant answer bubble is tagged with the scope that produced it ("via Specific Session - <name>" / "via Any Session"), display-only
- Two-tier Any Session retrieval (BU089): `src/rag/router.py` `route_sessions()` picks the top ~5 sessions for a question (hybrid cosine + BM25 over one `session_profiles` row per session), then Tier-2 chunk search runs only inside them via `build_routed_session_context`
- `src/rag/embeddings.py`: local multilingual ONNX embedder (`intfloat/multilingual-e5-small`, pinned by revision), loaded on the existing `onnxruntime`; only `tokenizers` added. Degrades to lexical-only routing when unavailable; never crashes
- `AnswerResponse.routed_sessions` carries the routed candidates for Any Session answers (empty for other scopes); consumed by BU090
- Answer contract (BU090): in Any Session mode only, the system prompt gets `RESPONSE_CONTRACT` (`src/assistant/response_contract.py`); the model appends a `@@CHRONICLE_META@@` JSON trailer with `intent`/`evidence`/`sessions`. `parse_answer()` strips it before display and before `_persist_conversation`, degrading to the full raw text with null metadata on any failure. `intent`/`evidence` are logged only, never rendered
- Any Session answer call uses `config.ANY_SESSION_TEMPERATURE` (0.2) for reliable trailer compliance; all other call paths keep the client default (0.7)
- `AnswerResponse` gained optional `intent`, `evidence`, `scope_used`, `candidate_sessions` (all defaulted)
- Scope handoff (BU090): when Any Session returns `intent == "detail"` or `evidence` in (`partial`, `none`) with routed candidates, the answer gets a short handoff note and the existing candidate picker is shown relabelled ("Ask in Specific Session"), seeded with the routed sessions. Selecting one re-asks the question in Specific Session scope. Wired in both the main and detached assistant windows
- `session_profiles` refreshed on `index_session_content`; whole-corpus backfill is `reindex_all_session_profiles(db)`
- Per-chunk embeddings (BU091): `index_session_content` now computes `embed_passages` vectors for every chunk and `replace_rag_chunks` persists `embedding`/`embedding_model`. Idempotent: a document whose `content_hash` is unchanged and whose chunks already carry current-model vectors is skipped (no writes); `db.count_chunks_missing_embedding` drives re-embed on a model/revision change; `index_session_content(db, sid, force=True)` bypasses the skip. FTS rebuild is gated on an actual chunk change
- `src/rag/migration.py` `backfill_corpus(db, progress_callback, force)`: idempotent, resumable one-time backfill over all sessions; returns `{sessions, processed, failed, embeddings}`. Degrades to lexical-only (NULL vectors) when the embedder is unavailable; a later run repairs the vectors
- Settings menu > "Reindex All (RAG)..." runs `backfill_corpus(force=True)` on `RagBackfillThread` (background QThread) with `sessions done/total` progress on the status bar; invalidates the router profile cache on completion. `closeEvent` waits for a running backfill
- The three `session_manager` index hooks (session stop, transcription complete, summary regenerate) and `window.py` summary path all go through `index_session_content`, so they refresh chunks + chunk embeddings + profile + FTS together
- Agent dropdown now shows actual agent names ("Chronicle Assistant", "Concise Helper", "Research Helper") instead of generic "Agent" label
- Added dropdown arrow indicator (v) to all QComboBox widgets
- Expanded session bar and ask bar width by 1.8x (from 900px to 1620px)
- Refined UI log messages in window.py to be more concise and user-friendly:
  - 'App Started' - app initialization
  - 'Session Started' - session started
  - 'Session Paused' / 'Session Resumed' - pause/resume
  - 'Screenshot Taken' - screenshot capture
  - 'Transcript Window Detached' - transcription window detached
  - Removed verbose messages: 'Loading session...', 'Processing transcriptions...', 'Generating summary...', scope change notifications, session rename notifications
