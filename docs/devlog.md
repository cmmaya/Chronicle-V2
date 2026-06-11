## BU071 - RAG Metadata Schema

Summary:
Added local SQLite tables required to represent searchable RAG documents and chunks. Created normalized RAG metadata tables without changing existing behavior.

Files Changed:

- src/storage/database.py (added rag_documents and rag_chunks tables with indexes)

Implementation:

- Created rag_documents table with: id, source_type, source_id, session_id, timestamp, title, content_hash, metadata_json, created_at, updated_at
- Created rag_chunks table with: id, document_id, session_id, chunk_index, content, token_count, start_timestamp, end_timestamp, metadata_json, created_at
- Added indexes for rag_documents(source_type, source_id), rag_documents(session_id), rag_chunks(document_id), rag_chunks(session_id)

Definition of Done Satisfied:

- [x] rag_documents table exists
- [x] rag_chunks table exists
- [x] required indexes exist
- [x] existing schema initialization still succeeds

Validation:

- Tables verified in SQLite
- Indexes verified: idx_rag_documents_source, idx_rag_documents_session, idx_rag_chunks_document, idx_rag_chunks_session
- Existing tables (sessions, transcripts, screenshots, summaries) still work

Next:

- Ready for BU072

## BU072 - RAG Source Upsert Helpers

Summary:
Added database helper methods for inserting or replacing normalized RAG documents and chunks.

Files Changed:

- src/storage/database.py (added upsert_rag_document and replace_rag_chunks methods)
- tests/test_database_rag.py (new test file)

Implementation:

- upsert_rag_document(source_type, source_id, session_id, timestamp, title, content_hash, metadata_json) - Uses UPDATE-then-INSERT pattern with source_type + source_id as logical identity
- replace_rag_chunks(document_id, chunks) - Atomic operation that deletes old chunks and inserts new ones in a transaction

Definition of Done Satisfied:

- [x] upsert_rag_document implemented
- [x] replace_rag_chunks implemented
- [x] upsert is idempotent by source identity
- [x] chunk replacement is deterministic
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 6 tests passing in test_database_rag.py
- upsert returns same ID when called with same source_type/source_id
- replace_rag_chunks removes old chunks before inserting new ones
- Empty chunk list removes all chunks for document

Important Decisions:

- Used UPDATE-then-INSERT pattern instead of ON CONFLICT (SQLite UPSERT not available in Python 3.11)
- Session ID is inherited from document when not provided in chunk dict

Recovery Notes:

- Methods are ready for use by index rebuild logic in subsequent BU

Next:

- BU073

## BU073 - Add RAG FTS Rebuild

Summary:
Created SQLite FTS5 virtual table for full-text search over RAG chunks and implemented rebuild method that indexes existing rag_chunks joined with rag_documents.

Files Changed:

- src/storage/database.py (added rag_fts table and rebuild_rag_fts method)
- tests/test_database_rag.py (added 3 tests for rebuild functionality)

Implementation:

- Created rag_fts FTS5 virtual table with content, source_type, session_id, document_id, chunk_id columns (source_type, session_id, document_id, chunk_id marked UNINDEXED)
- Implemented rebuild_rag_fts() that deletes existing FTS rows and inserts all rag_chunks joined to rag_documents
- Empty rebuild (zero chunks) succeeds without error

Definition of Done Satisfied:

- [x] rag_fts table exists
- [x] rebuild_rag_fts indexes chunks
- [x] rebuild is idempotent
- [x] empty rebuild succeeds
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 9 tests passing in test_database_rag.py
- rebuild_rag_fts correctly indexes content from chunks
- Calling rebuild twice produces same row count (no duplicates)
- Empty chunks case succeeds without error

Next:
- BU074

## BU074 - Add Unified FTS Search

Summary:
Added search_rag_fts method for full-text search over indexed RAG chunks with optional filtering by session and source type.

Files Changed:

- src/storage/database.py (added search_rag_fts method)
- tests/test_database_rag.py (added 7 tests for search functionality)

Implementation:

- search_rag_fts(query, limit=20, session_id=None, source_types=None) - Returns source-aware results with BM25 ranking
- Query validation: raises DatabaseError for empty or whitespace-only queries
- Limit capped at 50
- Optional session_id filter excludes chunks from other sessions
- Optional source_types filter excludes non-requested source types
- Returns chunk_id, document_id, source_type, source_id, session_id, timestamp, title, content, rank

