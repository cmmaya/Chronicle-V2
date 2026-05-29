# Current State

## Execution Status
- Current BU: BU014
- Next BU: BU015

## Target Architecture

The current development effort is focused on building a UI for session management and interaction. This includes:

1.  Displaying past sessions and their status (transcribed, summarized).
2.  Allowing users to trigger transcription and summarization for past sessions.
3.  Providing a UI to view summaries and screenshots.
4.  Integrating existing backend functionality for screenshots and summarization with the UI.
5.  Configuring the summarization agent to use a specific model and custom instructions.

This work will be carried out in BUs 013-020.

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

## In Progress BUs
None

## Blocked BUs
None

## Known Issues
- The UI does not yet exist for most features. The backend functionality is largely in place, but there is no way for a user to interact with it.

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
