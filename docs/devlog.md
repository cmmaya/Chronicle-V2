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

## BU086 - Fix RAG FTS MATCH Clause

Summary:
`Database.search_rag_fts` computed `fts_query = query.strip()` and never used it. The generated SQL had no `MATCH` clause, so every call returned the first N chunks by rowid with `bm25()` evaluating to -0.0 and no error raised. Because the rows looked valid, `search_everything` accepted them and `AssistantAnswerService._get_all_sessions_context` never reached its legacy fallback — the user's question was silently discarded during retrieval in both Any Session and Specific Session paths. This BU applies the MATCH.

Files Changed:

- src/storage/database.py
- tests/test_database_rag.py
- docs/current_state.md
- docs/build_plan/index.md

Implementation:

- Added module-level `sanitize_fts_query(query)`: extracts `\w+` terms (Unicode-aware, so Spanish accented words survive), drops the bare FTS5 operators AND/OR/NOT/NEAR, quotes each term, and joins them with `OR` so partial matches still rank. Every FTS5 metacharacter (`"`, `*`, `^`, `:`, `-`, parentheses) is discarded by construction rather than escaped.
- `search_rag_fts` now seeds `where_clauses` with `f.content MATCH ?` and `params` with the sanitised query, so MATCH always combines with the existing `session_id` and `source_type` filters via `AND`. The `WHERE` clause is now unconditional.
- A query whose sanitised form is empty (punctuation-only, operators-only) returns `[]` instead of falling back to an unfiltered scan. Empty/whitespace input still raises `DatabaseError`, unchanged.
- Projection, `ORDER BY rank` (ascending BM25), limit capping and the return-key contract are untouched.
- Added 13 tests: exclusion of non-matching chunks, non-zero ascending ranks with the denser chunk first, multi-term OR behaviour, punctuation/quote/operator queries not raising, punctuation-only and operator-only returning empty, session and source_type filters still applying alongside MATCH, accented-term matching, plus direct unit tests for the sanitiser.

Important Decisions:

- `f.content MATCH ?` rather than `rag_fts MATCH ?`: FTS5 auxiliary/match column syntax does not resolve through the `f` table alias (`no such column: f`), and the other rag_fts columns are UNINDEXED, so matching on `content` is equivalent. `bm25(rag_fts)` does resolve with the alias present and was left as-is.
- Terms are OR-joined, not AND-joined: with 1000-char chunks, requiring every term would return nothing for most natural-language questions. BM25 already ranks chunks containing more of the terms first.
- Sanitising by whitelist (`\w+`) rather than escaping metacharacters — there is no way for user text to reach the FTS5 parser as syntax.

Validation:

- `python -m pytest tests/test_database_rag.py -q` → 30 passed, 16 subtests passed.
- Caller suites (test_rag_retrieval, test_database_search, test_assistant_tools, test_context_retriever, test_rag_indexer, test_rag_context_builder, test_session_resolver) → all pass. The 5 failures in test_assistant_service.py are pre-existing (`No module named 'dotenv'`), confirmed identical on a stashed tree.
- Live `chronicle.db`: ranks are now non-zero and ordered ascending (e.g. -8.717 → -6.077); punctuation-only queries return 0 rows.

Recovery Notes:

- No database or schema changes. Read-only query change; no reindex required.


## BU087 - Timestamp-Preserving Transcript Chunking

Summary:
`index_session_content` concatenated every transcript row of a session into one string and split it into 1000-char chunks, so a chunk had no time anchor — the parent `rag_documents` row carried only the first transcript's timestamp. That broke the `HH:MM:SS` citation promised by the `chronicle_assistant` prompt and broke `AssistantContextRetriever._get_screenshots_near_transcripts`, which matches screenshots to transcript times within 60 seconds. Transcript chunks are now time-windowed and carry their own timestamp and audio source.

Files Changed:

- src/rag/indexer.py
- src/storage/database.py
- src/assistant/context.py
- tests/test_rag_indexer.py
- tests/test_context_retriever.py
- docs/current_state.md
- docs/build_plan/index.md

Implementation:

- Added `_chunk_transcripts(transcripts, window_seconds=60, max_chars=800)`: walks transcript rows in timestamp order and closes a window when the audio source changes, when elapsed time from the window's first row reaches `window_seconds`, or when the next row would push the window past `max_chars`. Each chunk gets `start_timestamp` (first row in the window), `end_timestamp` (last row), and `source`. A single row longer than `max_chars` is split with `_chunk_text` on the size bound; every piece keeps that row's timestamp and source.
- `index_session_content` now chunks transcripts through `_chunk_transcripts` instead of `_chunk_text` over the concatenated text, and hashes the joined chunk text so a change in the chunking scheme changes the document hash. Summary chunking is unchanged.
- `rag_chunks` gained a `source TEXT` column (in the `CREATE TABLE` plus an `ALTER TABLE` migration guarded by `sqlite3.OperationalError`, matching the existing migration style). `replace_rag_chunks` persists it.
- `search_rag_fts` now `LEFT JOIN rag_chunks rc ON rc.id = f.chunk_id` and returns `timestamp = COALESCE(rc.start_timestamp, rd.timestamp)` plus per-chunk `start_timestamp`, `end_timestamp` and `source`. Ranking, filters and existing result keys are unchanged.
- `AssistantContextRetriever._convert_rag_transcripts` reads the per-chunk source instead of inferring it from the document title, falling back to the title heuristic when the chunk has no source (pre-BU087 rows).
- Added `reindex_all_sessions(db, progress_callback=None)` so existing sessions adopt the new chunking; it returns the number of sessions indexed and reports `(done, total)` progress. No UI trigger yet — that is BU091.
- Tests: 9 new. Time-windowed chunking over several minutes yields distinct increasing timestamps; a chunk's timestamp equals its window's first row; source is preserved per chunk for microphone and system; the size bound closes a window; a long single row splits on the size bound; stored chunks carry timestamp and source for both sources; re-indexing keeps chunk counts stable; `reindex_all_sessions` re-indexes and reports progress; `_convert_rag_transcripts` carries the chunk timestamp and source.

Important Decisions:

- Chunk time lives in the existing `start_timestamp` / `end_timestamp` columns; only `source` was added. The FTS5 virtual table was left untouched — adding a column there would require dropping and recreating it, and the chunk join at search time supplies the same data.
- A window never mixes microphone and system audio. Mixing them would make a chunk's single `source` value wrong.
- Idempotency comes from `upsert_rag_document` + `replace_rag_chunks` (replace-per-document), as before. No hash-based skip was added, so a re-index always adopts the current chunking scheme.
- `RetrievedChunk` has no `source` field and `src/assistant/rag_models.py` is outside this BU's Allowed Files, so the retriever keeps a `chunk_id -> source` map populated by `_get_rag_chunks` and consumed by `_convert_rag_transcripts` in the same call path.

Validation:

- `python -m pytest tests/test_rag_indexer.py tests/test_context_retriever.py tests/test_database_rag.py tests/test_rag_retrieval.py tests/test_rag_context_builder.py tests/test_database_search.py -q` → 129 passed, 16 subtests passed. The 5 failures in test_assistant_service.py are pre-existing (`No module named 'dotenv'`), unrelated to this change.
- On a copy of the live `chronicle.db`: `reindex_all_sessions` indexed 39/39 sessions and grew rag_chunks 76 → 577 (rag_fts likewise); chunks carry increasing per-chunk timestamps and both `microphone` and `system` sources; a second run left the count at 577 (idempotent); `search_rag_fts` returns per-chunk timestamps for transcript hits and falls back to the document timestamp for summary chunks.

Recovery Notes:

- Schema change: `rag_chunks.source`. The migration is additive and runs on connect; older code ignores the column.
- Existing chunks keep working (their `source` is NULL and the retriever falls back to the title heuristic). To pick up the new chunking, run `reindex_all_sessions(db)` from `src/rag/indexer.py`.

## BU088 - Scope Mode Indicator And Specific Session Rename

Summary:
The session-scoped assistant mode is renamed "Specific Session" (display text only; the item data value stays `"current"` and the wire scope stays `"current_session"`). The previously dead `_scope_label` widget is now a live mode indicator, the main and detached scope combos are kept in sync bidirectionally with a recursion guard, the ESC reset no longer double-clears the transcript view, and every assistant answer bubble now shows which scope produced it.

Files Changed:

- src/app/window.py
- docs/build_plan/index.md
- docs/build_plan/BU088.md
- docs/current_state.md
- docs/devlog.md

Changes:

- `scope_combo` item text "Current Session" -> "Specific Session". Item data (`"current"`) and the `explicit_scope` mapping to `"current_session"` are unchanged, so `AssistantSessionResolver`, stored conversations and existing tests keep working.
- `_scope_label` is created visible and styled; `_build_center_workspace` seeds it with `Scope: Any Session - all meetings`.
- `_update_scope_label` rewritten: it reads the mode from `scope_combo.currentData()`, not from `_selected_session_id`. Shows `Scope: Specific Session - <name>` (falling back to the active session, then `(no session selected)`) or `Scope: Any Session - all meetings`. Added helper `_session_name_for_id`.
- Bidirectional combo sync: `_on_scope_changed` pushes the index to `_detached_scope_combo`; the detached combo's `currentIndexChanged` now calls new `_on_detached_scope_changed`, which pushes back to `scope_combo`. Both directions are wrapped in a `_scope_sync_guard` flag so the echo cannot recurse. The detached window is no longer force-closed on a scope change, so its combo can never be read stale by `_on_detached_ask` or the candidate-retry path.
- `_on_scope_changed` no longer force-closes the detached window; it early-returns from the transcript reload when `_clearing_scope` is set.
- `_clear_scope` (ESC) sets `_clearing_scope` around its `setCurrentIndex` calls so `_on_scope_changed` does not duplicate the transcript clear/reload; comments now read "Specific Session".
- Per-message scope marker: `_on_ask_clicked` and `_on_detached_ask_clicked` record `_last_scope_marker` via `_scope_marker_text(explicit_scope, session_id)`. The answer render paths (`_on_assistant_query_finished`, `_run_detached_assistant_query`) append `_scope_suffix()` ("- via Specific Session - <name>" / "- via Any Session") to the answer bubble text. The marker is display-only; reloaded history from the DB does not carry it because `AssistantAnswerService` is out of this BU's scope.

Validation:

- `python -m py_compile src/app/window.py` -> OK.
- No automated tests required for this BU (UI-only). Manual verification points: combo reads "Specific Session" / "Any Session"; the mode indicator is visible and updates on scope change, session selection and ESC; detached assistant combo stays in sync with the main window both ways; each new answer bubble carries a "via <scope>" marker.

Out of Scope (untouched):

- `AssistantAnswerService`, `AssistantSessionResolver`, internal scope keys, DB values, retrieval, and the BU090 handoff banner / candidate reuse.

## BU089 - Session Router For Any Session

Summary:
`AssistantSessionResolver.resolve` short-circuits on `explicit_scope == "any_session"` and returns `ALL_SESSIONS` with no session ids, so `_get_all_sessions_context` asked `search_everything` for 30 chunks and built context from an arbitrary chunk slice. That neither finds the right meeting from a paraphrased question nor scales past ~1000 meetings. BU089 adds a two-tier design: Tier-1 routes the question to a handful of sessions using one compact profile vector per session (hybrid cosine + BM25), then Tier-2 chunk search only ever runs inside routed sessions.

Files Changed:

- src/rag/embeddings.py (new)
- src/rag/router.py (new)
- src/rag/indexer.py
- src/storage/database.py
- src/assistant/service.py
- src/assistant/rag_context_builder.py
- requirements.txt
- tests/test_rag_embeddings.py (new)
- tests/test_rag_router.py (new)
- docs/build_plan/index.md, docs/build_plan/BU089.md, docs/current_state.md, docs/devlog.md

Changes:

- `src/rag/embeddings.py`: `EmbeddingService`, a lazily-loaded singleton producing 384-dim float32 L2-normalised vectors. `embed_query` / `embed_passages` apply the E5 `query: ` / `passage: ` prefixes *inside* the service so a call site cannot bypass them. `model_id` is `"<repo_id>@<revision>"`. Loads `onnx/model.onnx` + `onnx/tokenizer.json` for `intfloat/multilingual-e5-small` pinned at revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` (verified via the HF API to ship an fp32 ONNX artifact), via `hf_hub_download`, and runs it on the already-installed `onnxruntime` CPU provider. Mean-pools the last hidden state with the attention mask, then L2-normalises. Any load/inference failure sets `is_available=False` and returns `None` -- never raises. `get_embedding_service()` / `set_embedding_service()` manage the singleton (the latter is a test hook).
- requirements.txt: added `tokenizers>=0.15,<1.0` only. The `onnxruntime==1.20.1` pin is untouched and `fastembed` is not added (it can promote the pin and break Parakeet).
- `src/storage/database.py`: new `session_profiles` table (session_id PK, profile_text, keywords, embedding BLOB, embedding_model, content_hash, updated_at) + `session_profiles_fts` FTS5 mirror. Methods: `upsert_session_profile` (ON CONFLICT upsert + FTS row refresh), `get_session_profiles` (joined with session name/start_time), `get_session_profile`, `session_profiles_signature` (cheap `(count, max updated_at)` tuple for cache invalidation), `search_session_profiles_fts` (BM25 over profile text, sanitised query).
- `src/rag/indexer.py`: `extract_keywords` (frequency-ranked, EN/ES stop-word filtered, words >= 3 letters), `build_session_profile` (name + date + summary + keywords), `index_session_profile` (embeds the profile passage and upserts; skips the embed recompute when profile hash and embedding model are both unchanged; stores a NULL vector when the embedder is unavailable), `reindex_all_session_profiles`. `index_session_content` now also refreshes the profile, best-effort.
- `src/rag/router.py`: `route_sessions(db, query, k=5) -> list[RoutedSession]`. Loads the profile matrix into an in-memory `_ProfileCache` keyed by `(session_profiles_signature, embedding_model)` so it rebuilds after any profile write; `invalidate_profile_cache()` forces a reload. Score = `0.6 * cosine + 0.4 * lexical` when both are present, else whichever is available. Cosine is a brute-force `matrix @ q_vec` and is rescaled from the E5 band `[0.70, 0.90]` to `[0, 1]` (absolute, not min-max, so an unrelated question does not get handed a 1.0). Lexical is min-max normalised BM25 over content words only (the query is filtered through `extract_keywords` first, otherwise the FTS tokenizer matches articles against every profile). Candidates below `MIN_SCORE = 0.15` are dropped; each result carries `score` and a short `reason`.
- `src/assistant/rag_context_builder.py`: `build_routed_session_context(db, query, routed, max_chars=12000)` -- per routed session: a header with the match reason, the longest summary (truncated to 500), and up to 4 of that session's top chunks for the query (Tier-2, `search_rag_fts` scoped to the session). Honours the 500-char per-excerpt and 12000-char total budgets. Shared budget constants extracted.
- `src/assistant/service.py`: `_get_all_sessions_context` now calls `route_sessions` first and builds context from `build_routed_session_context`; the routed candidates are stashed on `self._last_routed_sessions` and surfaced on a new optional `AnswerResponse.routed_sessions` field (empty for every other scope) for BU090. Falls back to the previous unified/legacy search when routing yields nothing. Per-request reset so a stale routed list never leaks into a non-Any-Session response.

Validation:

- `python -m pytest tests/test_rag_embeddings.py tests/test_rag_router.py` -> 17 passed, 2 skipped when run under a partial interpreter without the ML stack (the router tests use a deterministic fake embedder injected via `set_embedding_service`). Re-run in the project `.venv312` (onnxruntime + huggingface-hub + tokenizers present): **19 passed**, including the two real-model checks -- the `intfloat/multilingual-e5-small@614241f6...` ONNX artifact downloaded from HF, loaded on the installed `onnxruntime`, and produced 384-dim float32 unit-norm vectors for both `passage: ` and `query: ` inputs. Assumed ONNX input names were correct.
- `python -m pytest tests/test_rag_indexer.py tests/test_database_rag.py tests/test_rag_retrieval.py tests/test_rag_context_builder.py tests/test_context_retriever.py tests/test_session_resolver.py tests/test_database_search.py` -> 142 passed, 16 subtests passed.
- `tests/test_assistant_service.py` -> 9 passed, 5 failed. The 5 failures are the pre-existing `No module named 'dotenv'` errors on the OpenRouter call (documented under BU087); `route_sessions` degrades cleanly on the test's `MockDatabase` (missing `session_profiles_signature` -> caught, lexical-only, then legacy fallback).
- Router test coverage: paraphrased EN question routes the billing session to the top; a Spanish question routes the Spanish-summary session to the top; an unrelated question returns no candidates; results carry score + reason; lexical-only mode (embedder unavailable) still routes correctly; the cache picks up a newly written profile; routed context stays within a tightened budget; 1000 synthetic profiles route in well under the latency budget after cache warm-up (~1-2 ms/query locally). Embedding tests: `model_id` format, 384-dim, singleton/injection, unavailable-degrades-without-raising, prefixes applied by the service, requirements.txt still pins onnxruntime and omits fastembed.

Recovery Notes:

- New tables `session_profiles` / `session_profiles_fts` are created on connect; older code ignores them.
- No corpus backfill is wired here (BU091). New or re-indexed sessions get a profile via `index_session_content`; to build profiles for the existing corpus run `reindex_all_session_profiles(db)` from `src/rag/indexer.py`.
- If `onnxruntime` / `tokenizers` / `huggingface-hub` are missing, or the model cannot be fetched, routing runs lexical-only (BM25 over profile text) and nothing crashes.

## BU090 - Assistant Answer Contract And Scope Handoff

Summary:
In Any Session mode the assistant only ever sees session-level material but answered transcript-detail questions as if it had the full transcript, with no signal to the user that it was out of its depth. BU090 makes the model self-classify its answer in the same call via a `@@CHRONICLE_META@@` trailer (no second LLM round trip), strips the trailer before display and persistence, and uses `intent`/`evidence` to offer a handoff to Specific Session - reusing the existing candidate picker rather than adding new widgets.

Files Changed:

- src/assistant/response_contract.py (new)
- src/assistant/service.py
- src/assistant/openrouter_client.py (no change needed; temperature already threadable via constructor)
- src/config.py
- src/app/window.py
- tests/test_response_contract.py (new)
- tests/test_assistant_service.py
- docs/build_plan/index.md, docs/build_plan/BU090.md, docs/current_state.md, docs/devlog.md

Changes:

- `src/assistant/response_contract.py`: `RESPONSE_CONTRACT` prompt text, `META_SENTINEL = "@@CHRONICLE_META@@"`, `ParsedAnswer(answer_text, intent, evidence, sessions)`, pure `parse_answer(raw)` and `should_hand_off(intent, evidence)`. Parser splits on the last sentinel occurrence, takes `answer_text` as the right-stripped head, parses first `{` .. last `}` of the tail with `json.loads`. Missing sentinel / malformed JSON / non-dict payload -> full raw text with null metadata, never raises. Unknown `intent`/`evidence` enum values coerce to `None`; non-int session ids are skipped.
- `src/config.py`: `ANY_SESSION_TEMPERATURE = 0.2` (named constant, tune here; do not set 0.0).
- `src/assistant/service.py`: `AnswerResponse` gained optional `intent`, `evidence`, `scope_used`, `candidate_sessions` (defaulted). `_build_messages` takes `is_any_session` and appends `RESPONSE_CONTRACT` only in that mode. `_call_openrouter` / `_call_openrouter_async` take `temperature` and pass it to the `OpenRouterClient` constructor (`None` -> client default 0.7). New `_finalize_answer` helper shared by `ask` / `ask_async`: in Any Session mode it runs `parse_answer` on the raw response, logs `intent`/`evidence`/`sessions`, persists only the stripped `answer_text`, and populates `candidate_sessions` from `self._last_routed_sessions`. Specific Session path is byte-for-byte unchanged (no contract, no parse, default temperature).
- `src/app/window.py`: module constants for the picker labels + `_HANDOFF_NOTE`. `_display_candidates(candidates, handoff=False)` and `_display_detached_candidates(candidates, handoff=False)` relabel the title and button toward "Ask in Specific Session" when `handoff`. `_clear_candidates` resets the labels. `_on_assistant_query_finished` and `_run_detached_assistant_query` success branches: when `scope_used == "any_session"` and `should_hand_off(intent, evidence)` and there are routed candidates, append `_HANDOFF_NOTE` to the answer and show the relabelled picker seeded with `response.candidate_sessions`. The main handler preserves `_current_question` across `_clear_candidates` so the pick can re-ask. `_on_detached_use_candidate` now always re-asks with `explicit_scope="current_session"` (matching `_on_use_candidate_clicked`) and passes `conversation_id` (previously omitted -> would have raised).

Validation:

- `python -m pytest tests/test_response_contract.py` -> 10 passed (well-formed split; missing sentinel; malformed JSON; double sentinel splits on last; unknown enums coerced to null; None input; handoff conditions).
- `python -m pytest tests/test_assistant_service.py::TestAnswerContractAndHandoff` -> 7 passed (contract absent in Specific / present in Any; Any Session uses `ANY_SESSION_TEMPERATURE`; Specific keeps client default; persisted assistant message has no sentinel/metadata; `intent`/`evidence`/`scope_used` surface on the response).
- `python -m pytest tests/test_assistant_service.py tests/test_response_contract.py tests/test_rag_router.py tests/test_session_resolver.py tests/test_rag_context_builder.py` -> 70 passed, 5 failed. The 5 are the pre-existing `No module named 'dotenv'` / duplicate-clarification-branch failures in `TestAssistantAnswerService` (identical on the base commit, verified via `git stash`).
- `python -c "import ast; ast.parse(open('src/app/window.py').read())"` -> ok.

Recovery Notes:

- A model that ignores the contract emits no trailer; `parse_answer` returns the full text with `intent=None` and the app behaves exactly as pre-BU090 (no handoff, answer shown as-is).
- The trailer is stripped before `_persist_conversation`, so it never re-enters the prompt as history on later turns.
- If routing yields no candidates the handoff is suppressed even when `intent == "detail"` (nothing to seed the picker with).

## BU091 - Embedding Backfill And Incremental Reindex

Summary:
BU087 (time-windowed chunking) and BU089 (session router profiles) did not rewrite the corpus indexed under the old scheme, and no chunk-level embeddings were ever computed or stored. BU091 adds per-chunk embeddings, makes `index_session_content` idempotent/resumable via content hashes, adds a one-time `backfill_corpus` orchestrator and a user-triggered "Reindex All (RAG)" action, and confirms the live index hooks refresh chunks + embeddings + profile + FTS together.

Files Changed:

- src/storage/database.py
- src/rag/indexer.py
- src/rag/migration.py (new)
- src/app/window.py
- tests/test_rag_migration.py (new)
- tests/test_rag_indexer.py (no change needed; existing idempotency tests still pass)
- src/app/session_manager.py (no change needed - all three hooks already route through index_session_content)
- docs/build_plan/index.md, docs/build_plan/BU091.md, docs/current_state.md, docs/devlog.md

Changes:

- `src/storage/database.py`: `replace_rag_chunks` now persists `embedding` / `embedding_model` from each chunk dict (previously ignored). New `get_rag_document(source_type, source_id)` returns the stored row (with `content_hash`) so the indexer can detect an unchanged document. New `count_chunks_missing_embedding(document_id, embedding_model)` returns how many of a document's chunks lack a vector for the given model (`embedding_model IS NOT ?` is null-safe), driving re-embed on a model/revision change. No schema changes - the `embedding` columns already existed.
- `src/rag/indexer.py`: new `_embed_chunks(chunks)` mutates chunk dicts in place with `embed_passages` vectors (best-effort; returns `None` when the embedder is unavailable). New `_sync_document(...)` upserts one document + chunks + chunk embeddings idempotently: skips entirely when `content_hash` is unchanged and `count_chunks_missing_embedding == 0` (or no embedder), returns whether it wrote. `index_session_content` rewritten around `_sync_document` for transcript + each summary, tracks a `changed` flag and only calls `rebuild_rag_fts()` when a chunk row actually changed, and gained a `force` param (threaded through `index_session_profile` and `reindex_all_sessions`). Removed the redundant per-call `from datetime import datetime` shadows (module already imports it).
- `src/rag/migration.py` (new): `backfill_corpus(db, progress_callback=None, force=False) -> dict`. Iterates every session through `index_session_content`, tallies `processed` / `failed`, reports `embeddings` availability up front, and swallows a single bad session rather than aborting. On `force` it also does a final `rebuild_rag_fts()`. Idempotent and resumable by construction (the per-document hash skip). Logs a clear warning when the embedder is unavailable.
- `src/app/window.py`: `RagBackfillThread(QThread)` (progress/finished/error signals) runs `backfill_corpus` off the UI thread. New Settings menu action "Reindex All (RAG)..." -> confirm dialog -> `RagBackfillThread(force=True)`, status-bar progress "Reindexing sessions d/t", completion summary, and `invalidate_profile_cache()` on finish. `closeEvent` waits for a running backfill (safe to interrupt regardless). The action is disabled while a run is in flight.
- Live hooks: `session_manager.py` lines ~389/451/589 and `window.py` (summary path) all already call `index_session_content`, which now also does chunk embeddings + profile + FTS, so stopping a session / finishing transcription / regenerating a summary keep everything in sync with no hook changes.

Validation:

- `python -m pytest tests/test_rag_migration.py` -> 6 passed: backfill produces chunks + non-null chunk vectors + one profile-with-vector per session; a second run rewrites nothing (row ids + created_at identical); an interrupted run (only session 0 indexed) resumes to full coverage and re-entry does not duplicate chunks or documents (6 docs for 3 sessions); changing the embedding model id recomputes every chunk vector; an unavailable embedder still yields chunks + FTS with NULL vectors and no crash, and a later run with a working embedder backfills the vectors; `index_session_content` (the session-stop hook) refreshes chunks + embeddings + profile + FTS for that session.
- `python -m pytest tests/test_rag_indexer.py tests/test_rag_router.py tests/test_rag_embeddings.py tests/test_rag_context_builder.py tests/test_database_rag.py tests/test_rag_retrieval.py tests/test_context_retriever.py tests/test_assistant_service.py tests/test_response_contract.py` -> 155 passed, 2 skipped, 16 subtests; 5 pre-existing `No module named 'dotenv'` failures in `TestAssistantAnswerService` unchanged (verified against base commit).
- `python -c "import ast; ast.parse(...)"` on window.py + indexer.py -> ok.

Recovery Notes:

- If the embedding model / tokenizers / hub is missing, backfill and every live reindex still build the lexical (FTS + BM25) index; chunk and profile vectors are stored NULL and any later run with a working embedder fills them in (the hash skip is bypassed for documents with missing vectors).
- "Reindex All (RAG)" in the Settings menu is the manual recovery path for a stale or corrupt index; it forces a full rewrite and FTS rebuild on a background thread.
- No `sqlite-vec` / vector DB; cosine over chunk vectors is not yet consumed for ranking (kept out of scope - "no changes to routing scoring").

## BU092 - Scope Switch Offer Decision And Payload

Summary:
BU090 detects that an Any Session answer could not reach transcript-level detail and hands the routed candidates to the UI, which shows an undirected multi-session list picker even when the router is confident about exactly one meeting. BU092 adds the missing service-side decision: when the top routed session clearly dominates, emit a single-session `ScopeOffer` on `AnswerResponse`; when it does not, return `None` and leave the BU090 picker path untouched. It also lets the app record a decline so the same session is not re-offered on every turn of a conversation. Logic and plumbing only - the in-chat prompt widget is BU093.

Files Changed:

- src/assistant/scope_offer.py (new)
- src/assistant/service.py
- src/config.py
- tests/test_scope_offer.py (new)
- tests/test_assistant_service.py
- docs/current_state.md, docs/devlog.md

Changes:

- `src/assistant/scope_offer.py` (new): `ScopeOffer` dataclass (`session_id`, `session_name`, `start_time`) and the pure `build_scope_offer(routed_sessions, intent, evidence, declined_session_ids=None) -> Optional[ScopeOffer]`. It returns an offer only when all of: `should_hand_off(intent, evidence)` (imported from `response_contract`, not restated, so the two paths cannot drift), at least one routed session with a usable `session_id`, the top session not in `declined_session_ids`, and dominance. Dominance is "only candidate" or `top.score - max(rest.score) >= SCOPE_OFFER_MARGIN`. Totality is explicit: `_score_of` coerces a missing/`None`/unparseable `score` to 0.0 and non-dict entries to 0.0, a non-dict or id-less top entry yields `None`, and a missing `session_name` falls back to `Session <id>`. No DB access, no exceptions.
- A declined top session returns `None` rather than falling through to the runner-up - offering second place after a "no" would read as the app arguing with the user.
- `src/config.py`: added `SCOPE_OFFER_MARGIN = 0.15` next to `ANY_SESSION_TEMPERATURE`, with a comment on which direction to tune it (higher = offer less often, more list picking).
- `src/assistant/service.py`: `AnswerResponse` gained `scope_offer: Optional[ScopeOffer] = None` (defaulted, so existing consumers and tests are unaffected). `_finalize_answer` calls `build_scope_offer` inside the existing `if is_any_session:` branch only, so every other scope keeps `scope_offer=None`, and still populates `candidate_sessions` exactly as BU090 did - BU093 picks which presentation to show and the list fallback stays available. The offer's session id joins the existing contract log line.
- `AssistantAnswerService.decline_scope_offer(conversation_id, session_id)` records the decline in `self._declined_offers: Dict[Optional[int], Set[int]]`; `_finalize_answer` reads it with the *incoming* `conversation_id`. Keying on the raw `conversation_id` means `None` (a brand-new conversation) is its own bucket and never collides with a real conversation's declines. In-memory only - no schema change, no persistence beyond process lifetime.
- The offer is never written into the answer text and never persisted; it travels on `AnswerResponse` and is logged only, exactly like `intent` and `evidence`.

Validation:

- `python -m pytest tests/test_scope_offer.py` -> 15 passed: single routed session yields a full-payload offer; a gap above the margin names the top session; a gap exactly at the margin still dominates; `partial` evidence alone triggers; near-tied candidates (0.50 vs 0.45) yield `None`; `intent="overview"` + `evidence="sufficient"` yields `None` even at score 0.99; empty and `None` routed lists yield `None`; a declined top session yields `None` and the runner-up is not offered; a decline of an unrelated id does not suppress; missing `score` (both at 0.0, no dominance), missing `session_name` (-> `Session 42`), unparseable score, non-dict entries, a missing `session_id` and a non-dict top entry all return `None` or a safely defaulted offer without raising; `declined_session_ids` may be omitted.
- `python -m pytest tests/test_assistant_service.py::TestScopeOffer` -> 8 passed: a dominant candidate carries both `scope_offer` and the BU090 `candidate_sessions` (both routed sessions still listed); ambiguous candidates leave `scope_offer=None` with the picker still seeded; an overview/sufficient answer carries no offer; a Specific Session answer never carries one; `AnswerResponse(success=True).scope_offer is None`; `decline_scope_offer` suppresses the next `ask` in the same conversation but not in a different one; a decline recorded against `conversation_id=None` suppresses only the brand-new-conversation bucket and leaves an existing conversation offering; the persisted assistant message contains no sentinel, no session name and no offer text.
- `python -m pytest tests/test_assistant_service.py tests/test_scope_offer.py tests/test_response_contract.py tests/test_rag_router.py -q` -> all pass except the 5 pre-existing `No module named 'dotenv'` failures in `TestAssistantAnswerService`, unchanged from before this BU.

Recovery Notes:

- If the offer fires on the wrong meeting too often, raise `SCOPE_OFFER_MARGIN` in `src/config.py`; the BU090 list picker absorbs everything that stops qualifying, so there is no behaviour cliff at any value.
- Setting `SCOPE_OFFER_MARGIN` above 1.0 effectively disables the offer for every multi-candidate answer while leaving single-candidate offers intact; to disable it entirely, stop populating `scope_offer` in `_finalize_answer`.
- Declines live only in `self._declined_offers` on the service instance. A new service instance (app restart) re-offers a previously declined session; this is deliberate - no schema change was in scope.
- `route_sessions` returning nothing (embedder unavailable, empty corpus) suppresses the offer along with the BU090 picker, exactly as before.

## BU093 - In-Chat Scope Switch Prompt

Summary:
BU092 supplies a single dominant `AnswerResponse.scope_offer` when an Any Session answer cannot reach transcript detail and the router is confident about one meeting. BU093 renders that offer as a themed in-chat prompt - "Would you like to change the scope to Specific for session: <name>, <date>?" with YES / NO buttons - so the user switches scope with one click in the conversation instead of reading the BU090 multi-session list, and declining is a recorded answer rather than an ignored panel. When an offer is present the BU090 handoff note and picker are both suppressed; when it is absent BU090 is unchanged. Mirrored in the detached assistant window.

Files Changed:

- src/assistant/scope_offer.py
- src/app/pixel_widgets.py
- src/app/window.py
- tests/test_scope_offer.py
- docs/build_plan/BU093.md, docs/current_state.md, docs/devlog.md

Changes:

- `src/assistant/scope_offer.py`: four additions, all pure and Qt-free.
  - `format_scope_offer_prompt(session_name, start_time=None) -> str` produces exactly `Would you like to change the scope to Specific for session: <name>, <YYYY-MM-DD HH:MM>?`. `_format_offer_timestamp` reuses the `%Y-%m-%d %H:%M` format `_display_candidates` already renders and returns `None` for a falsy or unparseable value (TypeError/ValueError/OSError/OverflowError), so a missing date drops the `, <date>` segment and its comma rather than printing a placeholder. Session names are interpolated verbatim - no escaping, no mangling of embedded commas or punctuation.
  - `should_show_handoff_picker(scope_used, intent, evidence, candidate_sessions, scope_offer) -> bool`: the single either/or decision - returns False whenever `scope_offer is not None`, otherwise the exact BU090 condition (`scope_used == "any_session" and should_hand_off(intent, evidence) and bool(candidate_sessions)`). Both the main and detached answer paths call it, so the handoff note and the picker can never disagree about whether an offer replaces them.
  - `accept_scope_offer(offer, question, agent_id, run_query)`: calls `run_query(question=, agent_id=, explicit_scope="current_session", active_session_id=None, selected_session_id=offer.session_id)` - the `_on_use_candidate_clicked` call shape, defined once.
  - `decline_scope_offer(offer, conversation_id, record_decline)`: calls `record_decline(conversation_id, offer.session_id)`. Module-level; distinct from `AssistantAnswerService.decline_scope_offer`, which is the `record_decline` it is given.
- `src/app/pixel_widgets.py`: new `PixelScopePrompt(QWidget)` reusing the PixelBubble language - `pixel_round_rect_path` cut corners, a left tail, `BUBBLE_BLUE` / `BUBBLE_BLUE_BORDER`, Courier New 12 bold `#FFF0BF` label - with a `QVBoxLayout` holding the label and a row of two `PixelButton`s (YES / NO). Exposes `accepted` / `declined` signals and no application logic. `_settle(record)` (on click) and `retire(record)` (on supersede) disable both buttons, hide the button row and append a one-line static record, guarded by `_answered` so a second click is inert. Added `QVBoxLayout` and `Signal` to the imports.
- `src/app/window.py`:
  - `_on_assistant_query_finished` and `_run_detached_assistant_query` now compute `hand_off` via `self._should_hand_off_to_picker(response)` (thin wrapper over `should_show_handoff_picker`); when `response.scope_offer` is set they call `_add_scope_offer_to_conversation(offer)` and skip both `_HANDOFF_NOTE` and the picker. The now-unused `should_hand_off` import was removed from window.py.
  - `_add_scope_offer_to_conversation(offer)`: retires any open prompt, builds a `PixelScopePrompt` from `format_scope_offer_prompt`, wraps it left-aligned in a row, inserts it before the `_answer_layout` stretch and scrolls to bottom (same pattern as `_add_message_to_conversation`), stores it as `_pending_scope_prompt`, then mirrors into the detached view.
  - `_add_scope_offer_to_detached_conversation(offer)`: the same prompt as a plain `QFrame` bubble (`#E3F2FD`) with two `QPushButton`s, wired to the same `_on_scope_offer_accepted` / `_on_scope_offer_declined`; kept as `_pending_detached_scope_prompt = (frame, yes, no, settle)`.
  - `_on_scope_offer_accepted(offer)`: retire prompts -> `_switch_scope_to_specific(offer.session_id)` (sets `_selected_session_id`, moves `scope_combo` to the `"current"` index through `setCurrentIndex` / `_on_scope_changed` so the detached combo, transcript panel and scope label all follow via the existing `_scope_sync_guard` path; re-runs `_on_scope_changed` directly if already on that index) -> set the scope marker -> `accept_scope_offer(...)` with `_reask_after_scope_offer` (adds a "Thinking..." bubble and runs `_run_assistant_query` on the background thread).
  - `_on_scope_offer_declined(offer)`: retire prompts -> `decline_scope_offer(offer, self._current_conversation_id, self.assistant_service.decline_scope_offer)` -> status line.
  - `_retire_pending_scope_prompt(record=...)`: collapses the live prompt in both views (RuntimeError-safe against a view already torn down); called from `_on_ask_clicked` and `_on_detached_ask_clicked` so a new question leaves at most one live offer, and from the accept/decline handlers with a matching record line.
  - `_pending_scope_prompt` / `_pending_detached_scope_prompt` initialised in `__init__` and reset wherever the answer view is rebuilt (`_clear_conversation_view`, `_on_conversation_selected`, `_on_new_chat_clicked`), so a reloaded past conversation never carries or recreates a prompt.