Definition of Done Satisfied:

- [x] search_rag_fts implemented
- [x] query validation exists
- [x] limit cap exists
- [x] session filter works
- [x] source type filter works
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 15 tests passing in test_database_rag.py
- Empty query raises DatabaseError
- Whitespace-only query raises DatabaseError
- Limit capped at 50
- Session filter correctly excludes other sessions
- Source types filter correctly excludes non-requested sources

Important Decisions:

- Used FTS5 bm25() for ranking (lower rank = better match)
- Used parameterized SQL to prevent injection

Recovery Notes:

- Method ready for use by AssistantRetrievalTools in subsequent BU

Next:

- BU075

## BU075 - Expose Unified Search Tool

Summary:
Added search_everything method to AssistantRetrievalTools that exposes the unified RAG FTS search with validation and bounded limits.

Files Changed:

- src/assistant/tools.py (added search_everything method, extended DatabaseLike protocol)
- tests/test_assistant_tools.py (added 17 tests for search_everything)

Implementation:

- Added search_rag_fts to DatabaseLike protocol
- Added ALLOWED_SOURCE_TYPES frozenset: transcript, summary, screenshot, assistant_message
- Added DEFAULT_SEARCH_LIMIT=20, MAX_SEARCH_LIMIT=50
- search_everything validates: non-empty query, limit cap at 50, session_id validation, source_types whitelist
- Returns normalized dictionaries with chunk_id, document_id, source_type, source_id, session_id, timestamp, title, content, rank

Definition of Done Satisfied:

- [x] search_everything tool exists
- [x] tool validates inputs
- [x] tool enforces source type whitelist
- [x] tool output is normalized
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 54 tests passing in test_assistant_tools.py
- Empty query raises ValueError
- Whitespace-only query raises ValueError
- Limit capped at 50
- Invalid session_id raises ValueError
- Invalid source_type raises ValueError
- Valid source types allowed: transcript, summary, screenshot, assistant_message

Important Decisions:

- Used frozenset for source type whitelist (immutable, O(1) lookup)
- Returned normalized dict without raw SQL details

Recovery Notes:

- Method ready for use by AssistantAnswerService in subsequent BU

Next:

- BU076

## BU076 - Create RAG Result Models

Summary:
Created typed data models for normalized RAG retrieval results to reduce dictionary drift.

Files Changed:

- src/assistant/rag_models.py (new file)

Implementation:

- SourceType enum: transcript, summary, screenshot, assistant_message
- RagScope enum: current_session, any_session
- RetrievedChunk dataclass: chunk_id, document_id, source_type, source_id, session_id, timestamp, title, content, score
- RetrievalBundle dataclass: query, scope, chunks

Definition of Done Satisfied:

- [x] rag_models.py created
- [x] SourceType exists
- [x] RagScope exists
- [x] RetrievedChunk exists
- [x] RetrievalBundle exists
- [x] module imports without runtime errors
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- Module imports successfully
- All enums and dataclasses defined with correct fields

Next:

- BU077

## BU077 - Add Current Session FTS Retrieval

Summary:
Added _get_rag_chunks helper method to AssistantContextRetriever for session-scoped RAG FTS search. Extended DatabaseProtocol with search_rag_fts method signature.

Files Changed:

- src/assistant/context.py (added search_rag_fts to protocol, added _get_rag_chunks method)

Implementation:

- Extended DatabaseProtocol with search_rag_fts(query, limit, session_id) method signature
- Added _get_rag_chunks(session_id, question, limit=12) helper that calls self._db.search_rag_fts
- Converts database rows into RetrievedChunk objects with proper source_type enum conversion
- Returns empty list on database errors
- build_session_context output unchanged (as required)

Definition of Done Satisfied:

- [x] _get_rag_chunks helper exists
- [x] helper is session-scoped (passes session_id filter)
- [x] helper returns normalized RetrievedChunk objects
- [x] existing build_session_context behavior unchanged
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- Module imports without runtime errors
- Syntax verification passed

Next:

- BU078

## BU078 - Replace Current Session Context Path

Summary:
Added RAG-first transcript retrieval in current-session context building, with legacy fallback when no RAG chunks exist.

Files Changed:

- src/assistant/context.py (added _get_transcripts_rag_first, _convert_rag_transcripts)
- tests/test_context_retriever.py (added search_rag_fts to MockDatabase, added 5 tests)

