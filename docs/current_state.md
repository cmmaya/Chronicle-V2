# Current State

## Execution Status
- Current BU: BU050
- Next BU: BU051

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

## In Progress BUs
None

## Blocked BUs
None

## Known Issues
- The UI does not yet exist for most features. The backend functionality is largely in place, but there is no way for a user to interact with it.
- Fixed: Live transcription session ending did not update transcription_status to 'transcribed' in database

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