- The offer prompt is never passed to `_add_message_to_conversation` / `_persist_conversation`; it is UI-only and BU092 already keeps `scope_offer` out of `assistant_messages`.

Validation:

- `python -m pytest tests/test_scope_offer.py` -> 30 passed (15 pre-existing BU092 + 15 new): exact prompt sentence for a normal name + timestamp; None / 0 / "" / "not-a-timestamp" / nan / 10**20 all yield the date-less sentence with no trailing comma and no "None"; call with no `start_time`; a name containing `: , &` is present verbatim and unescaped; `accept_scope_offer` invokes the runner exactly once with `explicit_scope="current_session"` and the offered id and `active_session_id=None`; `decline_scope_offer` passes `(conversation_id, session_id)` and `(None, session_id)` for a brand-new conversation; `should_show_handoff_picker` returns False with an offer present and True without it on a detail intent, and False for non-Any-Session or empty candidates.
- `python -m pytest tests/test_scope_offer.py tests/test_assistant_service.py tests/test_response_contract.py tests/test_rag_router.py -q` -> 72 passed, 5 failed. The 5 failures are the pre-existing `No module named 'dotenv'` cases in `TestAssistantAnswerService` - identical set before and after this BU (verified with `git stash`).
- `python -m py_compile src/app/window.py src/app/pixel_widgets.py src/assistant/scope_offer.py` -> ok. `ast.parse` on all four changed files -> ok.
- PySide6 is not installed in this environment, so the widget itself was not instantiated here. Manual GUI checklist (to run where PySide6 is available) - NOT YET EXECUTED:
  1. Any Session, ask a detail question that routes to one dominant meeting -> a blue pixel prompt with a left tail appears under the answer, naming that session and its `YYYY-MM-DD HH:MM`, with YES / NO buttons; no handoff note text, no multi-session picker.
  2. Click YES -> scope combo switches to "Specific Session", the session becomes selected, the transcript panel and `Scope:` label follow, the question is re-answered against that session, and the prompt collapses to "> Scope switched to Specific Session." with disabled buttons.
  3. New offer, click NO -> prompt collapses to "> Kept Any Session."; ask another detail question about the same meeting in the same conversation -> no prompt for that session.
  4. New offer, then ask a different question without answering -> the stale prompt collapses to "> No longer offered." and only the new flow is live.
  5. Detach the assistant; repeat 1-3 in the detached window (plain light-blue bubble, plain YES/NO buttons) -> same behaviour; a prompt raised in one view is mirrored in the other and both collapse together.
  6. Reload a past conversation that had shown a prompt -> only the stored user/assistant messages render; no prompt.
  7. Ambiguous Any Session detail question (two near-tied sessions) -> the BU090 relabelled multi-session picker still appears, unchanged.