Implementation:

- Modified build_session_context to call _get_transcripts_rag_first instead of _get_transcripts directly
- Added _convert_rag_transcripts(session_id, session_name, chunks) to convert transcript-source RAG chunks to TranscriptExcerpt objects
- Added _get_transcripts_rag_first(session_id, session_name, question) that:
  - First calls _get_rag_chunks to retrieve FTS-indexed chunks
  - Filters to only SourceType.TRANSCRIPT chunks
  - Converts to TranscriptExcerpt if RAG chunks exist
  - Falls back to legacy _get_transcripts if no RAG chunks
- Non-transcript RAG chunks (summary, screenshot, assistant_message) are ignored
- Long transcripts truncated to MAX_TRANSCRIPT_LENGTH (500 chars)
- Screenshots near transcripts unchanged (works with whatever transcripts are used)

Definition of Done Satisfied:

- [x] current-session context uses RAG transcript chunks when available
- [x] legacy fallback remains
- [x] screenshots still attach near transcript timestamps
- [x] existing callers do not need changes
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 15 tests passing in test_context_retriever.py
- test_rag_transcript_retrieval_when_available: RAG chunks converted to transcripts
- test_rag_fallback_to_legacy_when_no_chunks: legacy transcripts used when no RAG
- test_rag_ignores_non_transcript_chunks: summary/screenshot chunks filtered
- test_rag_transcript_truncation: long RAG transcripts truncated
- test_screenshots_near_transcripts: unchanged behavior verified

Important Decisions:

- RAG-first approach prioritizes FTS relevance over keyword matching
- Fallback ensures backward compatibility when RAG index is empty
- Source type derived from chunk.title (microphone/system) for transcript chunks

Recovery Notes:

- Implementation ready for BU080 (blocks BU080 per build plan)

Next:

- BU079

## BU079 - Add Any Session Context Builder

Summary:
Created rag_context_builder.py with build_any_session_context function for building compact context from any-session RAG search results grouped by session.

Files Changed:

- src/assistant/rag_context_builder.py (new file)
- tests/test_rag_context_builder.py (new test file)

Implementation:

- build_any_session_context(query, results, max_chars=12000) groups results by session_id
- Each result includes source_type, timestamp (HH:MM:SS), title (if available), and truncated content
- Respects max_chars by stopping before exceeding limit
- Returns "(No relevant context found in any session)" when results is empty

Definition of Done Satisfied:

- [x] rag_context_builder.py created
- [x] any-session context builder exists
- [x] grouping by session works
- [x] max_chars is respected
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 14 tests passing in test_rag_context_builder.py
- Empty results returns no-context message
- Results from multiple sessions grouped separately
- Long content truncated to 500 chars
- max_chars limits output length

Important Decisions:

- Uses datetime.fromtimestamp for timestamp formatting (local time)
- Content truncated at 500 chars per result
- Session header added per session group

Recovery Notes:

- Implementation ready for BU080 (blocks BU080 per build plan)

Next:

- BU080

## BU080 - Wire Any Session Service Retrieval

Summary:
Wired AssistantAnswerService to use unified search_everything with build_any_session_context for all-session context, with legacy fallback preserved.

Files Changed:

- src/assistant/service.py (imported build_any_session_context, modified _get_all_sessions_context)

Implementation:

- Imported build_any_session_context from assistant/rag_context_builder
- Modified _get_all_sessions_context to first call self._tools.search_everything(question, limit=30)
- If unified search returns results, returns build_any_session_context(question, results)
- If unified search fails or returns error, falls back to legacy _get_all_sessions_context_legacy method
- Legacy method preserved with original search_summaries and search_transcripts behavior
- ask() signature unchanged
- Conversation persistence unchanged

Definition of Done Satisfied:

- [x] _get_all_sessions_context uses search_everything first
- [x] legacy fallback remains
- [x] service ask signature unchanged
- [x] conversation persistence unchanged
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- Module imports without runtime errors
- search_everything method available on AssistantRetrievalTools
- build_any_session_context imported correctly

Important Decisions:

- Used try/except to handle search_everything errors gracefully
- Legacy fallback method named _get_all_sessions_context_legacy for clarity
- No changes to AnswerResponse shape

Next:

- BU081

## BU081 - Improve Session Resolver Search Terms

Summary:
Updated session resolver to search with multiple meaningful terms instead of only the first term. First tries full joined phrase, then individual terms, with deduplication by session_id.

