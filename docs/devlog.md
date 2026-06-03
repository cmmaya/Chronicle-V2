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

- Created _create_transcription_view() method with QScrollArea and QVBoxLayout for scrollable display
- Created add_transcription_to_view() method to add styled transcription bubbles
- Mic transcriptions displayed in blue bubbles with microphone emoji
- System transcriptions displayed in green bubbles with speaker emoji
- Each bubble shows source label, timestamp, and transcription text
- Word wrapping enabled for long transcriptions
- Auto-scroll to bottom when new transcriptions are added
- Updated _append_transcription() to parse formatted text and use the new chat-like view

Recovery Notes:

- Ready for BU033
- The detach window still uses the simpler QTextEdit approach (not the chat-like view)

---