Recovery Notes:

- The either/or between the in-chat prompt and the BU090 picker is decided in one place: `should_show_handoff_picker` in `src/assistant/scope_offer.py`. If both ever show at once, that function (or a caller that stopped routing `scope_offer` into it) is the bug.
- Whether an offer is emitted at all is still BU092 (`build_scope_offer` + `SCOPE_OFFER_MARGIN` in `src/config.py`); BU093 only presents it. Raising the margin sends more cases back to the picker with no behaviour cliff.
- Declines are in-memory on the service instance (BU092); an app restart re-offers a previously declined session. Unchanged by this BU.
- The detached prompt keeps a 4-tuple `(frame, yes, no, settle)` in `_pending_detached_scope_prompt`; if the detached window is closed with a prompt open, `_retire_pending_scope_prompt` swallows the resulting `RuntimeError`.
- If PySide6 / webrtcvad / dotenv get installed, `python -m pytest tests/` should be re-run to pick up the GUI-adjacent paths; today only the pure `scope_offer` logic is under test.

### BU093 follow-up - suppress handoff when nothing was found

Feedback: an Any Session answer of the form "this is in none of the sessions" still showed the BU090 handoff note and the multi-session picker ("pick a session below"), which is pointless - there is no session to re-ask against.

Change: `should_hand_off(intent, evidence)` in `src/assistant/response_contract.py` now returns `False` whenever `evidence == "none"`, regardless of `intent`. It still fires on `intent == "detail"` or `evidence == "partial"`. This narrows brief 3.3 (which also handed off on `none`). One line, and it flows to every consumer: the BU090 note + picker, `should_show_handoff_picker` (BU093), and `build_scope_offer` (BU092) all go quiet on a `none` answer.