Files Changed:

- src/assistant/session_resolver.py (_search_sessions method rewritten)
- tests/test_session_resolver.py (added 4 new tests)

Implementation:

- Strategy 1: Try full joined phrase first when at least 2 terms exist
- Strategy 2: Try individual terms until candidates found (up to 5)
- Deduplication by session_id using seen_ids set
- Error handling preserved (try/except blocks per search)
- Existing return type (List[SessionCandidate]) unchanged

Definition of Done Satisfied:

- [x] _search_sessions uses full phrase before individual terms
- [x] deduplication works
- [x] existing resolver output shape unchanged
- [x] errors remain contained
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 22 tests passing (18 existing + 4 new)
- test_multi_term_search_first_tries_full_phrase passes
- test_multi_term_falls_back_to_individual_terms passes
- test_deduplication_by_session_id passes
- test_db_exception_is_contained passes

Important Decisions:

- Full phrase uses first 3 extracted terms joined with spaces
- Max 5 candidates returned (matches original limit)
- Inner try/except isolates each db call to prevent cascade failures

Next:

- BU082

## BU082 - Tighten Chronicle Assistant Prompt

Summary:
Updated chronicle_assistant system instruction to reduce hallucinations by requiring the assistant to distinguish Chronicle evidence from general knowledge.

Files Changed:

- src/config.py (chronicle_assistant system_instruction)

Implementation:

- Updated prompt to recognize multiple source types: transcripts, summaries, screenshots, and assistant conversation history
- Added instruction to state when Chronicle evidence is insufficient
- Added requirement to reference session name and timestamp (HH:MM:SS) when available
- Model unchanged (google/gemini-2.5-flash)

Definition of Done Satisfied:

- [x] chronicle_assistant prompt updated
- [x] prompt recognizes multiple source types
- [x] prompt instructs insufficiency behavior
- [x] model config unchanged
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- Module imports without runtime errors
- System instruction updated as specified in BU

Next:

- BU083

## BU083 - Add Retrieval Tests

Summary:
Created focused tests for unified search tooling and any-session context building without requiring external services or a real production database.

Files Changed:

- tests/test_rag_retrieval.py (new file)

Implementation:

- Created test_rag_retrieval.py with MockDatabase for testing search_everything and build_any_session_context
- TestSearchEverythingValidation: 11 tests for input validation (empty query, whitespace query, limit capping, invalid session_id, invalid source_type)
- TestSearchEverythingFunctionality: 3 tests for core functionality (parameter passing, normalized results, error handling)
- TestBuildAnySessionContext: 8 tests for context formatting (empty results, single/multiple session grouping, source type, session identifiers, max_chars, timestamps)
- TestSearchEverythingIntegration: 1 test for full pipeline (search to context building)

Definition of Done Satisfied:

- [x] test_rag_retrieval.py created
- [x] tests cover validation and context formatting
- [x] tests avoid external services
- [x] tests pass locally (23 tests)
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- 23 tests passing
- search_everything rejects empty query
- search_everything caps limit at 50
- build_any_session_context returns no-context message for empty results
- build_any_session_context groups multiple sessions
- context includes source type metadata

Important Decisions:

- Used MockDatabase to avoid production database dependencies
- Tests use local timezone for timestamp formatting
- Context builder falls back to "Session {id}" when session_name not available

Recovery Notes:

- Implementation ready for BU084 (embedding integration)

Next:

- BU084

## BU084 - Add Local Embedding Schema Placeholder

Summary:
Added nullable embedding columns to rag_chunks table to prepare for future local embeddings without implementing semantic search.

Files Changed:

- src/storage/database.py (added embedding_model and embedding columns, added index)

Implementation:

- Added embedding_model TEXT column (nullable) to rag_chunks
- Added embedding BLOB column (nullable) to rag_chunks
- Added idx_rag_chunks_embedding_model index on rag_chunks(embedding_model)
- No embedding computation added
- Schema initialization succeeds on new database

Definition of Done Satisfied:

- [x] embedding_model column exists
- [x] embedding column exists
- [x] embedding_model index exists
- [x] no embedding computation added
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- Module imports without runtime errors
- 15 existing database RAG tests pass
- New columns verified in in-memory database
- Index verified: idx_rag_chunks_embedding_model

Important Decisions:

- Columns are nullable to support incremental migration
- No vector search or sentence-transformers added (future BU)

