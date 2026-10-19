# Non-Blocking Assistant Implementation

## Summary

Implemented asynchronous support for the assistant to allow the UI to remain responsive while processing queries. This enables the user to switch to other tasks while waiting for the assistant's response.

## Files Changed

- `src/assistant/service.py`: Added `ask_async` and `_call_openrouter_async` methods.
- `src/assistant/openrouter_client.py`: Added `chat_async` method using `httpx`, restored synchronous `chat` method for backward compatibility.
- `src/app/window.py`: Created `AssistantQueryThread` class and modified `_run_assistant_query` to use threads.
- `src/storage/database.py`: Added `check_same_thread=False` to SQLite connection for thread safety.

## Important Decisions

- Used `QThread` to run the async function in a separate thread to keep the UI responsive.
- Installed `httpx` library to support both synchronous and asynchronous HTTP requests.
- Maintained backward compatibility by keeping the synchronous `chat` method.
- Added thread safety to database connections to allow the assistant query thread to access the database.

## Bug Fixes

- Fixed "QThread: Destroyed while thread is still running" error by:
  - Adding `_assistant_thread` instance variable to track active thread.
  - Waiting for previous thread to finish before starting a new one.
  - Waiting for thread to finish in `closeEvent` before closing.
  - Clearing thread reference in signal handlers.
- Fixed conversation context not being saved by enabling thread-safe SQLite connections.

## Recovery Notes

- The UI now uses `AssistantQueryThread` to run the assistant query in a background thread.
- The `ask` method still works synchronously for backward compatibility.
- The new `ask_async` method can be used for fully async workflows.

# BU003 Implementation Summary (Completed)

- Created audio directory structure
- Implemented AudioRecorder class with start/stop methods
- Added device enumeration and selection functionality
- Installed required dependencies (sounddevice, numpy)
- Added error handling and logging
- Verified audio device enumeration works
- Fixed audio device selection issue (was using non-existent device 7)
- Basic microphone recording functional with system default device
- Audio files successfully saved as WAV files
- Removed Windows-specific WASAPI code for Linux compatibility
- Implemented system audio capture using PulseAudio monitor sources

# Key Accomplishments

- Microphone recording functional
- System audio capture using PulseAudio monitor sources
- Session folder integration
- Device enumeration and selection
- Clear error handling

# Future Improvements

- Integration with session manager from BU002
- More sophisticated device selection UI
- Additional audio formats support

# Environment:

- Python 3.12.3
- Virtual environment with sounddevice and numpy installed
- PortAudio library installed
- Documentation updated to reflect current status

# BU004 Implementation Summary (Completed)

- Created screenshots directory structure
- Implemented ScreenshotCapture class with full screen and region capture methods
- Created SnippingOverlay widget for interactive drag-select region snipping
- Used mss library for cross-platform screenshot capture
- Screenshots saved as PNG files to session screenshots/ subdirectory
- Timestamp metadata stored in database screenshots table
- Added keyboard shortcut support via QShortcut (Ctrl+Shift+S full screen, Ctrl+Shift+R region)
- Added get_start_time() method for timestamp synchronization
- Added mss dependency to requirements.txt

# Key Accomplishments

- Full screen capture verified working
- Region capture functional via coordinate API
- Interactive snipping overlay with rubber band selection
- Database metadata storage integration
- Consistent with AudioRecorder patterns (session_path, logging, error handling)

# Future Improvements

- Integration with session manager from BU006 for dynamic session_id
- Thumbnail generation for screenshot gallery
- Multi-monitor support improvement

# Environment:

- mss 10.2.0 installed
- Test screenshots saved to /tmp/sessions/session_001/screenshots/
- All existing dependencies preserved

---

## BU003 Fix - Timestamp Synchronization

Summary:
Added `get_start_time()` and `get_elapsed_time()` methods to AudioRecorder class for timestamp synchronization with transcription and screenshots.

Files Changed:

- src/audio/recorder.py

Important Decisions:

- Added both absolute timestamp (get_start_time) and relative elapsed time (get_elapsed_time) methods to support different synchronization use cases

Recovery Notes:

- Methods enable transcription sync per architecture principle: "Timestamp synchronization between transcript and screenshots"

---

## BU005 - Parakeet Transcription

Summary:
Implemented audio transcription using Parakeet V3 for both microphone and system audio streams. Created transcription directory structure with ParakeetV3 engine wrapper and TranscriptionProcessor workflow class.

Files Changed:

- src/transcription/**init**.py
- src/transcription/parakeet.py
- src/transcription/processor.py
- src/storage/database.py (added transcript methods)
- requirements.txt (added parakeet-ctc==0.0.3)

Important Decisions:

- Mock mode fallback when no Parakeet model is available (allows testing without heavy model download)
- Audio preprocessing handles WAV loading, mono/stereo conversion, and resampling to 16kHz
- Timestamp extracted from audio filename (YYYYMMDD_HHMMSS format)
- Source detection based on filename pattern (\_system suffix)
- Database stores transcripts with session_id, timestamp, text, and source fields

Recovery Notes:

- TranscriptionProcessor integrates with existing AudioRecorder file format (WAV, 44100Hz, 16-bit)
- Ready for BU006 integration with session manager
- Mock mode enables development testing before model installation

---

## BU006 - Session Management

Summary:
Implemented session lifecycle management integrating recording, screenshots, and transcription into cohesive meeting sessions. Created Session class for meeting lifecycle, SessionManager for coordination, and Timeline for timestamp synchronization.

Files Changed:

- src/app/session.py (new)
- src/app/session_manager.py (new)
- src/app/timeline.py (new)
- src/app/**init**.py (updated exports)
- docs/current_state.md (updated status)

Important Decisions:

- Session manages lifecycle states: active, paused, stopped, processing, completed
- SessionManager wires together AudioRecorder, ScreenshotCapture, TranscriptionProcessor
- Timeline provides timestamp correlation across components
- All components use consistent session_path pattern for file organization

Recovery Notes:

- Ready for BU007 integration with UI
- All components now coordinate through SessionManager
- Timeline enables per-architecture: "Timestamp synchronization between transcript and screenshots"

---

## BU007 - Summary Generation

Summary:
Implemented meeting summary generation using OpenRouter API with Gemini Flash and DeepSeek models. Created SummaryGenerator class with template-based summarization supporting key points, action items, decisions, and comprehensive summaries.

Files Changed:

- src/summarization/**init**.py (new)
- src/summarization/templates.py (new)
- src/summarization/generator.py (new)
- src/storage/database.py (added summaries table and methods)
- requirements.txt (added requests>=2.31.0)

Important Decisions:

- Template-based approach with 4 template types for flexible summary generation
- Lazy import of requests library to handle missing dependency gracefully
- Default to Gemini Flash 2.0 model, with DeepSeek alternatives available
- Database integration for persistent summary storage
- All template types can be generated in one call via generate_all()

Recovery Notes:

- Ready for BU008 integration with Notion sync
- API key required for OpenRouter (set via SummaryGenerator constructor)
- Mock mode could be added for testing without API key

---

## BU009 - Chunked Audio Recording

Summary:
Implemented chunked audio recording with 10-second WAV chunks and metadata. Created AudioChunk dataclass for metadata management and ChunkedAudioRecorder class for continuous chunked capture. DualSourceChunkedRecorder coordinates simultaneous mic/system recording.

Files Changed:

- src/audio_capture/**init**.py (new)
- src/audio_capture/core.py (new)
- src/audio_capture/chunk.py (new)

Important Decisions:

- AudioChunk uses dataclass with ISO timestamp strings for serialization
- Metadata saved as JSON file alongside each audio chunk
- ChunkedAudioRecorder uses threading for background chunk processing
- Samples per chunk calculated as sample_rate \* chunk_duration

Recovery Notes:

- Ready for BU010 to process chunks in transcriber
- Uses existing AudioRecorder for underlying audio capture
- Handles both mic and system sources in same manner

---

## BU010 - System Audio Capture

Summary:
Implemented system audio capture using soundcard loopback functionality. Added soundcard and soundfile dependencies, created SystemAudioRecorder class for loopback capture, and integrated it into the ChunkedAudioRecorder architecture. The system audio now uses the same chunking mechanism as microphone audio, saving 10-second timestamped chunks with metadata to the audio/system/ directory.

Files Changed:

- requirements.txt (added soundcard, soundfile)
- src/audio_capture/system_recorder.py (new)
- src/audio_capture/core.py (modified ChunkedAudioRecorder)
- src/audio_capture/**init**.py (updated exports)

Important Decisions:

- SystemAudioRecorder uses soundcard library's loopback functionality
- Auto-detects default speaker and uses its loopback microphone
- Uses buffer-based approach (poll every 500ms)不同于 sounddevice的callback方式
- Same chunking mechanism as mic for consistency
- ChunkedAudioRecorder now routes to appropriate recorder based on source type

Recovery Notes:

- Ready for BU011 to process system audio chunks in transcriber
- Both microphone and system audio now independently capturable
- DualSourceChunkedRecorder coordinates simultaneous capture

---

## BU011 - Chunk-Based Transcription

Summary:
Refactored the TranscriptionProcessor to work with chunked audio from dual sources. Added chunk tracking to avoid re-transcription, new methods for incremental processing, and polling support for real-time transcription as chunks appear.

Files Changed:

- src/transcription/processor.py (modified)

Important Decisions:

- Scans audio/mic/ and audio/system/ directories separately
- Uses in-memory set to track processed chunks (avoids re-transcription)
- Added get_new_chunks() for incremental processing
- Added polling loop for continuous transcription during recording

Recovery Notes:

- Ready for BU012 to merge and order transcripts chronologically
- Source attribution preserved in database (mic vs system)
- Timestamps extracted from chunk metadata for synchronization

---

## BU012 - Fix Transcription Pipeline to Use Real Speech-to-Text Model

Summary:
Fixed the transcription pipeline to use a real speech-to-text model instead of silently falling back to mock transcriptions. The pipeline now raises a clear ModelLoadError if neither parakeet-ctc nor coqui-stt can be loaded.

Files Changed:

- src/transcription/parakeet.py (modified)
- src/transcription/processor.py (modified)
- requirements.txt (added coqui-stt)

Important Decisions:

- Removed mock fallback in ParakeetV3.load() - now raises ModelLoadError if no model available
- Removed \_mock_transcribe() method entirely
- Added ModelLoadError handling in TranscriptionProcessor methods
- Added coqui-stt as alternative dependency in requirements.txt

Recovery Notes:

- Ready for BU013 to implement temporal merge layer for transcripts
- Application will now fail with clear error if STT model cannot be loaded
- Users must install either parakeet-ctc or coqui-stt package and download model files

---

## BU013 - Display Past Sessions in UI

Summary:
Added a session list widget to the main window that displays all past sessions from the database. Each session entry shows the session name, timestamp, and status. The list is populated on application startup.

Files Changed:

- src/app/window.py (modified)

Important Decisions:

- Used QGroupBox to group the sessions list with a label
- Used existing Database.list_sessions() method which was already implemented
- Format: "Session name - YYYY-MM-DD HH:MM - status"
- Sessions are sorted by start_time DESC (newest first) as per database query

Recovery Notes:

- Ready for BU014 to add session interaction (clicking, selecting)
- No interaction functionality included per BU013 scope
- Uses existing database method - no schema changes needed

---

## BU014 - Add Session Status to UI

Summary:
Added transcription and summarization status display to the session list in the UI using a QTableWidget with separate cells. Database schema updated with transcription_status and summary_status columns, status updates are set in the database when transcription/summarization complete, and only the session name cell is editable.

Files Changed:

- src/storage/database.py (added transcription_status and summary_status columns with migration)
- src/app/window.py (changed from QListWidget to QTableWidget with 3 columns)
- src/app/session_manager.py (modified process_transcriptions to update status)

Important Decisions:

- Database migration uses ALTER TABLE for existing databases (try/except for OperationalError)
- Status values: 'none' (default), 'transcribed', 'summarized'
- UI uses QTableWidget with 3 columns: Session Name (editable), Transcription (read-only), Summary (read-only)
- Session list refreshes after processing to show updated status
- Only name column is editable; status columns are read-only via item flags

Recovery Notes:

- Ready for BU015 to add trigger buttons for transcription/summarization
- Status is informational only per BU014 scope

---

## BU015 - Trigger Transcription from UI

Summary:
Added a Transcribe button to the UI for triggering transcription on past sessions. Sessions table now has 4 columns: Session Name, Transcription, Summary, and Actions. The button appears only for untranscribed sessions (transcription_status != 'transcribed'). When clicked, it disables the button, shows "Transcribing..." text, processes transcriptions via SessionManager, updates the database status, reloads the UI, and shows a completion message.

Files Changed:

- src/app/window.py (added Actions column with Transcribe button, \_on_transcribe_clicked, \_run_transcription methods)

Important Decisions:

- Button shows "Done" for already transcribed sessions
- Uses QTimer.singleShot to run transcription in background to keep UI responsive
- Session is loaded via SessionManager.load_session() for processing
- Database status updated to 'transcribed' after successful transcription

Recovery Notes:

- Ready for BU016 to add trigger button for summarization
- Transcription process already exists in session_manager.py - this BU only adds the UI trigger

---

## BU016 - Configure Summarization Agent

Summary:
Updated SummaryGenerator to support custom instructions loaded from a configuration file. Added config.py with summarization settings including model selection and custom instructions.

Files Changed:

- src/config.py (new - configuration settings for summarization)
- src/summarization/generator.py (added custom_instructions parameter and \_load_custom_instructions method)

Important Decisions:

- Default model is GEMINI_2_5_FLASH
- Custom instructions are optional and prepended to template system prompts
- Configuration loaded from SUMMARIZATION dict in config.py

Recovery Notes:

- Ready for BU017 to add trigger button for summarization
- Custom instructions can be modified in config.py

---

## BU017 - Trigger Summarization from UI

Summary:
Added a Summarize option to the Actions dropdown for triggering summarization on transcribed sessions. The button appears only when transcription_status == 'transcribed' and summary_status in (None, 'none'). Uses QTimer.singleShot for background processing to keep UI responsive.

Files Changed:

- src/app/window.py (added Summarize action option and \_on_summarize_clicked, \_run_summarization methods)

Important Decisions:

- Summarize option in dropdown appears only when transcription is complete
- Uses existing SummaryGenerator.generate_and_store method
- UI provides feedback during summarization process
- Status updated to 'summarized' after completion

Recovery Notes:

- Ready for BU018 to add view summary functionality
- Combined with BU016 for implementation efficiency

---

## BU018 - Display Summary in UI

Summary:
Added functionality to display session summaries in a separate window. Users can double-click on a summarized session or use the "View Summary" option from the context menu to view the summary content in a modal dialog.

Files Changed:

- src/app/window.py (added \_on_session_double_clicked, \_show_session_summary methods, QDialog and QTextBrowser imports)

Important Decisions:

- Summary display uses a QDialog with QTextBrowser for readable text presentation
- Shows session name, summary type, and model used in the dialog header
- Double-click handling on table rows triggers summary display
- Context menu option provides alternative access to view summary
- Shows informative message if session has no summary

Recovery Notes:

- Ready for BU019 for additional UI features
- Uses existing Database.get_summaries() method

---

## BU020 - Display Screenshots in UI

Summary:
Added functionality to display session screenshots in a separate window with timestamps. Users can:

1. Right-click on a past session and select "View Screenshots" from the context menu
2. Click the "View Screenshots" button during an active session

Files Changed:

- src/storage/database.py (added get_screenshots method)
- src/app/window.py (added \_show_session_screenshots method, \_on_view_screenshots method, "View Screenshots" button, added QScrollArea and QGridLayout imports, added "View Screenshots" to context menu)

Important Decisions:

- Screenshot display uses a QDialog with QScrollArea for scrollable content
- Uses QGridLayout to display screenshots in a 2-column grid
- Each screenshot is displayed with its capture timestamp
- Images are scaled to 300x200 while maintaining aspect ratio
- Shows informative message if session has no screenshots
- Uses existing Database.get_screenshots() method
- Added dedicated "View Screenshots" button enabled during active sessions

Recovery Notes:

- Ready for BU021 for additional UI features

---

## BU023 - Overlapping Audio Chunks Implementation

Summary:
Implemented overlapping audio chunk recording in ChunkedAudioRecorder to prevent word loss at chunk boundaries. Each chunk saves 10 seconds of audio with a 1-second overlap from the previous chunk. This ensures continuous audio coverage for transcription.

Files Changed:

- src/audio_capture/core.py (added overlap_duration parameter and overlapping chunk logic)

Important Decisions:

- Added overlap_duration parameter to ChunkedAudioRecorder (default: 1 second)
- Rewrote \_recording_loop() to use continuous buffer approach with overlap
- Rewrote \_system_recording_loop() with same overlapping logic
- Added \_save_chunk_from_array() method for saving chunks from numpy arrays
- Updated DualSourceChunkedRecorder to pass overlap_duration to both sources
- Updated factory functions to accept overlap_duration parameter
- Each chunk saves every (chunk_duration - overlap_duration) seconds of NEW audio
- Buffer keeps only the overlap portion after each save for the next chunk
- Maintains backward compatibility via default parameter value

How It Works:

- Chunk duration: 10 seconds
- Overlap: 1 second
- Save interval: Every 9 seconds of NEW audio
- Each chunk: Last 10 seconds (9s new + 1s from previous)
- Buffer after save: Keeps only last 1 second for next chunk

Recovery Notes:

- VAD integration (BU026) continues to work - chunks still filtered by speech detection
- Transcription pipeline processes each chunk independently
- Overlap enables better transcription at chunk boundaries

---

## BU024 - Transcription Deduplication and Context

Summary:
Implemented rolling context of recent transcriptions (last 5) to improve accuracy, passes context as initial_prompt for better transcription, and removes duplicate text at chunk boundaries using overlap detection.

Files Changed:

- src/transcription/processor.py (existing implementation verified)

Important Decisions:

- Rolling context uses last 5 transcriptions via `_transcription_context` list
- Context prompt built via `_get_context_prompt()` method and passed to transcribe_audio()
- Deduplication handled by `_deduplicate_transcription()` method which detects overlapping phrases
- Algorithm checks last 8, 6, 4, 3 words for overlap detection
- Partial matches with 70%+ word similarity also removed
- Context limited to MAX_CONTEXT_LENGTH (2000 chars) to prevent prompt overflow
- Note: Parakeet via onnx-asr does not natively support initial_prompt, but the context is prepared for compatibility with other engines (whisper.py)

---

## BU026 - VAD Integration

Files Changed:

- requirements.txt (added webrtcvad-wheels)
- src/audio_capture/core.py (added VAD integration)

Important Decisions:

- Used webrtcvad-wheels for pre-compiled Windows binaries
- VAD operates at 16kHz with 30ms frames (WebRTC standard)
- Configurable aggressiveness mode (0-3, default 2)
- Returns None from \_save_chunk when no speech detected
- Only appends chunks to list and calls callback if chunk contains speech
- Handles both mic and system audio recording loops

Recovery Notes:

- Ready for BU027 (next BU)
- No changes to UI or transcription pipeline needed
- Silent chunks are simply not saved - existing transcription code works unchanged

---

## BU027 - Live Transcription Callback

Summary:
Added a callback mechanism to the `ChunkedAudioRecorder` to trigger live transcription for each new audio chunk. The `live_transcription_callback` is called after each chunk is saved, receiving the `AudioChunk` object.

Files Changed:

- src/audio_capture/core.py (modified ChunkedAudioRecorder, DualSourceChunkedRecorder, and factory functions)

Important Decisions:

- Added `live_transcription_callback` parameter to `ChunkedAudioRecorder.__init__` with proper documentation
- Callback is called in both `_save_chunk` and `_save_chunk_from_array` methods after chunk is successfully saved
- Added debug print statements to verify callback is triggered during recording
- `DualSourceChunkedRecorder` propagates the callback to both mic and system recorders, passing the source as first argument
- Factory functions updated to accept the new callback parameter

Recovery Notes:

- Ready for BU028 to implement live transcription using this callback
- Callback is non-blocking - exceptions are caught and logged but don't interrupt recording
- Both mic and system audio chunks will trigger the callback when recorded

---

## BU028 - Real-time Transcription in SessionManager

Summary:
Implemented the logic in SessionManager to handle live transcription of audio chunks. Created a new LiveTranscriber class that wraps the Parakeet engine for real-time transcription, with deduplication logic to avoid repeated text at chunk boundaries. Live transcriptions are now saved to the database.

Files Changed:

- src/transcription/live.py (new)
- src/app/session_manager.py (modified)

Important Decisions:

- Created LiveTranscriber class with transcribe_chunk() method that handles AudioChunk objects
- Added handle_live_transcription() method to SessionManager that receives (source, chunk) from the callback
- Pass live_transcription_callback to DualSourceChunkedRecorder in both create_session and load_session
- Results are printed to console for debugging and passed to live_transcription_ui_callback for UI integration
- LiveTranscriber maintains context and deduplicates between consecutive chunks
- Live transcriptions are saved to the database using existing db.add_transcript() method

Recovery Notes:

- Ready for BU029 to display live transcriptions in UI
- Transcription runs in the recording thread - may need threading adjustment for production
- Model loading happens on first chunk - may cause initial delay

---

## BU029 - Display Live Transcriptions in UI

Summary:
Added live transcription display to the UI. Created a QGroupBox with QTextEdit to show transcriptions in real-time during recording. Transcriptions display with timestamp, source (Mic/System), and text.

Files Changed:

- src/app/window.py

Important Decisions:

- Added QTextEdit import
- Added live_transcription_group with QTextEdit in \_create_central_widget
- Created \_on_live_transcription callback method that receives dict with text, source, timestamp
- Passed live_transcription_ui_callback to SessionManager initialization
- Clear display on session start

Recovery Notes:

- Ready for BU030
- Fix applied: LiveTranscriber now passes rolling context as initial_prompt to Parakeet engine for improved accuracy

---

## Fix - LiveTranscriber Rolling Context

Summary:
Fixed LiveTranscriber to pass rolling context as initial_prompt to the Parakeet engine for improved transcription accuracy. Previously context was stored but not used.

Files Changed:

- src/transcription/live.py

Important Decisions:

- Build initial_prompt from self.\_context before transcribing
- Pass initial_prompt to self.engine.transcribe()
- Include context in result dict for debugging

---

## BU030 - UI Layout for Live Transcriptions

Summary:
Changed the main window layout from vertical to horizontal split. Left side contains existing controls (title, session name, buttons, status, past sessions), right side contains live transcription display.

Files Changed:

- src/app/window.py

Important Decisions:

- Changed main layout from QVBoxLayout to QHBoxLayout
- Created left_widget with QVBoxLayout for existing controls
- Created right_widget with QVBoxLayout for live transcriptions
- Both widgets use stretch factor 1 for equal width
- Live transcription group moved to right side
- Reduced margins from 40 to 20 for better space utilization

Recovery Notes:

- Ready for BU031
- UI now has horizontal split layout with placeholder on right side

---

## BU031 - Live Transcription Checkbox

Summary:
Added checkbox to enable/disable live transcription. Also added a detachable window feature to pop out the live transcription display into a separate window.

Files Changed:

- src/app/window.py
- src/app/session_manager.py

Important Decisions:

- Added QCheckBox import and widget to UI (enabled by default)
- Modified create_session() to accept enable_live_transcription parameter
- Modified start_session() to accept enable_live_transcription parameter
- Callback is only passed to recorder when enabled
- Added detach button to pop out transcription to separate window
- Detached window syncs with main window display

Recovery Notes:

- Ready for BU032

---

## BU032 - Live Transcription Display

Summary:
Implemented a "chat-like" UI component to display live transcriptions in real-time. Transcriptions are displayed as styled bubbles in a scrollable area, with different visual styles for microphone (blue) and system (green) audio sources.

Files Changed:

- src/app/window.py

Important Decisions:

- Created \_create_transcription_view() method with QScrollArea and QVBoxLayout for scrollable display
- Created add_transcription_to_view() method to add styled transcription bubbles
- Mic transcriptions displayed in blue bubbles with microphone emoji
- System transcriptions displayed in green bubbles with speaker emoji
- Each bubble shows source label, timestamp, and transcription text
- Word wrapping enabled for long transcriptions
- Auto-scroll to bottom when new transcriptions are added
- Updated \_append_transcription() to parse formatted text and use the new chat-like view
- Detached window now uses same chat-like interface as main UI
- Detached window set as independent window (no parent) so it stays open when main window is minimized
- Detached window stays on top of other windows (WindowStaysOnTopHint)
- Detached window has WA_QuitOnClose=False to prevent app quit on close

Recovery Notes:

- Ready for BU033

---

## BU033

Summary:
Added controls to filter live transcriptions by source (All, Mic only, System only). Filter works in both main window and detached transcription window.

Files Changed:

- src/app/window.py

Important Decisions:

- Used QComboBox for filter selection in both main and detached windows
- Stored source as Qt property on bubble frame for efficient filtering
- Filter applies both to new transcriptions and existing ones
- Detached window has independent filter control

Recovery Notes:

- Filter state tracked via `_transcription_filter` variable
- Main window filter combo available via `_transcription_filter_combo`
- Detached window filter combo available via `_detached_filter_combo`

---

## Database Structure

SQLite database: `chronicle.db`

### Tables:

**sessions**

- id (INTEGER PRIMARY KEY)
- name (TEXT)
- start_time (INTEGER)
- end_time (INTEGER)
- status (TEXT)
- transcription_status (TEXT DEFAULT 'none')
- summary_status (TEXT DEFAULT 'none')

**transcripts**

- id (INTEGER PRIMARY KEY)
- session_id (INTEGER FOREIGN KEY)
- timestamp (INTEGER)
- text (TEXT)
- source (TEXT) - 'microphone' or 'system'

**screenshots**

- id (INTEGER PRIMARY KEY)
- session_id (INTEGER FOREIGN KEY)
- timestamp (INTEGER)
- filepath (TEXT)
- description (TEXT)

**summaries**

- id (INTEGER PRIMARY KEY)
- session_id (INTEGER FOREIGN KEY)
- summary_type (TEXT)
- content (TEXT)
- model_used (TEXT)
- created_at (INTEGER)

---

## Fix - Live Transcription Status Not Updated

Summary:
Fixed bug where transcription_status was not updated to 'transcribed' when session ends with live transcription enabled. The previous code checked `if result:` which is always truthy (dictionary exists), but when live transcription already saved transcripts to the database, the batch processing found no new files and returned empty lists.

Files Changed:

- src/app/session_manager.py

Important Decisions:

- Changed condition from `if result:` to `if self.db.get_transcripts(session.id):` to check if there are any transcripts in the database, regardless of whether they came from live transcription or batch processing

Recovery Notes:

- This ensures transcription_status is correctly updated to 'transcribed' when:
  1. Live transcription was used during recording (transcripts saved in real-time)
  2. Batch processing found new audio files to transcribe
  3. Any transcripts exist in the database for the session

---

## BU034 - Assistant Conversation Storage

Summary:
Added SQLite tables and database methods for persisting assistant conversations and messages. Supports session-scoped conversations (linked to session_id) and global conversations (null session_id for all-session scope).

Files Changed:

- src/storage/database.py

Important Decisions:

- Used `CREATE TABLE IF NOT EXISTS` for schema creation
- session_id is nullable to support both session-scoped and global conversations
- Messages stored with role ('user', 'assistant', 'system') and plain text content only
- Messages returned in chronological order (timestamp ASC)
- Conversation updated_at timestamp automatically updated when messages are added
- Delete conversation cascades to delete all associated messages

Recovery Notes:

- Ready for BU035 (assistant UI integration)
- Plain text only - no screenshots or audio binaries stored
- Schema migrations for existing databases not yet implemented (manual migration required)

---

## BU035 - Assistant Agent Options Config

Summary:
Added ASSISTANT_AGENTS configuration to config.py with two assistant agent options. Each agent has id, label, model, and system_instruction. Default agent is set to "chronicle_assistant".

Files Changed:

- src/config.py

Important Decisions:

- Defined 2 agents: "chronicle_assistant" (default) and "concise_helper"
- Both agents use google/gemini-2.5-flash model
- System instructions focus on session-grounded answers and clarification when context is ambiguous
- Existing SUMMARIZATION config remains compatible (no changes needed)

Recovery Notes:

- Ready for BU036 (UI dropdown wiring)
- Config structure allows easy addition of more agents
- Default agent accessible via ASSISTANT_AGENTS["default"]

---

## BU036 - Assistant OpenRouter Client

Summary:
Created a minimal OpenRouter chat client for assistant answers. The client is separate from the existing summarization code and provides a focused interface for making chat completions.

Files Changed:

- src/assistant/openrouter_client.py (new)
- src/assistant/**init**.py (new)
- tests/test_openrouter_client.py (new)

Important Decisions:

- Created OpenRouterClient class with chat() method accepting api_key, model, messages, and optional temperature
- Returns assistant text only (not full response object)
- Raises clear custom exceptions: MissingAPIKeyError, APIRequestError, InvalidResponseError
- Uses same API URL pattern as SummaryGenerator but is a separate focused client
- Supports loading API key from environment variable (OPENROUTER_API_KEY)
- Default temperature of 0.7 for assistant conversations
- Uses lazy import for requests library to handle missing dependency gracefully

Validation:

- All 8 unit tests pass:
  - test_missing_api_key_raises_error
  - test_missing_model_raises_error
  - test_empty_messages_raises_error
  - test_successful_response_parsing
  - test_missing_choices_raises_error
  - test_http_error_raises_api_error
  - test_import_openrouter_client
  - test_import_exceptions

Recovery Notes:

- Ready for BU037 (UI dropdown wiring and integration)
- Client can be imported without side effects
- No summarization code was modified - this is a separate focused client

---

## BU037 - Assistant Context Models

Summary:
Created simple dataclasses for session context, search matches, and clarification candidates. Includes AssistantContext, SessionCandidate, TranscriptExcerpt, SummaryExcerpt, and ScreenshotReference classes with prompt text generation methods.

Files Changed:

- src/assistant/context_models.py (new)
- src/assistant/**init**.py (updated exports)
- tests/test_context_models.py (new)

Important Decisions:

- Used plain dataclasses (not abstraction framework) per BU specification
- Included to_prompt_text methods for prompt construction
- All primitive fields for serialization compatibility
- is_empty() method for safe empty context checking

Validation:

- All 20 unit tests pass:
  - Test constructing context models
  - Test prompt text generation for transcript and summary excerpts
  - Test empty context renders safely
  - Full context rendering with sessions, transcripts, summaries, and screenshots

---

## BU038 - Single Session Assistant Retrieval

Summary:
Created AssistantContextRetriever class to build bounded context from SQLite-backed resources for a single session. Supports retrieving summaries, filtering transcripts by keyword matching, and finding screenshots near relevant transcript timestamps.

Files Changed:

- src/assistant/context.py (new)
- tests/test_context_retriever.py (new)

Important Decisions:

- Used Protocol for database abstraction (DatabaseProtocol)
- Keyword extraction filters stop words and extracts content words > 3 chars
- Transcript filtering limits to top 5 keyword matches or most recent 20
- Long transcripts truncated to 500 characters with "..." suffix
- Screenshots matched within 60-second window of relevant transcript timestamps
- Raises NotImplementedError with clear BU039 TODO if get_transcripts missing

Definition of Done Satisfied:

- [x] build_session_context returns deterministic context for explicit session_id
- [x] Context includes summary text when available
- [x] Context includes bounded transcript excerpts
- [x] Context includes local screenshot references only

Validation:

- All 12 unit tests pass:
  - Test context building returns valid AssistantContext
  - Test empty database returns empty but valid context
  - Test summary inclusion when available
  - Test transcript filtering with keywords
  - Test transcript filtering returns recent when no keywords
  - Test long transcripts truncated
  - Test screenshots near transcripts
  - Test no transcripts means no screenshot filtering
  - Test missing session uses default name
  - Test keyword extraction filters stop words
  - Test keyword extraction includes content words
  - Test missing get_transcripts raises clear TODO error

---

## BU039

Summary:
Added safe read-only database search methods for transcript and summary search across sessions. These methods use parameterized SQL queries to prevent injection and return results with session metadata for context resolution.

Files Changed:

- src/storage/database.py - Added search_transcripts(), search_summaries(), find_sessions() methods
- tests/test_database_search.py - New test file with 13 tests

Important Decisions:

- Used parameterized LIKE queries (not raw SQL from model input)
- All search methods are read-only - no INSERT/UPDATE/DELETE operations
- Results include session_name for context/resolver needs
- get_transcripts(session_id) already existed from prior BU

Recovery Notes:

- BU039 blocks BU040 and BU041
- No UI changes or assistant service logic included (out of scope)
- No embeddings/vector database implemented (out of scope)

---

## BU040 - Assistant Session Resolver

Summary:
Created AssistantSessionResolver class to infer session scope from user questions. Resolves whether a question targets current session, selected session, all sessions, or returns ambiguous/needs_clarification when inference is not possible.

Files Changed:

- src/assistant/session_resolver.py (new)
- src/assistant/**init**.py (updated exports)
- tests/test_session_resolver.py (new, 18 tests)

Important Decisions:

- ScopeResolution enum with 5 outcomes: CURRENT_SESSION, SELECTED_SESSION, ALL_SESSIONS, AMBIGUOUS, NEEDS_CLARIFICATION
- Cross-session patterns detected first (all sessions, previous sessions, compare, where did we discuss)
- Database search via find_sessions for keyword-based session inference
- Stop word filtering for search term extraction
- Resolver never guesses when multiple candidates exist - returns AMBIGUOUS with candidates

Definition of Done Satisfied:

- [x] Resolver returns explicit structured outcomes
- [x] Resolver never guesses when candidates are ambiguous
- [x] Cross-session intent is detected before single-session inference
- [x] Behavior is deterministic for active/selected/any scope inputs

Validation:

- All 18 unit tests pass

Recovery Notes:

- Ready for BU041 (Whitelisted Assistant Retrieval Tools)
- No OpenRouter calls, UI code, or SQL outside database methods (out of scope)

---

## BU041 - Whitelisted Assistant Retrieval Tools

Summary:
Created AssistantRetrievalTools class with whitelisted retrieval methods for assistant queries. Provides controlled access to session data without exposing raw database or SQL execution capabilities.

Files Changed:

- src/assistant/tools.py (new)
- tests/test_assistant_tools.py (new, 37 tests)

Important Decisions:

- Created 5 tool methods: find_sessions, get_session_context, search_transcripts, search_summaries, get_screenshots_near
- All methods validate inputs and raise ValueError for empty queries or invalid parameters
- Limits are capped at maximum values (50) to prevent oversized outputs
- Methods delegate to AssistantContextRetriever or Database methods - no raw SQL execution
- Error handling returns error dict instead of raising to allow graceful degradation

Definition of Done Satisfied:

- [x] Tool methods are importable and deterministic
- [x] Tools cover explicit session and cross-session retrieval
- [x] Tools enforce bounded query limits
- [x] No raw SQL execution path exists

Validation:

- All 37 unit tests pass:
  - Test each tool delegates to fake db/retriever
  - Test empty query handling raises ValueError
  - Test max limit is capped
  - Test invalid session_id/timestamp raises ValueError
  - Test exception handling returns error dict

Recovery Notes:

- Ready for BU042
- No UI code, OpenRouter function-calling protocol, or model-generated SQL (out of scope)

# BU042 Implementation Summary (Completed)

## Summary:

Created AssistantAnswerService in src/assistant/service.py that coordinates session resolution, context retrieval, OpenRouter answering, and conversation persistence.

## Files Changed:

- src/assistant/service.py (new file)
- src/assistant/**init**.py (updated exports)
- tests/test_assistant_service.py (new file)

## Important Decisions:

- Single ask() entrypoint hides retrieval, ambiguity handling, and OpenRouter complexity from UI
- Ambiguous cases return clarification WITHOUT calling OpenRouter (saves API calls)
- Single-session uses get_session_context for bounded context
- All-session uses search tools for cross-session context
- Conversation persistence via existing database create_conversation/add_message methods

## Implementation Details:

- ask(question, agent_id, explicit_scope, active_session_id, selected_session_id, conversation_id) -> AnswerResponse
- Uses AssistantSessionResolver to determine scope (current_session, selected_session, all_sessions, ambiguous, needs_clarification)
- Returns structured AnswerResponse with success/clarification/error states
- Context converted to prompt text using \_dict_to_context_prompt()
- Messages built with system prompt (agent instruction + context), history, and current question
- Persists user/assistant messages to database after successful answer

## Definition of Done Satisfied:

- [x] Service has one ask entrypoint for UI wiring
- [x] Ambiguous session cases ask clarification instead of guessing
- [x] Single-session and any-session scopes both produce answers
- [x] Conversation persistence works through existing database methods

## Validation:

- All 10 unit tests pass:
  - Test ambiguous resolver output returns clarification without OpenRouter call
  - Test single-session path calls context retrieval and OpenRouter client
  - Test all-session path uses search tools
  - Test messages are saved after successful answer

## Recovery Notes:

- Ready for BU043 (blocks BU044, BU045, BU047)
- No UI code, database schema changes, streaming, or screenshot image upload (out of scope)

---

## BU043 - Assistant UI Panel Skeleton

Summary:
Added an Assistant UI panel skeleton to the main window with agent selector, scope control, question input, Ask/Detach buttons, and read-only answer display.

Files Changed:

- src/app/window.py

Important Decisions:

- Agent dropdown populated from ASSISTANT_AGENTS config (chronicle_assistant, concise_helper)
- Scope control provides two options: "Current Session" and "Any Session"
- Ask and Detach buttons have placeholder handlers that display status messages
- Panel visually separate from live transcription controls (on left side)

Definition of Done Satisfied:

- [x] Assistant panel appears in the UI
- [x] Agent selector shows configured agents
- [x] Scope control clearly distinguishes Current Session from Any Session
- [x] Ask and Detach controls exist but do not perform backend actions yet

Recovery Notes:

- Ready for BU044 (Assistant Service Wiring)
- No service wiring, OpenRouter calls, or database queries per BU043 scope
- Buttons show status messages indicating they are not yet wired

---

## BU044 - Wire Assistant Ask Action

Summary:
Connected the assistant UI Ask button to AssistantAnswerService. The assistant panel is now fully functional - users can ask questions about their sessions and receive answers.

Files Changed:

- src/app/window.py

Important Decisions:

- Imported AssistantAnswerService and added to window.py
- Instantiated service in \_init_session_manager with session_manager.db
- \_on_ask_clicked reads question, agent_id, and scope (current_session/any_session)
- Gets active_session_id from active session if recording, selected_session_id from table selection
- Uses QTimer.singleShot to run query in background for UI responsiveness
- Answer/clarification/error displayed in answer_display QTextEdit
- Ask button disabled during processing, re-enabled in finally block

Definition of Done Satisfied:

- [x] Ask button calls AssistantAnswerService with correct scope
- [x] Current Session scope passes active/selected session context
- [x] Any Session scope passes explicit all-session intent
- [x] Errors are shown in the assistant answer area without crashing UI

Recovery Notes:

- Ready for BU045 (next)
- No detachable behavior implemented (out of scope per BU044)
- No past conversation browser (out of scope)

---

## BU045 - Assistant Clarification Flow UI

Summary:
Added UI support for handling ambiguous session responses from the assistant. When the resolver finds multiple possible sessions, the assistant now displays candidate sessions in the UI for user selection instead of silently guessing.

Files Changed:

- src/app/window.py

Important Decisions:

- Added QAbstractItemView import for selection mode
- Added candidate selection UI (QGroupBox with QListWidget and "Use Selected Session" button)
- Added \_display_candidates() to show session candidates with name and date
- Added \_clear_candidates() to hide candidate UI when no longer needed
- Added \_on_candidate_selected() to enable button when user selects a candidate
- Added \_on_use_candidate_clicked() to retry question with selected session
- Candidates cleared on: new question, successful answer, or error
- Original question stored in \_current_question for retry after selection

Definition of Done Satisfied:

- [x] Ambiguous responses show candidate session choices
- [x] Choosing a candidate retries the question against that session
- [x] The UI does not silently pick among multiple candidates
- [x] Candidate UI is cleared when no longer relevant

Recovery Notes:

- Ready for BU046 (next)
- No resolver changes per BU045 scope
- No conversation history browser (out of scope)
- No multi-turn tool loop (out of scope)

# BU048 - Session-Scoped Conversation Persistence

Summary:
Added database methods to persist assistant conversation turns independently from transcript storage. This enables follow-up questions that maintain chat context.

Files Changed:

- src/storage/database.py

Implementation:

- Confirmed existing assistant_conversations and assistant_messages schema already supports session_id, role, content, and timestamps
- Added get_recent_messages(conversation_id, limit) - retrieves recent messages with a limit
- Added get_conversation_for_session(session_id) - finds the most recent conversation for a session
- Added get_or_create_conversation(session_id, title) - gets existing conversation or creates new one

Validation:

- Created test script verifying: conversation creation, message storage, message retrieval with limits, session-conversation mapping
- All tests passed successfully
- No syntax or runtime errors

Definition of Done Satisfied:

- [x] Database exposes minimal conversation persistence methods
- [x] Messages can be stored without transcript content
- [x] Messages can be retrieved by session in stable order
- [x] No schema unrelated to assistant conversation is changed

Next:

- Ready for BU049

---

## BU049 - Assistant Context Loader

Summary:
Added conversation history loading to the assistant context. This enables follow-up questions to include prior chat turns in the prompt context.

Files Changed:

- src/assistant/context_models.py (added ConversationTurn dataclass, conversation_history field)
- src/assistant/context.py (added \_get_conversation_history method, updated build_session_context)
- src/assistant/tools.py (updated get_session_context to accept conversation_id)
- src/assistant/service.py (updated \_get_single_session_context to pass conversation_id)

Important Decisions:

- Default limit of 10 conversation turns to bound context size
- Empty conversation history handled gracefully (returns empty list, no error)
- Conversation history rendered first in prompt context for highest relevance
- Uses existing database get_messages method from BU048

Definition of Done Satisfied:

- [x] Recent assistant turns are available to prompt construction
- [x] Context size is bounded (10 turns)
- [x] No transcript or summary storage behavior changes
- [x] Empty history is handled safely

Validation:

- All 37 assistant tools tests pass
- All 12 context retriever tests pass
- Import validation successful

Next:

- Ready for BU050

---

## BU050 - Assistant Conversation Writeback

Summary:
Verified that the assistant answer service already persists user questions and assistant answers after successful responses. This was implemented in BU042/BU049 - the `_persist_conversation` method creates conversations when needed, adds user questions and assistant answers, and is only called after successful OpenRouter API calls.

Files Changed:

- src/assistant/service.py (already implemented - verified existing code)

Important Decisions:

- Conversation persistence happens after successful answer generation only
- Failed or empty responses are NOT persisted (method is only called after successful OpenRouter call)
- Uses existing database create_conversation and add_message methods
- Continues existing conversations if conversation_id is provided

Definition of Done Satisfied:

- [x] User and assistant turns are persisted per session
- [x] Failed responses do not create misleading history
- [x] Existing assistant answer flow still works
- [x] No unrelated assistant behavior changes

Validation:

- All 10 unit tests pass
- Implementation verified via code review of \_persist_conversation method

Next:

- Ready for BU051

---

## BU050-1 - New Chat Button

Summary:
Added a "New Chat" button to the assistant panel that resets the conversation context, allowing users to start fresh conversations without previous context.

Files Changed:

- src/app/window.py (added \_current_conversation_id state, \_on_new_chat_clicked method, New Chat buttons in main and detached windows)

Important Decisions:

- Button placed next to "Ask" button for easy access
- Clicking clears: conversation_id, question input, answer display, and candidate UI
- Status message confirms new conversation started
- Both main window and detached assistant window have the button

Definition of Done Satisfied:

- [x] New Chat button appears in assistant panel
- [x] Clicking clears current conversation context
- [x] Next question starts fresh conversation

Validation:

- Python syntax check passed (py_compile)
- Existing tests still pass

Next:

- Ready for BU051

---

## BU051 - Automatic Summary Setting

Summary:
Added a persisted setting to control whether a summary is generated after a session stops. The setting is stored in the SESSION configuration dictionary with a default value of False.

Files Changed:

- src/config.py (added SESSION config dict with auto_summary_after_stop setting)

Important Decisions:

- Default value is False (no auto-summary on stop)
- Setting accessible via SESSION['auto_summary_after_stop']
- No UI, database, or session lifecycle changes (out of scope per BU specification)

Definition of Done Satisfied:

- [x] Auto-summary default exists (False)
- [x] Setting is accessible from app code
- [x] No session lifecycle behavior changes
- [x] No model calls added

Validation:

- Setting can be imported: `from src.config import SESSION`
- Value verified: SESSION['auto_summary_after_stop'] returns False

Next:

- Ready for BU052

---

## BU052 - Auto Summary Toggle Button

Summary:
Added a UI toggle/checkbox to control automatic summary generation after session stops. The checkbox is initialized from the SESSION config setting and allows users to enable/disable auto-summary without code changes.

Files Changed:

- src/app/window.py (added import for SESSION, added auto_summary_checkbox widget)

Important Decisions:

- Checkbox placed after live transcription checkbox for logical grouping
- Initial state reflects config default (False)
- Checkbox uses QCheckBox widget with descriptive label

Definition of Done Satisfied:

- [x] Auto Summary control is visible
- [x] Control reflects persisted/default setting
- [x] Toggling does not break existing session controls
- [x] Manual summarization still works

Validation:

- Python syntax check passed (py_compile)
- No runtime errors in import

Next:

- Ready for BU053

---

## BU053 - Auto Summary On Stop

Summary:
Implemented auto-summary on session stop. Modified session_manager.py to automatically generate summary when a session stops and the auto-summary setting is enabled. Also connected the UI checkbox to update the config.

Files Changed:

- src/app/session_manager.py
- src/app/window.py

Important Decisions:

- Added \_auto_generate_summary method that checks for existing summaries and transcripts before generating
- Connected auto_summary_checkbox.toggled signal to update SESSION config dictionary
- Added summary status verification in \_load_past_sessions to fix inconsistent database state
- Updated summary_status in database after auto-summary completes

Recovery Notes:

- The checkbox was not connected to update the config, which was why auto-summary wasn't triggering
- Also fixed the UI to verify and display actual summary status from the database

---

## BU054 - Assistant Model Setting

Summary:
Added persisted model selection setting for OpenRouter. Defined ALLOWED_MODELS list with 13 compatible models, DEFAULT_MODEL constant preserving current behavior, and get_selected_model()/set_selected_model() functions with validation.

Files Changed:

- src/config.py

Important Decisions:

- Default model set to google/gemini-2.5-flash to preserve existing behavior
- set_selected_model() returns False for invalid model IDs instead of raising exception
- Models include Google, DeepSeek, Anthropic, OpenAI, Meta Llama, and Mistral providers

Recovery Notes:

- This is a backend-only change; UI will be added in future BUs (BU055, BU056)

---

## BU055 - Model Selector UI

Summary:
Added a model selector UI to the application via Settings > Model Settings. The dialog allows users to select from the allowed models list and persists the selection in memory.

Files Changed:

- src/app/window.py

Important Decisions:

- Added ALLOWED_MODELS, get_selected_model, set_selected_model to imports from config
- Added "Model Settings..." menu item to Settings menu
- Implemented \_show_model_settings() dialog with QComboBox populated from ALLOWED_MODELS
- Dialog initializes with currently selected model via get_selected_model()
- Changes are saved via set_selected_model() when user clicks OK

Definition of Done Satisfied:

- [x] Model selector is visible (via Settings > Model Settings)
- [x] Selector options come from config (ALLOWED_MODELS)
- [x] Selected value persists in memory (across dialog changes within session)
- [x] No assistant/summarization behavior changes yet (BU056 will wire this)

Next:

- Ready for BU056 (Apply Selected Model To AI Calls)

---

## BU056 - Apply Selected Model To AI Calls

Summary:
Modified the assistant OpenRouter client to use the selected model from config settings. When no explicit model is passed, the client now falls back to `get_selected_model()` from config.

Files Changed:

- src/assistant/openrouter_client.py
- src/assistant/service.py

Important Decisions:

- Priority order: explicit model parameter > instance model > selected model from config
- Preserved backward compatibility - existing callers with explicit models work unchanged
- Fixed service.py to not override with hardcoded agent model - now uses selected model by default

Definition of Done Satisfied:

- [x] AI client defaults to selected model
- [x] Explicit model override still works
- [x] Existing API shape remains stable
- [x] No unrelated request behavior changes

Validation:

- Python syntax check passed
- Import verification successful
- Model selection functions verified working

Next:

- Ready for BU057

---

## BU057 - Screenshot AI Context Field

Summary:
Added storage support for AI-generated screenshot context. Added a new `context` column to the screenshots table via migration (idempotent), and added methods to update and retrieve screenshot context.

Files Changed:

- src/storage/database.py

Important Decisions:

- Added `context` column to screenshots table with migration (uses CREATE TABLE IF NOT EXISTS + ALTER TABLE with try/except for idempotency)
- Separated from existing `description` column which serves a different purpose (user-provided descriptions)
- Added `update_screenshot_context(screenshot_id, context)` method to update context by screenshot ID
- Added `get_screenshot(screenshot_id)` method to retrieve a specific screenshot by ID (includes context)
- Existing `get_screenshots(session_id)` continues to work unchanged (returns all screenshot fields including context)

Definition of Done Satisfied:

- [x] Screenshot context can be persisted
- [x] Existing screenshot listing still works
- [x] Migration is idempotent
- [x] No AI dependency introduced

Next:

- Ready for BU058

---

## BU058 - Screenshot Context Generator

**Date:** 2026-06-03

**Goal:** Generate concise AI context for one screenshot and store it.

**Context:** This BU adds the backend action behind the screenshot-context button without changing gallery layout.

**Implemented:**

- Created `src/screenshots/context_generator.py` with:
  - `ScreenshotContextGenerator` service class
  - `ScreenshotContextGeneratorError` base exception
  - `ModelDoesNotSupportImagesError` exception for clear error messages
  - `generate_context(screenshot_path, session_metadata, transcript_excerpt, store)` - main public method
  - Vision model detection via `_model_supports_vision()` with known models and pattern matching
  - Image encoding to base64 data URI
  - Context storage via database

- Added `get_screenshot_by_filepath(filepath)` method to `src/storage/database.py` to retrieve screenshots by path

**Validation:**

- Syntax verified (no compile errors)
- Uses existing OpenRouter client from BU054
- Uses existing database method from BU057
- Vision model check returns clear error if model doesn't support images
- No external screenshot sync (uses local API only)

**Definition of Done Satisfied:**

- [x] Generator has a minimal public method
- [x] Generated context is saved locally
- [x] Unsupported AI-image capability fails clearly
- [x] No screenshot data is synced externally

**Allowed Files:**

- src/screenshots/context_generator.py
- docs/current_state.md
- docs/devlog.md

**Out of Scope:**

- Gallery UI
- Bulk context generation
- OCR pipeline
- Uploading screenshots to Notion

**Next:**

- Ready for BU059 - Screenshot Context UI Button

---

## BU059 - Screenshot Context UI Button (Fix - Selection Support)

**Date:** 2026-06-04

**Summary:**
Fixed the screenshot context UI to support selecting individual screenshots. Previously, clicking "Give Context" would generate context for all screenshots at once. Now users can:

1. Click on any screenshot in the gallery to select it
2. See the context for the selected screenshot displayed in the context panel
3. Use "Give Context to Selected" to generate context for just the selected screenshot
4. Use "Give Context to All" to generate context for all screenshots

**Files Changed:**

- src/app/window.py

**Implementation Details:**

- Added selection highlighting (blue border) when clicking on a screenshot
- Created `get_context_for_screenshot()` function to retrieve formatted context for a specific screenshot
- Created `update_selection()` function to handle visual selection state
- Replaced single "Give Context" button with two buttons:
  - "Give Context to Selected" - enabled when a screenshot is selected and summary exists
  - "Give Context to All" - enabled when screenshots and summary exist
- Updated placeholder text to reflect new workflow
- Double-click still opens fullscreen view

**Important Decisions:**

- Selected screenshot context is displayed automatically when selection changes
- "Give Context to Selected" button is enabled when user clicks on a screenshot
- Both buttons work with or without existing context (can regenerate)

**Next:**

- Ready for BU060

---

## BU060 - Pause Resume Session Lifecycle

**Date:** 2026-06-04

**Summary:**
Implemented pause/resume behavior in session lifecycle without changing UI. The Session class already had pause() and resume() methods that update status, but SessionManager didn't expose them. Added pause_session() and resume_session() methods to SessionManager that coordinate audio recording suspension/resumption while keeping all data associated with the same session_id.

**Files Changed:**

- src/app/session_manager.py

**Implementation Details:**

- Added `pause_session()` method:
  - Checks for active session
  - Stops audio recording (suspends capture)
  - Updates session status to 'paused' via Session.pause()
  - Returns boolean for success/failure
- Added `resume_session()` method:
  - Checks for paused session
  - Updates session status to 'active' via Session.resume()
  - Restarts audio recording into the same session folder
  - Returns boolean for success/failure

**Important Decisions:**

- Preserved existing Session.pause()/resume() which handle database status updates
- Audio recording stops on pause and restarts on resume - no new audio chunks created during pause
- All data (transcripts, screenshots) continues to associate with same session_id

**Next:**

- Ready for BU061 - Pause Button UI

---

## BU061 - Pause Button UI

**Date:** 2026-06-04

**Summary:**
Added pause/resume button to the active session controls in the UI. The button is enabled only while a session is active or paused, and toggles between "Pause Session" and "Resume Session" states.

**Files Changed:**

- src/app/window.py

**Implementation Details:**

- Added Session import to window.py
- Created pause_button with "Pause Session" text, placed between stop and screenshot buttons
- Added \_on_pause_resume_session() handler that:
  - Calls pause_session() when session is active, updates button to "Resume Session"
  - Calls resume_session() when session is paused, updates button to "Pause Session"
- Updated \_update_ui_state() to handle pause button state:
  - STATUS_ACTIVE: button enabled with "Pause Session" text
  - STATUS_PAUSED: button enabled with "Resume Session" text
  - STATUS_PROCESSING: button disabled
  - Otherwise: button disabled
- Also fixed _update_ui_state() to use Session.STATUS_\* constants instead of string literals

**Important Decisions:**

- Pause button placed between Stop and Screenshot buttons for logical flow
- Button text changes to indicate the action that will happen on next click
- Screenshots remain enabled during pause (pause only affects audio recording)

**Next:**

- Ready for BU062

---

## BU062 - Home Layout Shell

**Date:** 2026-06-04

**Summary:**
Refactored the home UI into a three-panel layout with left session history, center current chat/session area, and right live transcription panel.

**Files Changed:**

- src/app/window.py

**Implementation Details:**

- Created three-column layout using QHBoxLayout:
  - Left panel: Session History (Past Sessions table)
  - Center panel: Current session/chat area (title, session controls, assistant panel)
  - Right panel: Live Transcriptions (unchanged behavior)
- Changed group box title from "Past Sessions" to "Session History" for the left panel
- Center panel gets stretch factor 2 for wider display
- All existing controls remain functional and usable
- Live transcription remains available during active sessions

**Important Decisions:**

- Preserved all existing functionality - only repositioned widgets
- Center panel is wider (stretch factor 2) to accommodate session controls and assistant
- Left panel (session history) and right panel (live transcriptions) have equal width (stretch factor 1)

**Definition of Done Satisfied:**

- [x] Left session history area exists
- [x] Center chat/current session area exists
- [x] Existing controls remain usable
- [x] Live transcription remains available during active sessions

**Next:**

- Ready for BU063

---

## BU063 - Session Search Combobox

**Date:** 2026-06-04

**Summary:**
Added a session search combobox to the home UI that shows recent sessions on focus and supports filtering by name.

**Files Changed:**

- src/app/window.py

**Implementation Details:**

- Added QComboBox with editable=True above the Assistant group in the center panel
- Implemented \_load_recent_sessions() to load last 5 sessions sorted by start_time descending
- Implemented \_filter_sessions_by_name() to filter sessions by name as user types
- Connected editTextChanged signal to trigger filtering
- Connected currentIndexChanged to update \_selected_session_id on selection
- Sessions are loaded on application startup

**Important Decisions:**

- Shows up to 5 most recent sessions by default
- Text filtering is case-insensitive
- Selecting a session sets \_selected_session_id for assistant queries
- Empty selection (default option) clears the selected session

**Definition of Done Satisfied:**

- [x] Search bar is visible above chat
- [x] Recent 5 sessions appear on focus
- [x] Typing filters by session name
- [x] Selecting a session updates selected session state

**Validation:**

- Python syntax check passed (py_compile)
- No runtime errors in import (syntax validation only)

**Next:**

- Ready for BU064

# BU065 - Selected Session Scope Label

**Summary:**
Added a scope label below the session search area that displays "Scope: <session_name>" when a session is selected, providing clear feedback about which session drives assistant queries.

**Files Changed:**

- `src/app/window.py`: Added `_scope_label` widget and `_update_scope_label()` method, wired to all session selection handlers.

**Implementation Details:**

- Added `_scope_label` QLabel after session search layout in center panel
- Created `_update_scope_label()` method that:
  - Shows "Scope: <session_name>" with blue bold styling when a session is selected
  - Shows "Scope: (none)" with gray italic styling when no session is selected
  - Shows "Scope: (not found)" if session ID not found in database
- Wired scope label updates to:
  - `_on_session_completer_selected()` - when user selects from autocomplete
  - `_on_session_search_selected()` - when user selects from dropdown combobox
  - Popup item click handler in `_show_session_search_dropdown()`

**Definition of Done Satisfied:**

- [x] Scope label exists
- [x] Label updates from all existing selection paths
- [x] No selected session shows safe default
- [x] No backend scope behavior changes

**Validation:**

- Python syntax check passed (py_compile)

**Next:**

- Ready for BU066

---

## BU066 - Session Action Icon Row

**Date:** 2026-06-05

**Summary:**
Added a compact icon row above the assistant panel with four action buttons: play/stop toggle, pause/resume, screenshot, and view screenshots/view summary icons. The icons respond to session state - play/stop toggles between ▶ and ⏹, pause icon appears only during active session, screenshot enabled only during active session, and summary icon enabled only when selected session has a summary.

**Files Changed:**

- `src/app/window.py`: Added QToolButton import, created icon row with 4 buttons, added \_on_play_stop_clicked, \_on_view_screenshots_icon_clicked (delegates to existing handler), \_on_view_summary_icon_clicked, \_update_summary_icon_state, and updated \_update_ui_state for icon state management.

**Implementation Details:**

- Play/Stop button toggles between ▶ (start) and ⏹ (stop) based on session state
- Pause icon button (⏸/▶) appears only when session is active/paused
- Screenshot icon (📷) enabled only during active session
- View Screenshots icon (🖼) uses existing complete \_on_view_screenshots handler
- View Summary icon (📝) enabled only when selected/active session has summary
- Summary icon state updates when scope label changes

**Definition of Done Satisfied:**

- [x] Four icons are visible
- [x] Screenshot icon is enabled only during active session
- [x] Summary icon is enabled only when selected session has summary
- [x] Existing handlers are reused where available (view screenshots delegates to complete handler)

**Validation:**

- Python syntax check passed (py_compile)

**Next:**

- Ready for BU067

---
