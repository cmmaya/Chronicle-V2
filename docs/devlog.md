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
- src/transcription/__init__.py
- src/transcription/parakeet.py
- src/transcription/processor.py
- src/storage/database.py (added transcript methods)
- requirements.txt (added parakeet-ctc==0.0.3)

Important Decisions:
- Mock mode fallback when no Parakeet model is available (allows testing without heavy model download)
- Audio preprocessing handles WAV loading, mono/stereo conversion, and resampling to 16kHz
- Timestamp extracted from audio filename (YYYYMMDD_HHMMSS format)
- Source detection based on filename pattern (_system suffix)
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
- src/app/__init__.py (updated exports)
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
- src/summarization/__init__.py (new)
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
- src/audio_capture/__init__.py (new)
- src/audio_capture/core.py (new)
- src/audio_capture/chunk.py (new)

Important Decisions:
- AudioChunk uses dataclass with ISO timestamp strings for serialization
- Metadata saved as JSON file alongside each audio chunk
- ChunkedAudioRecorder uses threading for background chunk processing
- Samples per chunk calculated as sample_rate * chunk_duration

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
- src/audio_capture/__init__.py (updated exports)

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
- Removed _mock_transcribe() method entirely
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
- src/app/window.py (added Actions column with Transcribe button, _on_transcribe_clicked, _run_transcription methods)

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
- src/summarization/generator.py (added custom_instructions parameter and _load_custom_instructions method)

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
- src/app/window.py (added Summarize action option and _on_summarize_clicked, _run_summarization methods)

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
- src/app/window.py (added _on_session_double_clicked, _show_session_summary methods, QDialog and QTextBrowser imports)

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
- src/app/window.py (added _show_session_screenshots method, _on_view_screenshots method, "View Screenshots" button, added QScrollArea and QGridLayout imports, added "View Screenshots" to context menu)

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

## BU023 - Implement Overlapping Audio Chunking

Summary:
Modified the AudioRecorder to save audio in continuous, overlapping chunks to prevent word loss at boundaries. The recorder now maintains a circular buffer and saves 10-second chunks every 9 seconds, creating a 1-second overlap between consecutive chunks.

Files Changed:
- src/audio/recorder.py

Important Decisions:
- Used deque as circular buffer to hold last 11 seconds of audio
- Chunk saving runs in a background thread that wakes every 9 seconds
- Supports both microphone (sounddevice) and system audio (parec) recording modes
- Legacy complete recording file only saved for sounddevice path (not for parec)
- Chunk filenames include timestamp and chunk index: `YYYYMMDD_HHMMSS_chunk_XXXX.wav`

Recovery Notes:
- Ready for BU024 to handle deduplication of overlapping transcriptions
- Chunks are saved to `session_path/audio/<source>/` directory
- Total chunks logged after recording stops