Recovery Notes:

- Schema placeholder ready for future embedding integration

Next:

- none

## BU085 - Implement RAG Content Indexing

Summary:
Created RAG indexer module to populate RAG tables with session transcripts and summaries. Wired session_manager and window to call indexer after transcription/summarization complete.

Files Changed:

- src/rag/__init__.py (new file)
- src/rag/indexer.py (new file)
- src/app/session_manager.py (wired indexer calls)
- src/app/window.py (wired indexer after manual summary)
- src/storage/database.py (fixed upsert_rag_document to return correct ID)
- tests/test_rag_indexer.py (new file)

Implementation:

- Created src/rag/indexer.py with index_session_content() function
  - Fetches transcripts and summaries from database
  - Upserts RAG documents for each content type
  - Chunks content using _chunk_text() helper
  - Stores chunks via replace_rag_chunks()
  - Calls rebuild_rag_fts() to make content searchable
- Wired SessionManager.stop_session() to call indexer after transcription
- Wired SessionManager.process_transcriptions() to call indexer
- Wired window._run_summarization() to call indexer after manual summary
- Fixed database.upsert_rag_document() to return correct document ID for both insert and update
- Added transcription status update even when auto_transcribe=False (for live transcription)

Definition of Done Satisfied:

- [x] src/rag/indexer.py created with session indexing logic
- [x] SessionManager calls indexer after transcription and summarization
- [x] rebuild_rag_fts() is called after content is indexed
- [x] New tests for indexer pass (15 tests)
- [x] current_state.md updated
- [x] devlog.md appended

Validation:

- All 15 tests pass
- Module imports without errors
- Database bug fix verified (upsert returns correct ID on update)

Important Decisions:

- Indexing runs after transcription/summarization completes, not during live recording
- Uses existing database connection (not thread-safe for concurrent access)
- Content hashed for deduplication

Recovery Notes:

- Clear chronicle.db and sessions/ to start fresh with RAG indexing

Next:

## UI Improvements - Agent Dropdown and Bar Width

Summary:
Updated the agent dropdown to show actual agent names ("Chronicle Assistant", "Concise Helper", "Research Helper") instead of generic "Agent" label. Added dropdown arrow indicator to all QComboBox widgets. Expanded session bar and ask bar width by 1.8x.

Files Changed:

- src/app/window.py (changed agent dropdown labels, expanded _center_control_max_width)
- src/app/pixel_theme.py (added QComboBox::down-arrow style)

Implementation:

- Modified agent_combo to use agent_info.get('label', agent_id) instead of hardcoded "Agent"
- Changed _center_control_max_width from 900 to 1620 (900 * 1.8)
- Added CSS border-based arrow using border-left/right transparent and border-top colored to create downward arrow effect

Important Decisions:

- Kept agent IDs unchanged (chronicle_assistant, concise_helper, research_helper)
- Used CSS borders for dropdown arrow to avoid external icon dependencies

Recovery Notes:

- No database changes required
- Visual change only - no functional impact on agent selection logic

## UI Log Message Refinement

Summary:
Refined UI log messages in window.py to be more concise and user-friendly. Replaced verbose, debug-like messages with shorter, more meaningful high-level status updates.

Files Changed:

- src/app/window.py

Implementation:

- Changed 'Ready' to 'App Started' in _init_session_manager
- Added 'Session Started' message in _on_start_session
- Simplified pause/resume messages to 'Session Paused' and 'Session Resumed' (removed session name)
- Changed 'Screenshot saved' to 'Screenshot Taken' in _execute_screenshot_capture
- Added 'Transcript Window Detached' in _on_detach_transcription
- Simplified transcription messages: removed 'Loading session...', 'Processing transcriptions...', verbose chunk counts
- Simplified summarization messages: removed 'Loading session...', 'Generating summary...', 'RAG indexing completed'
- Removed scope change notifications ('Scope set to active session...', 'Scope cleared - no active session')
- Removed session rename notifications ('Session renamed to...')

Important Decisions:

- Primary log messages are: 'App Started', 'Session Started', 'Session Paused', 'Session Resumed', 'Screenshot Taken', 'Transcript Window Detached'
- Error messages still displayed to user (is_error=True)
- Backend logging (logger) unchanged

Recovery Notes:

- No database changes required
- Visual change only - affects status bar and app logs widget display

- BU086 - (Optional) Add UI element to trigger re-indexing for a session