Files: `src/assistant/response_contract.py`, `tests/test_response_contract.py` (updated the `none` cases), `tests/test_scope_offer.py` (retargeted two BU092 tests off `none`, added `evidence="none"` suppression cases for `build_scope_offer` and `should_show_handoff_picker`), `docs/current_state.md`, `docs/devlog.md`.

Validation: `python -m pytest tests/test_response_contract.py tests/test_scope_offer.py tests/test_assistant_service.py -q` -> 66 passed, 5 failed (the pre-existing `dotenv` cases, unchanged).

### BU093 follow-up 2 - broaden the prompt and fold in the picker

Feedback: (a) when the answer identifies a session ("The discussion ... is found in Session 41" - an overview/sufficient answer), the app should still offer to switch scope, and (b) the prompt should let the user pick a different session.

Design (confirmed with the user): keep the single-session YES / NO prompt, add a "Choose another session" link that opens the existing candidate picker; the standalone auto-picker for Any Session is removed (folded into the prompt's link).

Changes:

- `src/assistant/scope_offer.py`: `build_scope_offer` reworked. New optional `cited_session_ids` param (the contract trailer's `sessions`). An offer is returned when `evidence != "none"` and there is a routed session and (`should_hand_off(intent, evidence)` OR the answer cited a session). The offered session is the first cited session that was routed, else the top routed session (`_pick_target`). Dropped the dominance / `SCOPE_OFFER_MARGIN` gate entirely - the "choose another" path covers ambiguity. Removed the now-unused `should_show_handoff_picker` and `_score_of`. Still pure/total.
- `src/assistant/service.py`: `_finalize_answer` passes `cited_session_ids=parsed.sessions` to `build_scope_offer`.
- `src/config.py`: `SCOPE_OFFER_MARGIN` marked unused (kept for reference).
- `src/app/pixel_widgets.py`: `PixelScopePrompt` gains a `choose_another` signal and a "Choose another session" link button (`allow_choose_another` hides it when there are no candidates); `_settle` also disables/hides the link.
- `src/app/window.py`: removed `_HANDOFF_NOTE`, `_should_hand_off_to_picker`, the `should_show_handoff_picker` import, and both `hand_off` branches. `_on_assistant_query_finished` / `_run_detached_assistant_query` now just: show the answer, and if `response.scope_offer` insert the prompt via `_add_scope_offer_to_conversation(offer, response.candidate_sessions)`. The prompt's `choose_another` -> `_display_candidates(candidates, handoff=True)` (main) / `_display_detached_candidates(...)` (detached). `_CANDIDATE_TITLE_HANDOFF` shortened to "ASK IN SPECIFIC SESSION".
- Consequence: the in-chat prompt now appears on most Any Session answers that name or lean on a session; it never appears when the answer is "in no session" (`evidence="none"`) or when the router returned nothing. The decline mechanism and "at most one live prompt" keep it from stacking up.

Tests:

- `tests/test_scope_offer.py` rewritten: cited-session preference, overview/sufficient-with-a-citation now offers, overview/sufficient-without-a-citation does not, `evidence="none"` never offers, near-tied candidates still yield an offer for the top (ambiguity -> "choose another"), declined target is not replaced, malformed-input totality. `format_scope_offer_prompt` / `accept_scope_offer` / `decline_scope_offer` tests unchanged. Removed the `should_show_handoff_picker` tests.
- `tests/test_response_contract.py`: `should_hand_off` `none` cases already updated in follow-up 1.
- `tests/test_assistant_service.py::TestScopeOffer`: `test_answer_carries_offer_and_candidate_sessions`, `test_near_tied_candidates_still_carry_an_offer_plus_the_full_list`, `test_overview_answer_naming_no_session_carries_no_offer`, `test_answer_not_found_in_any_session_carries_no_offer` (added `NO_SESSION_ANSWER` / `NOT_FOUND_ANSWER` fixtures, dropped unused `OVERVIEW_ANSWER`).
- `python -m pytest tests/test_scope_offer.py tests/test_response_contract.py tests/test_assistant_service.py tests/test_rag_router.py tests/test_rag_context_builder.py -q` -> 88 passed, 5 failed (the pre-existing `dotenv` cases, unchanged). `py_compile` on the four changed source files -> ok. PySide6 still not installed here; the manual GUI checklist in the first BU093 entry needs re-running with the added "Choose another session" step.

## BU095 - System Audio Capture Supervisor And Watchdog

Problem (diagnosed from Session 2026-09-09 20:59 / session_046): "transcription
stopped after ~1h". It was not a transcription limit (Parakeet is local). The
**system-audio loopback capture thread died ~42 min in** and never recovered -
no system `.wav` chunks were written after 21:41, while mic capture ran the full
2h8m. Same failure in session_044 (~46 min in). Root cause: the WASAPI loopback
stream is bound to the default speaker resolved once at thread start; when the
default output device changes (classic trigger: a Zoom/Meet call ending), the
stream goes stale. The old code either retried a dead handle forever or let the
daemon thread die silently, with `_is_recording` still True and nothing surfaced
to the UI.

Fix 1 - supervised loopback (`src/audio_capture/system_recorder.py`):
`_recording_thread` is now a supervisor loop. `_capture_once` opens a stream and
pumps frames; on a fault (N consecutive `record()` errors, or no frames for
`loopback_silent_stall_seconds`) it returns cleanly and the supervisor rebuilds -
re-resolving `default_speaker()` each time - with interruptible exponential
backoff (`loopback_backoff_initial`..`loopback_backoff_max`), reset after any
capture that ran healthily for >=30s. The thread never propagates an exception;
it exits only on `stop()`. New health surface for the watchdog:
`seconds_since_last_data()`, `is_stream_active`, `is_thread_alive`,
`restart_count`, `last_error`. `_data_buffer` access is now lock-guarded.

Fix 2 - watchdog (`src/audio_capture/core.py`):
`ChunkedAudioRecorder` gained `is_thread_alive`, `check_health(stall_seconds)`
(honours a post-start/restart grace window) and `restart()` (stops the loop,
rebuilds a fresh `SystemAudioRecorder` so the device is re-resolved, restarts the
loop in place, keeping `self.chunks` and callbacks; refuses if the old thread is
wedged, to avoid double-writing). `_last_chunk_time` is stamped on every saved
chunk. `DualSourceChunkedRecorder` takes `on_status(source, message, is_error)`
and runs a watchdog thread (`watchdog_interval_seconds`) that restarts a dead or
stalled recorder, rate-limited by `watchdog_restart_cooldown_seconds` and capped
at `watchdog_max_restarts` (then it emits a "giving up" status and stops). New
`is_healthy` / `restart_count` properties.

Wiring: `SessionManager` passes `on_status=self._on_audio_status`, which routes
capture-lost / capture-restored events through `_update_status` to the UI status
bar. Tunables live in `src/config.py` `AUDIO_CAPTURE`.

Tests: `tests/test_system_recorder_resilience.py` (8 tests, fakes `soundcard`):
supervisor rebuilds on stream fault / device-resolution failure / silent stall,
`stop()` is prompt, watchdog restarts an unhealthy recorder and emits status,
leaves healthy recorders alone, and gives up after the cap.
`python -m pytest tests/test_system_recorder_resilience.py -q` -> 8 passed.
Full stable subset -> 116 passed. `soundcard`/`webrtcvad`/`sounddevice`/PySide6
still not installed here, so the live path needs a manual run: start a session,
change the default output device mid-session (or end a call), confirm the status
bar shows "System audio: capture lost ... / capture restored" and system chunks
resume.

## Any Session context metadata tightening (follow-up to ANY_SESSION_METADATA_AUDIT.md)

Summary:
Audit (`docs/build_plan/ANY_SESSION_METADATA_AUDIT.md`) found the model never
receives a session's date or id on the two Any Session context paths that
normally fire, while the answer contract asks it to cite session ids. Directed
change: add session date + id to the context, and drop the metadata that was
adding noise rather than signal. No new retrieval behaviour.

Changes:
- `src/assistant/rag_context_builder.py`: new shared `_session_header()` ->
  `## [S<id>] <name> (<YYYY-MM-DD HH:MM>)` used by both
  `build_routed_session_context` (date from `RoutedSession.start_time`) and
  `build_any_session_context` (date from the group's earliest result
  timestamp; also now falls back to `Session <id>` instead of printing `None`).
  `_format_result` chunk lines carry the full date
  (`[<source> @ <YYYY-MM-DD HH:MM:SS>]: ...`) instead of time-of-day, and no
  longer render the document `title`. The router match `reason` is no longer
  concatenated into the routed header (it stays on `RoutedSession.reason` for
  logs / the UI candidate list).
- `src/assistant/response_contract.py`: `RESPONSE_CONTRACT` gains one line
  telling the model the `sessions` field is the `N` from each `[SN]` header, so
  `parse_answer().sessions` -> `build_scope_offer(cited_session_ids=...)` is
  fed real ids.
- `src/assistant/service.py`: `MAX_HISTORY_MESSAGES = 10`; `_build_messages`
  replays only the last 10 conversation messages into the prompt (was: all of
  them via `get_messages`). Legacy `_get_all_sessions_context_legacy` session
  list drops the `- Transcribed: .. , Summarized: ..` status suffix.

Tests:
- `tests/test_rag_context_builder.py`: title no longer rendered; header carries
  `[S<id>]` + date; chunk line timestamp carries the date; missing
  `session_name` -> `Session <id>`, never `None`.
- `tests/test_rag_router.py`: routed context header carries `[S<id>]` + the
  session date and does not leak the router `reason`.
- `tests/test_assistant_service.py`: a 40-message conversation replays exactly
  the last `MAX_HISTORY_MESSAGES` into the prompt.
- `python -m pytest tests/test_rag_context_builder.py tests/test_rag_retrieval.py tests/test_rag_router.py tests/test_response_contract.py` -> all pass.
  `tests/test_assistant_service.py` -> same 5 pre-existing `dotenv` failures as
  before, 85 pass (incl. the new history-cap test). Broader RAG/assistant
  suite (`test_database_rag`, `test_rag_indexer`, `test_rag_migration`,
  `test_context_retriever`, `test_assistant_tools`, `test_scope_offer`,
  `test_session_resolver`) -> 182 passed.

### BU093 follow-up 3 - YES on the scope switch prompt opens a blank chat scoped to the session

Summary:
Accepting the in-chat scope switch offer ("Would you like to change the scope
to Specific for session ...?") used to re-ask the question inside the same Any
Session conversation *and* move the persistent scope combo to Specific Session.
Two problems surfaced in use: (1) landing in Specific Session meant every
following question was answered scoped, so the offer never appeared again
("it never asks me"); (2) the new chat opened pre-filled with the Any Session
question re-asked. Final behaviour: YES opens a *blank* new chat with the scope
combo moved to Specific Session for the offered meeting; nothing is re-asked.
The Any Session Q&A stays in its own conversation. The user then types their
next question already scoped to that session. (To get switch offers again they
set the scope combo back to Any Session, same as any other Specific session.)

Changes:
- `src/app/window.py`:
  - `_on_scope_offer_accepted` now just: retire the live prompt ->
    `_on_new_chat_clicked()` (clears both views, resets
    `_current_conversation_id`) -> `_switch_scope_to_specific(offer.session_id)`.
    No re-ask, no `accept_scope_offer`, no question echo.
  - `_switch_scope_to_specific` restored (selects the session and moves the
    scope combo to Specific Session via the normal `_on_scope_changed` path,
    so detached combo / transcript panel / scope label follow).
  - `_reask_after_scope_offer` deleted; `accept_scope_offer` import dropped.
    `accept_scope_offer` itself stays in `scope_offer.py` (still unit-tested).
  - NO (`_on_scope_offer_declined`) unchanged.

Tests:
- None (UI-only; no Qt window harness). `python -m py_compile src/app/window.py` -> OK.
