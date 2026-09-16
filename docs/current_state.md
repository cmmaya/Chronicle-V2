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
BU089 - Session Router For Any Session
BU090 - Assistant Answer Contract And Handoff
BU091 - Embedding Backfill And Incremental Reindex
BU092 - Scope Switch Offer Decision And Payload
BU093 - In-Chat Scope Switch Prompt
BU095 - System Audio Capture Supervisor And Watchdog
BU096 - Delete Past Conversation From Sidebar (Right-Click)
BU097 - Keyword Search For Transcripts And Conversations
BU101 - Transcript Download Button
BU102 - Resume Stopped Session From All Sessions
BU103 - Grouped Live Transcript Bubbles
BU104 - Upload Audio File As Session
BU105 - Performance, Memory, and Threading Rework
BU106 - Screenshot Preliminary Description And Metadata Optimization
BU107 - Two-Tier Screenshot Search Engine
BU108 - Screenshot-Aware Specific Session Answers
BU109 - Screenshot Viewer Window
BU110 - Screenshot Extras: Visible-Text Search, Live-Session Viewing, Capture Hotkey

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
- Fixed (BU095 - System audio capture resilience): the loopback capture thread died ~40 min into long sessions (session_044, session_046) when the default output device changed (e.g. a call ending) and never recovered, so system-audio transcription silently stopped while mic kept working. Now (1) `SystemAudioRecorder._recording_thread` is a supervisor that rebuilds the stream on fault - re-resolving `default_speaker()` - with backoff and never dies, and (2) `DualSourceChunkedRecorder` runs a watchdog that restarts a dead/stalled recorder and reports "System audio: capture lost/restored" to the status bar via `SessionManager._on_audio_status`. Tunables in `src/config.py` `AUDIO_CAPTURE`. Live path (soundcard/PySide6 not installed in dev env) still needs a manual device-change test.
- Fixed (BU103 - Grouped live transcript bubbles): every ~10s audio chunk (`ChunkedAudioRecorder.CHUNK_DURATION`) previously produced its own bubble via `add_transcription_to_view`, slicing continuous speech into many stacked bubbles with no visible time. `window.py` now tracks one "open group" per source (`mic`/`system`) and extends the open `PixelBubble` (via the new `PixelBubble.append_text`) instead of starting a new one, as long as the gap between chunks stays under `TRANSCRIPT_PAUSE_GAP_SECONDS` (~1.5x chunk duration - a bigger gap means the existing VAD check silently dropped that window) and the group hasn't run past `TRANSCRIPT_MAX_GROUP_SECONDS` (~60s). Each bubble shows one `HH:MM` footer (reusing `pixel_widgets._SCOPE_FOOTER_RE`, relaxed to match any `"— ..."` suffix), appended chunks start their own paragraph inside the bubble (`"\n\n"` separator) so they're visually distinguishable, and a freshly-inserted bubble (only) plays a brief `QGraphicsOpacityEffect` fade-in. The live thread-safe hop uses a Qt signal (`MainWindow._live_transcription_ready`, connected with `Qt.QueuedConnection`) carrying `text`/`source`/`timestamp_start`/`timestamp_end` - `QMetaObject.invokeMethod`/`Q_ARG` was tried first but this PySide6 build silently drops arguments beyond 3, dropping every live transcript. Historical session reload uses the same grouped call. `_transcription_history` (BU101 download) still logs one full-resolution line per raw chunk.
- Changed (BU103 follow-up): live audio chunk duration lowered from 10s, tried at 2s, settled at 5s (`ChunkedAudioRecorder.CHUNK_DURATION`) after 2s chunks gave the transcription model too little audio per call and hurt quality. This is a real cadence/cost change (~2x more transcription calls than the original 10s default) touching `src/audio_capture/core.py`, outside BU103's original file scope - done at the user's explicit request as a follow-up. `DualSourceChunkedRecorder` and the `create_chunked_recorder`/`create_dual_source_recorder` factory defaults reference `ChunkedAudioRecorder.CHUNK_DURATION`/`OVERLAP_DURATION` instead of separately hardcoded literals, so there's one source of truth and `window.py`'s `TRANSCRIPT_PAUSE_GAP_SECONDS`/`TRANSCRIPT_MAX_GROUP_SECONDS` (bubbles keep growing for up to ~60s) track it automatically.
- Fixed (BU103 follow-up - invented/missing words at chunk boundaries): shorter chunks made two pre-existing quality issues more visible - words hallucinated near a chunk edge (too little acoustic context) and words dropped where a phrase got cut between chunks. `ChunkedAudioRecorder.OVERLAP_DURATION` raised from 0.5s to 1.0s (20% of the 5s chunk) so a boundary word sits fully inside the overlap window, with real audio on both sides, in at least one of the two chunks. `LiveTranscriber._deduplicate` (`src/transcription/live.py`) now matches phrases of 4+ words by character-similarity ratio (`difflib.SequenceMatcher`, thresholds 0.75-0.85) instead of requiring an exact match, since the model doesn't always transcribe the same overlapping audio identically twice - a slightly different boundary word (e.g. "its" vs "it's") previously slipped past the exact match and showed up as a leftover invented word. Phrases of 2-3 words still require an exact match (fuzzy-matching short phrases risked deleting real, non-duplicate text - see devlog). Also fixed a latent bug where a successful dedup match returned the lower-cased remainder instead of the original casing.
- Added (BU104 - Upload Audio): the All Sessions bottom bar is now [Upload Audio] [Close] (Refresh removed - the modal dialog already reloads after every action and on live-state changes). Upload decodes a WAV / iPhone Voice Memos `.m4a` (AAC or Apple Lossless; also MP3/AAC/CAF/FLAC/OGG/OPUS/OGA - OPUS/OGA covers WhatsApp voice notes) with PyAV (`av`, now in requirements.txt) via `src/audio/importer.py`, writes ~30s 16 kHz mono chunks - cut at the quietest 50 ms so words aren't split - into a new stopped session's `audio/mic` as `<start>_<end>_import.wav`, then runs the existing batch `process_transcriptions` (transcribed + RAG-indexed). The session is named after the file and dated from the recording's `creation_time` metadata, else mtime minus duration. `TranscriptionProcessor` skips its cross-chunk dedup for `_import` chunks: they don't overlap, and its fuzzy matcher (`_try_fuzzy_match`) deletes any 2+ word phrase repeated from the previous chunk - still true for batch-transcribing live recordings (only reached via "•••" → Transcribe, since stop uses live transcripts). Import and transcription now run on `SessionManager`'s background job thread (BU105) rather than the UI thread, so a long upload no longer freezes the window.
- Follow-up (BU104 - WhatsApp voice notes): `SUPPORTED_EXTENSIONS` in `src/audio/importer.py` added `.opus` and `.oga` - PyAV already decoded Ogg Opus (container format is sniffed from content, not the extension) but the upload file picker's filter excluded it. Verified against a synthesized Opus file: decodes to the same 16 kHz mono output as the other formats.
- Fixed (BU105 - Performance, memory, and threading rework): the app used 2.5-3.5 GB RAM and Stop/Pause/Close could freeze the UI for the length of a full transcribe+index+summarize pass, or worse (a second Parakeet load) on close. Root causes: live transcription ran inline inside the audio capture callback; up to three ~2.6 GB Parakeet copies (live mic, live system, a fresh batch `TranscriptionProcessor`) could be resident at once, plus one unbatched embedding call per session; and `chronicle.db` had a single shared connection with no WAL, no indexes on the hot paths, a full-table FTS rebuild on every re-index, and leftover rows for 25+ sessions deleted before `purge_session` existed. `src/transcription/worker.py` (new) is a single background queue live transcription now goes through (`ChunkedAudioRecorder`'s mic callback only copies the buffer; VAD/resample/write moved to the recorder's own loop); `src/transcription/parakeet.py` `get_shared_engine()` is the one process-wide model instance (thread-safe, `enable_cpu_mem_arena=False`, idle-unloaded after 5 min - measured 2593 MB -> 83 MB); `LiveTranscriber`'s dedup/context state is keyed per `(session_id, source)` stream instead of one shared `_last_text`. `SessionManager` (rewritten) runs finalize/transcribe/summarize on a background `_JobRunner`; `stop_session(background=True)` returns immediately and reports via `session_finalized_callback`; `close()` skips finalizing (flags `needs_finalize=1`, picked up by `finalize_pending_sessions()` on the next start) and never re-transcribes. `database.py` gained per-thread connections + WAL, `SCHEMA_VERSION`-driven migration with an automatic backup, indexes on `transcripts(session_id, timestamp)` / `assistant_messages(conversation_id, timestamp)` / etc., `rag_fts` rows keyed by chunk rowid (one session's re-index no longer rebuilds the whole FTS table), and `purge_orphans()`/`repair_interrupted_sessions()` run at every startup. `window.py`'s `_status_ready`/`_session_finalized_ready`/`_ui_callback_ready` Qt signals marshal these callbacks (now fired from worker/job threads) onto the UI thread; Transcribe/Summarize/Upload Audio moved off the UI thread via `SessionManager.submit_job`. int8 Parakeet quantization was measured (~750 MB vs ~2.6 GB) but left off by default - it changed 26-46% of words on this machine's CPU (no AVX-VNNI) versus fp32. 37 new tests plus 6 end-to-end lifecycle tests against a fake engine/recorder (no devices, no model, no network); full existing suite unaffected (409 passed, same 18 pre-existing unrelated OpenRouter-auth failures, confirmed via `git stash`). See `docs/build_plan/BU105.md` for the full breakdown. Not yet done: real hardware validation (actual mic/system-audio devices) - needs the user's machine.
- Added (BU106-BU110 - Screenshot module overhaul): the Specific Session assistant used to see screenshots only as `[Screenshot at <epoch>]: <filepath>`, with no content and no id to cite. Now (BU106) every screenshot gets a preliminary description (`screenshots.preview_description` / `preview_source`, schema v3), built without AI from the user's note plus the transcript said ±20 s around the capture (`src/screenshots/metadata.py`, refreshed lazily by `ensure_previews`, so live sessions improve as speech arrives). The vision model additionally returns a `short_description`, which replaces the auto preview, and now receives the real ±20 s window instead of "nearest 2 rows × 200 chars". (BU107) `src/screenshots/search.py` ranks a session's screenshots from previews/keywords/capture time only (Tier 1; accent-insensitive EN/ES, explicit "#42"/"captura 42" references, "what was on screen" questions) and loads full metadata for at most 3 winners in one query (Tier 2). (BU108) The Specific Session prompt carries a `#ID` index plus full details for the winners, never file paths; `SCREENSHOT_CONTRACT` makes the model end a screenshot-backed answer with "The information you asked might be contained in the screenshot: #<ID>", ids are validated (hallucinated ones stripped before persistence) and exposed as `AnswerResponse.screenshot_refs`. Any Session is unchanged. (BU109) The two ~450-line duplicate grid dialogs (plus `_show_full_image`) are replaced by one image-viewer window, `src/app/screenshot_viewer.py`, in the Summary window's pixel theme: zoom/fit/pan stage with prev/next, details panel (editable description, preview, AI summary, visible text, keywords), thumbnail filmstrip, generate context (no longer requires a summary), open folder, delete. Assistant answers get "View screenshot #ID" buttons that open it. (BU110) The viewer is non-modal and one-per-session, refreshes live when a screenshot is captured, and is hidden during a snip. It has a visible-text search (this session or all sessions, optional description/summary matching, matches emphasized). A system-wide capture hotkey (`SCREENSHOT["global_hotkey"]`, default Ctrl+Alt+S, Win32 `RegisterHotKey` via ctypes in `src/app/global_hotkey.py`) is held only while a session is live, with an in-app fallback if the combination is taken. Win+Shift+S is reserved by Windows, so it is supported through an opt-in clipboard import (`SCREENSHOT["import_clipboard_snips"]`, off by default). Not yet validated on the real app: the hotkey from another app, a Win+Shift+S clipboard import, and AI context generation against OpenRouter (checked offscreen / with a posted WM_HOTKEY only).
- Fixed (BU109/BU110 follow-up): the modal Summary window covered and blocked the non-modal screenshot viewer. The Summary is now non-modal and one per session; both go through `MainWindow._present_window` (non-modal, or modal on top when a modal dialog such as All Sessions is already open). Double-clicking a screenshot opens it alone in full screen (Esc closes it), and a "?" / F1 help window explains every button, key and label.

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
- Added search_transcripts(query, limit, session_id) for parameterized transcript search (BU097: multi-keyword AND, `\ % _` escaped, empty query -> [])
- Added search_conversations(query, limit) (BU097): per-term title/message-content match AND-ed, each conversation once with match_count + snippet, updated_at DESC; `_like_terms` helper shared with search_transcripts
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
- Any Session context metadata (`src/assistant/rag_context_builder.py`): every per-session block header is `## [S<id>] <name> (<YYYY-MM-DD HH:MM>)` via the shared `_session_header()` (date from `RoutedSession.start_time`, or the earliest result timestamp on the `build_any_session_context` fallback path). Chunk lines are `[<source> @ <YYYY-MM-DD HH:MM:SS>]: <content>` - full date, not time-of-day. The `[S<id>]` prefix is what the answer contract's `sessions` field refers to (`RESPONSE_CONTRACT` names it explicitly). Not rendered into the model context: the router match `reason`, the document `title`, and (legacy fallback) per-session transcribe/summarize status. Prompt conversation history is capped to the last `service.MAX_HISTORY_MESSAGES` (10) turns so it cannot crowd out retrieved context
- `src/rag/embeddings.py`: local multilingual ONNX embedder (`intfloat/multilingual-e5-small`, pinned by revision), loaded on the existing `onnxruntime`; only `tokenizers` added. Degrades to lexical-only routing when unavailable; never crashes
- `AnswerResponse.routed_sessions` carries the routed candidates for Any Session answers (empty for other scopes); consumed by BU090
- Answer contract (BU090): in Any Session mode only, the system prompt gets `RESPONSE_CONTRACT` (`src/assistant/response_contract.py`); the model appends a `@@CHRONICLE_META@@` JSON trailer with `intent`/`evidence`/`sessions`. `parse_answer()` strips it before display and before `_persist_conversation`, degrading to the full raw text with null metadata on any failure. `intent`/`evidence` are logged only, never rendered
- Any Session answer call uses `config.ANY_SESSION_TEMPERATURE` (0.2) for reliable trailer compliance; all other call paths keep the client default (0.7)
- `AnswerResponse` gained optional `intent`, `evidence`, `scope_used`, `candidate_sessions`, `scope_offer` (all defaulted)
- Answer-contract handoff signal (BU090, narrowed by BU093 feedback): `should_hand_off(intent, evidence)` fires on `intent == "detail"` or `evidence == "partial"`. `evidence == "none"` (the answer is in no routed session) always returns `False` - nothing to hand off to.
- Scope switch offer (BU092, reworked by BU093): `src/assistant/scope_offer.py` `build_scope_offer(routed_sessions, intent, evidence, declined_session_ids=None, cited_session_ids=None)` returns a `ScopeOffer(session_id, session_name, start_time)` when: `evidence != "none"`, there is at least one routed session, and the answer either cites a session (`cited_session_ids`, from the contract trailer's `sessions`) or `should_hand_off` fires. The session offered is the first cited session that was routed, else the top routed session. Already-declined target -> `None` (not replaced). No dominance / `SCOPE_OFFER_MARGIN` check any more - ambiguity is handled by the prompt's "choose another session" path. Pure and total: no DB access, malformed routed dicts degrade rather than raise. `_finalize_answer` passes `cited_session_ids=parsed.sessions` and populates `scope_offer` for Any Session answers only, alongside `candidate_sessions`; logged, never rendered or persisted. `AssistantAnswerService.decline_scope_offer(conversation_id, session_id)` suppresses later offers for that pair (in-memory, `None` conversation is its own bucket).
- In-chat scope switch prompt (BU093): whenever `AnswerResponse.scope_offer` is present the app inserts a `PixelScopePrompt` (`src/app/pixel_widgets.py` - PixelBubble language: pixel-cut corners, left tail, blue variant, Courier New bold; `YES` / `NO` `PixelButton`s and a "Choose another session" link) right after the answer bubble. There is **no** standalone auto-picker for Any Session any more and no handoff note text. YES -> `_on_new_chat_clicked()` starts a fresh, empty conversation (both views cleared, `_current_conversation_id` reset), then `_switch_scope_to_specific(offer.session_id)` moves the `scope_combo` to Specific Session for that meeting (detached combo / transcript panel / scope label follow via the normal `_on_scope_changed` path). Nothing is re-asked - the Any Session Q&A stays in its own conversation; the user types their next question in the blank chat already scoped to that session. (Switch offers resume once the user sets the combo back to Any Session.) NO -> `decline_scope_offer(offer, conversation_id, record_decline)` -> `AssistantAnswerService.decline_scope_offer`. "Choose another session" -> the existing candidate picker (`_display_candidates` / `_display_detached_candidates`, `handoff=True`) seeded with the routed sessions; picking one re-asks in Specific Session (`_on_use_candidate_clicked`). After YES/NO both buttons disable and the prompt collapses to a one-line record; a newer question retires any still-open prompt (`_retire_pending_scope_prompt`), so at most one is ever live. Transient: never written to `assistant_messages`, never rebuilt on conversation reload. Mirrored in the detached window with its plain `QFrame` bubble + `QPushButton`s + link, same handlers. The candidate picker still serves the original ambiguity-clarification flow unchanged.
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
- BU096: right-click a conversation in the "PAST CONVERSATIONS" sidebar (`conversations_list`) -> "Delete" -> confirm -> `db.delete_conversation` (messages then conversation, one transaction) + `AssistantAnswerService.forget_conversation` (drops the `_declined_offers` entry). Deleting the loaded conversation runs `_on_new_chat_clicked()` to blank the panel + detached window. Conversations are not RAG-indexed, so there is no chunk/FTS cleanup. Handlers: `_show_conversation_context_menu` / `_delete_conversation` in `window.py`.
- BU097: sidebar **Search Chats** opens `_open_search_dialog()` - a "Search" `QDialog` with a keyword field and Transcripts / Conversations tabs. Keywords are whitespace-split, AND-matched, case-insensitive. Transcripts tab -> `db.search_transcripts(query, limit=50)` (now multi-term: one `LIKE ? ESCAPE '\'` per term AND-ed, `\ % _` escaped, empty query -> `[]`); double-click focuses that session via `_select_session_by_id` + scope combo -> Specific Session. Conversations tab -> `db.search_conversations(query, limit=50)` (matches title OR message content per term, each conversation once, with `match_count` + `snippet`, `updated_at DESC`); double-click -> `_load_conversation(conv_id)` + selects the `conversations_list` row. `_like_terms` helper shared by both DB methods. DB errors -> `logger.error` + `QMessageBox.warning`, dialog stays open. `session_search_input` autocomplete untouched.
- BU098: the left sidebar / center workspace / right transcripts shells are mutually-exclusive "focusable panes". `PixelPanel` (`src/app/pixel_widgets.py`) gained `_active` + `set_active()` + a `clicked` Signal + a non-consuming `mousePressEvent`; when active (and not `inner`) `paintEvent` draws the border with `BORDER_BLUE_ACTIVE` (`#4A78D8`) at `pen_width=3`, everything else unchanged. `PaneFocusController` (`src/app/window.py`, pure/no-Qt) holds `active: str|None` over `("left","center","right")` and `set_active(name)` returns the changed-pane set; `resolve_pane_for_widget(widget, shells)` walks the parent chain to a shell. `MainWindow._wire_pane_focus()` (end of `_create_central_widget`) connects each shell's `clicked` and `QApplication.focusChanged` to `_set_active_pane`, which maps controller state onto the `PixelPanel`s. `_active_pane` is a read-through property; startup active pane is `"center"`. Inner panels and detached windows are untouched. No search yet - this is the substrate for BU099's `Ctrl+F`.
- BU099: `Ctrl+F` opens `PixelFindBar` (`src/app/pixel_widgets.py` - navy pixel-cut bg, cream field, `current / total` counter, ▲/▼, Courier New; signals only, debounced `query_changed`, `Esc`/`Return`/`Shift+Return` via a field event filter) at the top-right of the active pane (BU098); its width adapts in `_position_find_bar` (`max(150, min(330, host.width()-20))`, applied after `show()`) so it never spills outside the narrow left sidebar / transcripts panel. `FindController` (`src/app/window.py`, Qt-free/DB-free) is the match engine: whitespace-split AND-matched case-insensitive terms, `matches` = row indices (center/right) or conversation ids (left), `next`/`prev` wrap, `match_label()` -> `(cur, total)`. `MainWindow._wire_find_bar()` supplies three adapters - `_find_search_conversations` (wraps `db.search_conversations(..., limit=200)` in try/except -> log + `[]`), `_find_center_texts` (row `message_text`), `_find_right_texts` (new `find_text` row property from `add_transcription_to_view`). Left mode: ▲/▼ select the matching `conversations_list` item, `Return` -> `_load_conversation` + close. Center/right: current match `PixelBubble.set_highlighted(True)` (`CREAM_BORDER` 3px, via `bubble_of_row`) + `ensureWidgetVisible`, and `PixelBubble.set_match_terms(terms)` re-renders the bubble's own `QLabel` as rich text with every matched word wrapped in a highlight chip (`highlight_terms_html(text, terms, bg, fg)`, `pixel_widgets.py` - HTML-escaped, case-insensitive, longest-term-first so a short term can't shadow a longer one). The chip color is picked per-bubble by `variant` (`FIND_TERM_ON_CREAM` / `FIND_TERM_ON_BLUE`) so it always contrasts with that bubble's own fill - a same-color chip on a cream bubble (e.g. System transcript lines) would otherwise be invisible. Cleared back to plain text when the highlight moves on or the bar closes. Closing (Esc / active-pane change under the bar / `_set_active_pane` to a different pane) clears the highlight and returns focus; `resizeEvent` repositions. BU097's Search Chats dialog and detached windows untouched.
- BU101: a `download_transcript_button` (icon `icon_download.svg`) sits next to `transcript_filter_button` / `detach_transcription_button` in the transcripts panel top row. `_on_download_transcripts` writes `self._transcription_history` (the same lines shown in the panel/detached window) to `chronicle_transcript_<session_name>_<YYYYMMDD_HHMMSS>.txt` under `QStandardPaths.DownloadLocation`, naming the file from the selected/active session via `_resolve_transcript_session_name`. Empty history or a write failure surface through `_on_status_update` instead of writing a file.
- BU100: the summary window is a collapsible, scalable view instead of a flat `QTextBrowser` dump. All three entry points (`_show_session_summary` from the sessions table, `_show_summary_by_session_id` from the row context menu, `_on_view_summary_icon_clicked` from the toolbar icon) now call one builder, `MainWindow._open_summary_window(session_id, session_name)`; each keeps its own pre-checks and the builder itself returns `False` after the "No Summary" box when `db.get_summaries` is empty. `parse_summary_sections(text)` (`src/app/pixel_widgets.py`) splits the stored plain-text summary into `[(title, body)]` on the numbered headers `templates.py` emits (Overview / Key Points / Action Items / Decisions / Next Steps / Open Questions, plus aliases and markdown decoration `##`, `**`, trailing `:`). Only *known* section names break a section, so a numbered body line ("1. Decisions were deferred.") can never be read as a header. Text before the first recognized header is the model's lead-in ("Here's a comprehensive summary of the meeting:") and is **dropped**; when no header is recognized at all the whole text falls back to one `Summary` section, so off-template content is never hidden. `format_summary_body_html(body)` renders a body into the Qt rich-text subset: cream ordinals for numbered entries, cream bold titles + muted `Label:` prefixes for the `Title/Description/Due date/Responsible` action-item format with an `<hr>` between cards, and hard-wrapped model output reflowed - a plain line continues the open entry/paragraph, a blank line ends it. Markdown `**bold**` is rendered as `<b>` by `escape_with_markdown_bold(text)` (regex applied *after* HTML escaping - `**` carries nothing HTML-special, so the order is safe), against a normal-weight body font so the emphasis reads by contrast. Bodies are `AlignLeft | AlignVCenter`, with the layout centring the block inside the body card - same treatment `PixelBubble` gives chat text. `count_summary_items(body)` feeds the cream count chip in each header. `PixelCollapsibleSection` (header bar via `_SectionHeader`: chevron ▼/▶, hover/collapsed fills, click to toggle; body in a `PixelPanel(inner=True)` card) sizes to its content, so the column scrolls as one page with no nested scrollbars. Sections start expanded; "Expand all" / "Collapse all" sit in the bottom bar. Window scale: `MainWindow.SUMMARY_SCALES` (85/100/120/145/175%) driven by an A- / A+ stepper with a percent readout and Ctrl+- / Ctrl++ / Ctrl+0; the dialog is freely resizable (size grip + maximize, floor 560x400 set by the fixed-size bottom row) and both the scale (`_summary_scale_index`) and the last window size (`_summary_window_size`) persist for the rest of the app session, not across restarts. Font sizes are applied through each widget's own stylesheet, not `setFont` - `app_qss()` declares `font-size` for `QWidget` and a stylesheet font property always beats `setFont`, so `setFont` alone silently does nothing here. `pixel_mini_button(text, tooltip, width, height)` is the shared compact button used for the stepper and the expand/collapse pair.
