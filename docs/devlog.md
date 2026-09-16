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

### BU096 - Delete past conversation from the sidebar (right-click)

Summary:
Added a way to permanently delete an assistant chat thread from the UI. The
"PAST CONVERSATIONS" sidebar list had no context menu and no delete path at all;
now right-click -> "Delete" -> confirm removes the conversation and every trace
of it. Scope is the assistant `assistant_conversations` / `assistant_messages`
threads, not meeting sessions (those already had `_delete_session`).

Changes:
- `src/app/window.py`: `conversations_list` gains `Qt.CustomContextMenu` +
  `customContextMenuRequested` -> `_show_conversation_context_menu` (one "Delete"
  action). `_delete_conversation(item)`: `QMessageBox.question` confirm (default
  No) -> `db.delete_conversation(conv_id)` -> `assistant_service.forget_conversation`
  (hasattr + try guarded) -> if the deleted id is `_current_conversation_id`,
  `_on_new_chat_clicked()` blanks the main + detached views and resets the id ->
  `_load_past_conversations()` + "Conversation deleted." status. DB failure ->
  `logger.error` + `QMessageBox.warning`, no partial state.
- `src/assistant/service.py`: new `forget_conversation(conversation_id)` ->
  `self._declined_offers.pop(conversation_id, None)`. No DB access; no-op for an
  unknown / None id. Single hook for any future per-conversation in-memory cache.
- `src/storage/database.py`: `delete_conversation` docstring now documents the
  messages->conversation transaction order and the unknown-id no-op. No
  behaviour change.
- Conversations are not RAG-indexed (no conversation `source_type` in
  `rag_documents`), so "any trace" is just the two tables + the service's
  in-memory declines. Past Conversations modal and Search Chats re-query
  `list_conversations()` on open, so they needed no change.

Tests (`tests/test_bu096.py`, 4 tests, DB-level - no Qt harness):
- delete cascades to `assistant_messages` (direct COUNT query) and the row is
  gone from `list_conversations()`;
- second delete of the same id, and delete of an unknown id, are silent no-ops;
- deleting one conversation leaves a sibling's rows and message order intact;
- `forget_conversation` drops the `_declined_offers` entry and is a no-op for an
  unknown / None id.

Validation:
- `python -m pytest tests/test_bu096.py -q` -> 4 passed.
- `tests/test_scope_offer.py` -> 12 passed; `tests/test_assistant_service.py` ->
  55 passed, same 5 pre-existing `dotenv` failures as BU092/BU095.
- `python -m py_compile src/app/window.py src/assistant/service.py
  src/storage/database.py` -> ok.
- PySide6 not installed here; the menu/dialog path needs a manual run.

### BU096 follow-up - right-click "Delete" menu respects the pixel theme

`_show_conversation_context_menu` created a bare `QMenu()` that rendered with the
OS default look. It now parents the menu to `conversations_list` and applies the
existing `_ALL_SESSIONS_MENU_QSS` (dark-blue ground, gold `#FFF0BF` text,
`#3E6B9B` border/selection, Courier New) - the same QSS the "All Sessions"
context menu already uses. `py_compile src/app/window.py` -> OK.

## BU097 - Keyword search for transcripts and conversations

The sidebar **Search Chats** button now opens a real "Search" dialog instead of
jumping focus to the session-name autocomplete.

DB layer (`src/storage/database.py`):
- `Database._like_terms(query)` - splits a query on whitespace and turns each
  term into a literal `LIKE` pattern, escaping `\ % _` (used with
  `ESCAPE '\'`). Empty / whitespace-only query -> `[]`.
- `search_transcripts` rewritten to AND-match every term against `t.text`
  (`LIKE ? ESCAPE '\'` chain), keeping the optional `session_id` filter, the
  `session_name` join, `ORDER BY t.timestamp DESC LIMIT ?` and the `dict(row)`
  shape. A single-word query collapses to one `LIKE`, so `assistant/tools.py`
  and `assistant/service.py` callers are unaffected. Empty query is a no-op.
- New `search_conversations(query, limit=20)` - a conversation matches when
  every term appears in the union of its `title` and its messages' `content`
  (`title LIKE ? OR EXISTS(assistant_messages ...)` per term, AND-ed). Returns
  each conversation once as its row dict plus `match_count` (messages matching
  any term) and `snippet` (first matching message trimmed to ~160 chars, else
  title, else `""`). `ORDER BY c.updated_at DESC LIMIT ?`, parameterized.

UI (`src/app/window.py`):
- `_open_search_dialog()` - `QDialog` (~640x520) with a keyword `QLineEdit`, a
  `QTabWidget` (Transcripts / Conversations), a Search button and a per-tab
  results `QListWidget`. Search runs on Return / button / tab-change (when a
  query is present); no live search. Empty query -> "Type keywords and press
  Enter"; no rows -> "No matches." (both non-selectable). Every DB call is
  wrapped -> `logger.error` + `QMessageBox.warning`, dialog stays open.
- `itemActivated`: transcript row closes the dialog, calls the extracted
  `_select_session_by_id(session_id)` and sets the scope combo to Specific
  Session; conversation row closes the dialog, calls the extracted
  `_load_conversation(conv_id)` and selects the matching `conversations_list`
  row if present.
- `_on_conversation_selected` is now a thin wrapper over `_load_conversation`;
  `_on_session_completer_selected` is a thin wrapper over
  `_select_session_by_id`. `session_search_input` / `_session_completer` /
  `_refresh_session_completer` behaviour is otherwise untouched.

Validation:
- `python -m pytest tests/test_bu097.py -q` -> 11 passed (multi-keyword AND,
  term-order independence, empty/whitespace no-op, `session_id` scoping,
  `timestamp DESC`, `limit`, wildcard/quote/injection literal-match; conversation
  message + title match, dedupe with `match_count == 3`, two-term union AND,
  unknown keyword -> `[]`, `updated_at DESC`, limit, injection).
- `tests/test_database_search.py` + `tests/test_assistant_tools.py` -> pass;
  `tests/test_assistant_service.py` -> same pre-existing `dotenv` failures only.
- PySide6 not installed here; the dialog path needs a manual run.

## BU098 - Active Pane Focus Model

The three top-level shells (left sidebar / center workspace / right transcripts)
are now one mutually-exclusive set of "focusable panes". The active pane draws a
brighter pixel-cut border; focus follows both the mouse and the keyboard.

- `src/app/pixel_widgets.py`: `PixelPanel` gains `_active` (default `False`),
  `set_active(value)` (repaints only on a real change), a `clicked = Signal()`
  class attribute, and a `mousePressEvent` that emits `clicked` on
  `Qt.LeftButton` then calls `super()` (never accepts the event - children still
  get their clicks, and a click that reaches the panel is a background click).
  `paintEvent` swaps the pen to new palette constant
  `BORDER_BLUE_ACTIVE = #4A78D8` at `pen_width = 3` when `_active and not inner`;
  fill and cut geometry unchanged, inactive/inner path byte-for-byte as before.
  No timers, no animation.
- `src/app/window.py`: `PaneFocusController` - pure, no Qt. Holds `active:
  str | None` and `PANES = ("left","center","right")`; `set_active(name)`
  returns the set of pane names whose active-ness changed (`{new}` from empty,
  `{old,new}` on a switch, `set()` when invalid or already active). This is the
  unit-tested core. `resolve_pane_for_widget(widget, shells)` walks
  `widget.parent()` upward to the first shell in `shells` (name -> widget),
  returning the pane name or `None`.
- `MainWindow._wire_pane_focus()` (called at the end of `_create_central_widget`)
  builds `self._panes`, one `PaneFocusController`, connects each shell's
  `clicked` -> `_set_active_pane(name)`, connects
  `QApplication.instance().focusChanged` -> `_on_global_focus_changed`, and sets
  the startup active pane to `"center"`. `_set_active_pane(name)` asks the
  controller and pushes `set_active(...)` into each changed `PixelPanel`.
  `_active_pane` is a read-through `@property` over `_pane_focus.active`.
  `_on_global_focus_changed(old, now)` guards `now is None`, resolves the pane
  for `now`, and activates it - so tabbing into a child keeps the visual active
  pane in sync for BU099's `Ctrl+F`.
- Inner `PixelPanel`s (history panel, transcript viewport, candidate box) are
  never in `_panes` and their paint path is unchanged. Detached windows are out
  of scope.

Validation:
- `python -m pytest tests/test_bu098.py -q` -> 6 passed. PySide6 is not
  importable in this env, so the tests extract `PaneFocusController` /
  `resolve_pane_for_widget` from `window.py` via `ast` and exec them in
  isolation (the real definitions, no Qt). Cases: set-from-empty, switch returns
  `{old,new}`, re-select -> `set()`, invalid name -> `set()` + unchanged
  `active`, parent-chain resolves to the right shell, unparented -> `None`.
- `python -m py_compile src/app/window.py src/app/pixel_widgets.py` -> OK.
- The click / `focusChanged` wiring and the brighter border need a manual run
  (no Qt here).

## BU099 - In-Pane Find Bar (Ctrl+F) With Match Navigation

`Ctrl+F` opens a small pixel-styled find bar at the top-right of the active pane
(BU098). Keyword field + `current / total` counter + ▲/▼; `Esc` closes. The
corpus depends on the active pane: left = the PAST CONVERSATIONS list
(title+message search via `db.search_conversations`, BU097; ▲/▼ step matching
conversations in the sidebar, `Return` loads the current one via
`_load_conversation`), center = the loaded assistant conversation bubbles,
right = the transcript stream bubbles (both find-in-view, no DB). Keywords are
whitespace-split, AND-matched, case-insensitive - identical to BU097.

- `src/app/pixel_widgets.py`:
  - `PixelBubble` gains `_highlighted` + `set_highlighted(value)`; when set,
    `paintEvent` draws the border with `CREAM_BORDER` at 3px regardless of
    variant (fill unchanged). `bubble_of_row(row)` returns the `PixelBubble`
    inside an `aligned_bubble` row (via `findChild`).
  - `PixelFindBar(QWidget)` - dumb UI, ~330x46, pixel-cut `NAVY` background,
    Courier New. Cream `QLineEdit` ("Find…"), a `0 / 0` `QLabel`, two
    `QToolButton` ▲/▼. Signals: `query_changed(str)` (debounced ~150 ms via a
    single-shot `QTimer` on `textChanged`), `next_match()`, `prev_match()`,
    `return_pressed()`, `closed()`. An event filter on the field maps `Esc` ->
    `closed`, `Return` -> `return_pressed`, `Shift+Return` -> `prev_match`.
    `set_match_count(cur, total)` updates the label (`0 / 0` when total 0) and
    enables/disables ▲/▼. `focus_field()` focuses + selects all.
- `src/app/window.py`:
  - `FindController` - Qt-free / DB-free match engine. Takes three injected
    callables (`search_conversations(terms) -> list[int]`, `list_center_texts`,
    `list_right_texts`). `parse_terms` (split/lower/drop-empty); `set_mode` /
    `set_query` recompute `self.matches` (row indices for center/right, conv ids
    for left) and reset `self.cursor` to 0 / -1; `next`/`prev` wrap;
    `match_label()` -> `(cursor+1, len)` or `(0, 0)`. Shared rule: a row matches
    when every term is a case-insensitive substring of its text. This is the
    unit-tested core.
  - `MainWindow._wire_find_bar()` (end of `_create_central_widget`, after
    `_wire_pane_focus`): one hidden `PixelFindBar`, one `FindController` with the
    three adapters. `_find_search_conversations` wraps
    `db.search_conversations(" ".join(terms), limit=200)` in try/except ->
    `logger.error` + `[]`. `_find_center_texts` reads each `_answer_layout`
    row's `message_text` property; `_find_right_texts` reads a new `find_text`
    property set in `add_transcription_to_view` (falls back to the bubble label).
  - `QShortcut(QKeySequence.Find, self)` -> `_open_find_bar()`: reparents the bar
    onto `self._panes[active]`, `set_mode(active)`, positions top-right (8 px
    inset), shows/raises/focuses, re-runs the current query. A second `Ctrl+F`
    over the same pane just re-focuses the field.
  - `query_changed` -> clear old highlight, `set_query`, apply highlight/select
    for the new current match, push `set_match_count`. `next_match`/`prev_match`
    -> step + re-apply. `return_pressed` -> left mode: `_load_conversation` +
    close; center/right: same as next. Center/right apply =
    `bubble_of_row(row).set_highlighted(True)` + `ensureWidgetVisible(row, 0,
    40)`. Left apply = select the `conversations_list` item with matching
    `Qt.UserRole` and `scrollToItem`.
  - `closed` / Esc / active-pane-change-under-the-bar -> `_close_find_bar()`:
    clears the highlight, hides, returns focus to the pane. `_set_active_pane`
    closes the bar when the new active pane differs from `_find_bar_pane`;
    `resizeEvent` repositions it while visible; `MainWindow.keyPressEvent`
    consumes `Esc` for the bar before the scope-clear path.
  - BU097's Search Chats dialog, `session_search_input` and the detached windows
    are untouched.

Validation:
- `python -m pytest tests/test_bu099.py -q` -> 10 passed (term parsing, center
  single/multi AND + order independence, empty -> `(0,0)`, next/prev wrap +
  label sequence, right corpus, mode-switch recompute/reset, left conv-id list
  in order, empty left result, invalid mode ignored, DB-adapter swallows a
  raising `search_conversations`). The test extracts `FindController` from
  `window.py` via `ast` and execs it in isolation (no Qt in this env).
- `python -m py_compile src/app/window.py src/app/pixel_widgets.py` -> OK;
  BU096/097/098 tests still green.
- The bar UI, highlight/scroll and focus-driven auto-close need a manual run.

### BU099 follow-up - find bar fit

The 330 px bar overflowed the left sidebar (250-278 px) and the transcripts
panel (300-340 px). `PixelFindBar` no longer fixes its width (only height 46,
`preferred_width = 330`); `MainWindow._position_find_bar` clamps it to
`max(150, min(330, host.width() - 20))` and re-applies on every open / resize,
so the bar always sits fully inside the active pane with a 10 px inset that
clears the panel's pixel-cut border and corner.

Second pass: the bar was still shown at its unclamped 330 px because
`_open_find_bar` called `_position_find_bar()` *before* `show()`, and that
method early-returns while the bar is not visible - so the clamp never ran on
open. Fixed by positioning after `show()` / `raise_()`.

### BU099 follow-up - in-text word highlight

The border highlight alone didn't show *which* word matched, so the current
match's bubble now also highlights the term(s) inside its own text.

- `src/app/pixel_widgets.py`: `highlight_terms_html(text, terms)` - HTML-escapes
  `text`, then wraps every case-insensitive occurrence of any `terms` substring
  in a `<span style="background:#F6E0A6; color:#071846;">` chip (new constants
  `FIND_TERM_BG` / `FIND_TERM_FG`); terms are matched longest-first so a short
  term (`"cat"`) can't shadow a longer one it's a substring of (`"category"`).
  `PixelBubble` keeps its original `_text`, gains `_match_terms` and
  `set_match_terms(terms)`: switches the label to `Qt.RichText` with the
  highlighted HTML, or back to `Qt.PlainText` with the original string when
  `terms` is empty.
- `src/app/window.py`: `_apply_find_current` calls
  `bubble.set_match_terms(FindController.parse_terms(self._find.query))`
  alongside `set_highlighted(True)`; `_clear_find_highlight` clears both
  (`set_highlighted(False)` + `set_match_terms([])`) before the highlight moves
  to the next match or the bar closes. Left mode (sidebar list) is unaffected -
  no bubble text to highlight there.

Validation: `python -m pytest tests/test_bu099.py -q` -> 15 passed (5 new cases
for `highlight_terms_html`: case-insensitive wrap, multiple terms, HTML
escaping of untrusted bubble text, no-terms passthrough, longest-term-first
shadowing). Extracted via the same `ast` isolation as `FindController` (no
PySide6 in this env). `py_compile` on both files -> OK.

### BU099 follow-up - transcripts highlight invisible (cream-on-cream)

Reported: word highlight worked in chat but not in the transcripts panel.
Root cause: the highlight chip used a single hardcoded color pair
(`background:#F6E0A6`) which is the *exact same* color as `CREAM`, the fill of
"cream"-variant bubbles (System transcript lines, and user chat bubbles). The
chip was rendering correctly - it was just invisible against a same-colored
bubble. Blue-variant bubbles (assistant replies, Mic lines) never showed the
bug because their fill (`BUBBLE_BLUE`) differs from the chip color, so chat
looked "working" while it was really only the assistant-bubble half of it.

Fix: `highlight_terms_html(text, terms, bg, fg)` now takes the chip colors as
parameters instead of hardcoding them. `PixelBubble.set_match_terms` picks the
pair that contrasts with its *own* `variant` - `FIND_TERM_ON_CREAM` (dark blue
chip, light text) for cream-fill bubbles, `FIND_TERM_ON_BLUE` (bright cream
chip, dark text) for blue-fill bubbles - so the chip is always visible
regardless of which bubble (chat or transcript, either role/source) it lands
on. Also dropped an unused `self._text` left over from the initial pass; the
bubble's real body is `self._body_text` (post the scope-footer split).

Validation: `python -m pytest tests/test_bu099.py -q` -> 16 passed (added a
case asserting the two contrast pairs are honored and don't bleed into each
other). `py_compile` on both files -> OK.

### BU100 - Collapsible sectioned summary view

The summary window was three near-identical `QDialog` bodies in `window.py`
(`_show_session_summary`, `_show_summary_by_session_id`,
`_on_view_summary_icon_clicked`), each doing
`QTextBrowser.setPlainText(summary_content)` - an unstyled wall of text, no
structure, and a gray `color: gray` info label that ignored the app theme.
Meanwhile the stored content already *has* structure: `templates.py` (FULL and
GENERAL_TRANSCRIPT) asks the model for numbered sections - Overview, Key
Points, Action Items, Decisions, Next Steps / Open Questions.

So the split is free; it just was never parsed. `parse_summary_sections` keys
off a table of known section names (with aliases and markdown decoration
stripped) rather than "any numbered line", because summaries are *full of*
numbered lines - a key point reading "1. Decisions were deferred." would
otherwise tear the section in half. Unknown content degrades to a single
`Summary` section instead of disappearing. `format_summary_body_html` renders
each body into Qt's rich-text subset, and reflows the model's hard line wraps:
a plain line continues the open entry, a blank line closes it. Without that,
every wrapped sentence rendered as its own paragraph with a gap in the middle.

`PixelCollapsibleSection` is the widget - a clickable pixel header (chevron,
title, cream count chip) over a `PixelPanel(inner=True)` body card. It sizes to
its content so the sections scroll as one page; nesting a `QTextBrowser` per
section would have given every section its own scrollbar.

Two things worth remembering:

- **Stylesheet font-size beats `setFont`.** The first cut of the zoom control
  scaled `QFont` objects and appeared to do nothing - only widget *heights*
  changed. `app_qss()` declares `font-family`/`font-size` on `QWidget`, and a
  stylesheet font property always overrides a programmatic `setFont`. Sizes now
  go through each widget's own stylesheet (`font-size: Npt`); letter spacing,
  which QSS can't express, still rides on the `QFont`.
- **Fixed-size chrome sets the window floor.** The bottom row (Expand all /
  Collapse all / Close) doesn't scale with the text, so the dialog minimum is
  560x400 - below that the three buttons overlapped.

Validation: `python -m pytest tests/test_bu100.py -q` -> 13 passed (section
order, per-section bodies, markdown headers, the numbered-line-is-not-a-header
regression, legacy fallback, preamble, empty input, item counts, HTML escaping,
prose/entry reflow, action-item rules). Extracted via the same `ast` isolation
as BU099 (no PySide6 in the test env). Plus a Qt harness run against the real
`_open_summary_window` body (also `ast`-extracted, driven with `QTest`): header
click collapses only its own section and re-expands, Expand/Collapse all,
zoom readout + clamping at both ends, no horizontal scrollbar at minimum width,
and the empty-summary path still informing instead of opening a dialog - all
green, with screenshots checked at 560x400, 780x660 and 1200x900.

### BU100 follow-up - drop the lead-in section, render markdown bold

Three formatting corrections after seeing the view against real summaries:

- **The leading `Summary` section was noise.** Models prefix their output with
  "Here's a comprehensive summary of the meeting:" before `1. Overview`, and
  the parser was faithfully turning that chatter into its own collapsible
  section. `parse_summary_sections` now drops everything before the first
  recognized header. The legacy path is untouched and still matters: when *no*
  header is recognized the whole text is kept as a single `Summary` section, so
  off-template content is never silently discarded - only a lead-in that sits
  in front of real sections is.
- **`**bold**` rendered literally.** `format_summary_body_html` escaped its
  text but never interpreted markdown, so the asterisks showed. Added
  `escape_with_markdown_bold(text)`: `html_escape.escape` first, then
  `\*\*(.+?)\*\*` -> `<b>\1</b>`. The order matters and is safe in that
  direction only - `**` contains nothing HTML-special, so escaping can't
  disturb the markers, while doing the regex first would let escaping mangle
  the `<b>` tags it produced. Reflow runs before this, so a bold span the model
  split across a hard wrap still closes correctly. The body font stays normal
  weight (QSS sets `font-size` without `font-weight`), which is what makes the
  bold read.
- **Alignment now matches the chat bubbles.** `body_label` moved from
  `AlignTop | AlignLeft` to `AlignLeft | AlignVCenter`, with
  `body_layout.setAlignment(..., Qt.AlignVCenter)` centring the block in its
  card - paragraphs still read left-aligned.

`meta_label` (Type / Model / date) already followed the chat's secondary-text
pattern - separated from the body, smaller, muted - so it was left alone.

Validation: `python -m pytest tests/test_bu100.py -q` -> 16 passed (the
preamble case flipped from "is kept" to "is dropped", plus three new bold
cases: basic rendering, bold surviving escaping and reflow, and unpaired
asterisks like "2**3" left alone). Qt harness re-run against a sample carrying
both a lead-in line and markdown bold - all interaction checks still green, and
the rendered view shows no stray `Summary` section and no visible asterisks.

## BU101 - Transcript Download Button

Summary:
Added a download button to the Transcripts Window panel, alongside the
existing filter and detach buttons, that saves the currently displayed
transcript stream to a `.txt` file in the Windows Downloads folder and
confirms the save via a status notification.

Files Changed:

- src/app/window.py (new button + `_on_download_transcripts` /
  `_resolve_transcript_session_name`, `QStandardPaths` and `os` imports)
- assets/pixel/icon_download.svg (new pixel-art download glyph, same palette
  as `icon_export.svg`)

Implementation:

- `download_transcript_button` (`PixelToolButton`) added to the top button row
  in `_build_transcripts_panel`, same icon size and styling as
  `transcript_filter_button` / `detach_transcription_button`.
- `_on_download_transcripts` exports `self._transcription_history` (the same
  `[HH:MM:SS] Source: text` lines already shown in the panel/detached window)
  to `chronicle_transcript_<session_name>_<YYYYMMDD_HHMMSS>.txt` under
  `QStandardPaths.DownloadLocation`. An empty history shows a "No transcripts
  to download" notification instead of writing a file; a write failure is
  caught and reported through `_on_status_update(..., is_error=True)`.
- `_resolve_transcript_session_name` picks the session name from the selected
  session (`db.get_session`) or the active session, falling back to
  `"session"`, and sanitizes it to alnum/`-`/`_` for use in a filename.

Definition of Done Satisfied:

- [x] Download button appears in the transcripts panel next to filter/detach,
      same visual style
- [x] Clicking it writes the visible transcript to a `.txt` file in the
      Windows Downloads folder
- [x] A status notification confirms the save (or reports the error)
- [x] Empty transcript view shows a notification instead of writing a file

Validation:

- `python -m py_compile src/app/window.py` -> OK.
- PySide6 not installed in this env, so the button click / file write / Qt
  status bar update need a manual run in the app.

Next:

- none

### BU101 follow-up - download icon redesign

The initial `icon_download.svg` reused the same chevron-cluster pattern as
`icon_export.svg`, which didn't read as "download". Redesigned it as a
pixelated version of the classic arrow-into-tray download glyph (matching the
reference image the user shared): a vertical shaft, a triangular arrowhead
narrowing to a single-cell apex, and a wide horizontal bar underneath
representing the tray. Same `#FFE8AD` fill and `crispEdges` rendering as the
rest of `assets/pixel/`, so it stays visually consistent with the other
toolbar icons while looking distinctly like a download action.

## BU102 - Resume Stopped Session From All Sessions

Summary:
Pause/resume (BU060/BU061) only covered the session currently loaded in
memory - there was no way to pick a stopped session from the All Sessions
window and continue recording into it. Added a "Resume" action to the
session card menu that resumes recording into the same session_id, keeping
its original start_time, name, transcripts and screenshots intact.

Files Changed:

- src/app/session_manager.py (added resume_stopped_session)
- src/app/window.py (added Resume menu item and handler)
- docs/build_plan/BU102.md (new file)
- docs/build_plan/index.md
- docs/current_state.md

Implementation:

- `SessionManager.resume_stopped_session(session_id)`: refuses if another
  session is currently active or paused (must stop/pause it first, so no
  second session is ever recorded concurrently). Loads the target session via
  the existing `load_session` (which restores start_time, name and path from
  the DB row unchanged), flips its in-memory and DB status back to `active`
  without touching `start_time`, sets it as `current_session`, then calls
  `start_recording('main')` to continue writing audio into the same session
  folder.
- `_build_session_actions_menu` (window.py) now adds a "Resume" item for any
  non-live card whose stored status is `stopped`, `paused` or `completed`,
  placed next to Rename. It is disabled with "Resume (stop current session
  first)" when another session is currently live, mirroring how Delete is
  disabled while a session is live.
- New handler `_resume_session_from_all_sessions`: calls the manager method,
  then reuses the same UI wiring `_on_start_session` uses to reflect a
  session becoming active - sets `_selected_session_id`, switches
  `scope_combo` to Specific Session, updates the search box and scope label,
  reloads live transcripts - refreshes the All Sessions list and closes the
  dialog.

Important Decisions:

- Did not reuse `Session.start()` for resuming, since it unconditionally
  sets `start_time = datetime.now()` - that would have overwritten the
  original session start time and broken the "same session metadata"
  requirement. `resume_stopped_session` sets `status` directly instead.
- Resuming into a session while another is live is refused rather than
  auto-stopping the live one, so the user doesn't lose an unsaved recording
  by clicking the wrong menu item.

Validation:

- `python -m py_compile src/app/window.py src/app/session_manager.py` -> OK.
- PySide6 not installed in this env, so the menu click / recording resume
  need a manual run in the app.

Next:

- none

### BU101 follow-up - autoscroll landed one message short

Reported: the transcripts panel's stick-to-bottom behavior would scroll to
the second-to-last message instead of the newest one.

Root cause: the original fix read `scroll_bar.maximum()` inside a
`QTimer.singleShot(0, ...)` right after inserting the new bubble. That single
deferred callback assumed one event-loop tick was enough for the scroll
area's content height to finish settling, but a word-wrapped bubble can take
an extra layout pass to reach its final height - so the captured `maximum()`
was sometimes still the pre-final value, and the view snapped to a position
just short of the true bottom.

Fix: `src/app/window.py` now connects `verticalScrollBar().rangeChanged` to
`_on_transcription_range_changed`, which sets the scrollbar to whatever
`maximum` it's just been given whenever `_transcription_autoscroll` is `True`.
`rangeChanged` fires every time the content height actually changes -
including a second pass if one occurs - so the view keeps re-snapping to
`maximum` until it reflects the bubble's real final height, instead of
guessing after a single fixed delay. `add_transcription_to_view` no longer
needs its own scroll-adjustment code; inserting the bubble is enough to
trigger the range change.

Validation: `python -m py_compile src/app/window.py` -> OK. PySide6 not
installed in this env, so the actual snap-to-newest-message behavior needs a
manual run.

## BU103 - Grouped Live Transcript Bubbles

Summary:
The live transcript stream produced a new bubble per ~10s audio chunk
(`ChunkedAudioRecorder.CHUNK_DURATION`), so a single continuous sentence
spoken over several chunks got sliced into stacked bubbles with no visible
time. Reworked the stream to group consecutive same-source chunks into one
growing bubble, ending the group on a real pause, a source change or a ~60s
cap, with one `HH:MM` timestamp per bubble and a brief fade-in on new/appended
text.

Files Changed:

- src/app/pixel_widgets.py (`_SCOPE_FOOTER_RE`, `PixelBubble.append_text`)
- src/app/window.py (grouping state, `add_transcription_to_view`,
  `_play_transcript_fade`, thread-safe hop, historical reload path)
- docs/build_plan/BU103.md (status)
- docs/build_plan/index.md
- docs/current_state.md

Implementation:

- `pixel_widgets._SCOPE_FOOTER_RE` relaxed from matching only
  `"\n\n(— via .+)$"` to any `"\n\n(— .+)$"`, so the same muted italic footer
  `PixelBubble` already renders for scope tags now also renders a timestamp
  caption like `"— 21:03"`.
- `PixelBubble.append_text(text)` appends `" " + text` to `_body_text` and
  re-renders `self.label` through the same path `set_match_terms` uses, so
  BU099 find-highlighting keeps matching text appended after the bubble was
  created.
- `window.py` tracks one "open group" per source (`mic`/`system`) in
  `self._transcript_groups`: the row widget, its `PixelBubble`, the group's
  `start_dt` and `last_end` datetime. Reset alongside `_transcription_history`
  in `_clear_transcription_view`.
- New module constants `TRANSCRIPT_PAUSE_GAP_SECONDS` (1.5x
  `ChunkedAudioRecorder.CHUNK_DURATION`, ~15s) and
  `TRANSCRIPT_MAX_GROUP_SECONDS` (60s).
- `add_transcription_to_view(text, source, timestamp='', start_dt=None,
  end_dt=None)` decides, per source: if there's an open group and the new
  chunk's `start_dt` is within `TRANSCRIPT_PAUSE_GAP_SECONDS` of the group's
  `last_end` *and* the group's total span stays under
  `TRANSCRIPT_MAX_GROUP_SECONDS`, extend the open bubble
  (`bubble.append_text`, merge `find_text`, bump `last_end`); otherwise start
  a new bubble exactly as before, with a `"— HH:MM"` footer (the new group's
  start time) appended via the relaxed footer regex. Mic and system never
  merge - they're separate dict keys / separate open-group slots.
- A pause is detected purely from the existing gap between one chunk's
  `timestamp_end` and the next chunk's `timestamp_start` for the same source
  - no new VAD plumbing. A chunk that fails the existing
  `ChunkedAudioRecorder._check_vad` speech-ratio check never reaches the live
  callback at all, so a gap bigger than ~1.5 chunks means that check silently
  dropped a window, which *is* the pause (see BU103.md's Design decision).
- `_play_transcript_fade(row)` plays a 200ms `QGraphicsOpacityEffect` +
  `QPropertyAnimation` fade from 0.35 -> 1.0 opacity on a row, whether it was
  just inserted or just extended. The effect/animation are cached on the row
  via `setProperty`/`property` so repeated appends reuse (and restart) the
  same objects instead of accumulating running animations.
- The thread-safe live hop no longer collapses the result into one
  pre-formatted string: `_on_live_transcription` now invokes
  `_append_transcription(text, source, timestamp_start, timestamp_end)` (4
  `Q_ARG(str, ...)`), so the real `timestamp_end` survives the
  `QMetaObject.invokeMethod` round trip for the pause-gap check.
  `_append_transcription` parses the ISO timestamps back to `datetime` and
  calls `add_transcription_to_view` with `start_dt`/`end_dt`.
- The historical reload loop (`_load_transcripts_for_session`) now computes
  `start_dt` from the stored unix timestamp and approximates `end_dt` as
  `start_dt + ChunkedAudioRecorder.CHUNK_DURATION` seconds, then calls the
  same grouped `add_transcription_to_view`, so reloaded sessions render with
  the same grouped bubbles instead of one bubble per stored row.
  `_transcription_history` still logs one full `[HH:MM:SS] Source: text` line
  per raw chunk regardless of visual grouping, so the BU101 transcript
  download stays at full resolution.
- `_add_transcription_to_detached` (the detached transcript window) is
  untouched and keeps its flat per-chunk format, per BU103's Out of Scope.

Validation:

- `python -m py_compile src/app/window.py src/app/pixel_widgets.py` -> OK.
- PySide6 not installed in this env, so the manual live-session grouping /
  pause / mic-vs-system / find-bar / download checks from BU103.md's
  Validation Requirements need a run in the actual app.

Next:

- none

### BU103 follow-up - live transcripts never reached the UI

Reported: after a manual run, live transcriptions showed up in the console
(`[LIVE TRANSCRIPTION] mic: ...`) but never appeared in the transcript panel
at all - not even ungrouped. The console showed:

```
QMetaObject::invokeMethod: No such method MainWindow::_append_transcription(QString,QString,QString)
Candidates are:
    _append_transcription(QString,QString,QString,QString)
```

Root cause: `_on_live_transcription` called
`QMetaObject.invokeMethod(self, "_append_transcription", Qt.QueuedConnection,
Q_ARG(str, ...), Q_ARG(str, ...), Q_ARG(str, ...), Q_ARG(str, ...))` with 4
`Q_ARG`s to match the new 4-argument `_append_transcription(text, source,
timestamp_start, timestamp_end)` slot added earlier in this BU. This PySide6
build's `invokeMethod` binding silently truncated the call to 3 arguments
before dispatch, so it could never find a matching registered slot (the
4-argument one was correctly registered - the call just didn't carry 4 args)
and the queued call failed every time, dropping every live transcript before
it ever reached `add_transcription_to_view`.

Fix: replaced the `QMetaObject.invokeMethod`/`Q_ARG` hop with a Qt signal.
Added `MainWindow._live_transcription_ready = Signal(str, str, str, str)`,
connected to `_append_transcription` with `Qt.QueuedConnection` in
`__init__`, and `_on_live_transcription` now calls
`self._live_transcription_ready.emit(text, source, str(timestamp_start),
str(timestamp_end))` instead of invokeMethod. Qt's native signal/slot
argument marshaling carries all 4 strings across the audio-thread ->
UI-thread hop reliably, sidestepping whatever limitation truncates
`invokeMethod`'s `Q_ARG` list in this environment. Removed the now-unused
`QMetaObject`/`Q_ARG` imports.

Files Changed:

- src/app/window.py

Validation:

- `python -m py_compile src/app/window.py` -> OK.
- PySide6 still not installed in this env; the user confirmed the original
  failure via a manual run and will need to re-verify live transcripts now
  reach the panel (grouped, with timestamp and fade) after this fix.

### BU103 follow-up - fade glitch on scroll, and no separation between appended chunks

Reported (after confirming live transcripts render): (1) scrolling up while a
bubble is still growing "kind of bugs" - a screenshot showed garbled/corrupted
text at the top of an in-progress bubble; (2) chunks appended to a growing
bubble ran together with a single space, making it hard to tell where new
text started.

Root cause (1): `_play_transcript_fade` re-triggered the same
`QGraphicsOpacityEffect` (left attached to the row after its first use) on
every `append_text` call. Re-applying/animating a graphics effect on a widget
whose content is *simultaneously* resizing (the label re-wrapping as text
grows) is a known-fragile combination in Qt - the effect's cached source
pixmap doesn't reliably track the new size, especially for a widget currently
clipped/off-screen in a `QScrollArea` (scrolled away from the bottom), which
matches exactly when the corruption was seen.

Fix: `add_transcription_to_view`'s "extend" branch no longer calls
`_play_transcript_fade` at all - only a freshly inserted bubble (whose size is
already settled by the time the fade starts) gets the fade-in.
`_play_transcript_fade` itself now creates the `QGraphicsOpacityEffect` fresh
and removes it again (`row.setGraphicsEffect(None)`) once the animation
finishes, instead of leaving it cached on the row - so any later resize from
`append_text` always happens on a plain widget with no effect attached.

Fix (2): `PixelBubble.append_text` now joins the new chunk onto
`_body_text` with `"\n\n"` (a blank-line paragraph break) instead of a single
space, so each appended chunk reads as its own paragraph inside the bubble.

Files Changed:

- src/app/window.py
- src/app/pixel_widgets.py

Validation:

- `python -m py_compile src/app/window.py src/app/pixel_widgets.py` -> OK.
- Manual re-verification in the running app still pending (PySide6 not
  installed in this dev env).

### BU103 follow-up - 2-second chunk duration

Requested: bubbles should update roughly every 2 seconds instead of every
10, for a more fluid live-captioning feel. Confirmed with the user this means
lowering the actual audio chunk length (not a UI-only refresh), with the
tradeoff spelled out first: ~5x more transcription calls for the same amount
of speech, and touching a file (`src/audio_capture/core.py`) outside BU103's
original Allowed Files list.

Implementation:

- `ChunkedAudioRecorder.CHUNK_DURATION` lowered from `10` to `2` seconds.
- `ChunkedAudioRecorder.OVERLAP_DURATION` lowered from `1` to `0.3` seconds.
  At the original 10s/1s chunk/overlap it was a 10% overlap ratio; keeping a
  flat 1s overlap on a 2s chunk would have made it 50% - each chunk would
  re-transcribe half of the previous chunk's audio, both compounding the
  cost increase further and doubling down on the duplicate-word-at-boundary
  artifact visible in the reported screenshot (repeated "it's it's" - a
  pre-existing overlap-boundary effect that grouping into one bubble made
  newly visible). 0.3s keeps roughly the original ~15% ratio.
- `ChunkedAudioRecorder.__init__`'s `chunk_duration`/`overlap_duration`
  parameter defaults now read `CHUNK_DURATION`/`OVERLAP_DURATION` off the
  class instead of separately hardcoded literals (`10`/`1`), and
  `DualSourceChunkedRecorder.__init__` plus the `create_chunked_recorder` /
  `create_dual_source_recorder` factory functions now default from
  `ChunkedAudioRecorder.CHUNK_DURATION`/`OVERLAP_DURATION` too - one source
  of truth, so `window.py`'s `TRANSCRIPT_PAUSE_GAP_SECONDS` (already derived
  from `ChunkedAudioRecorder.CHUNK_DURATION`) automatically tracks the new
  2s cadence without a separate edit.
- `SessionManager` calls `dual_recorder_factory(...)` without an explicit
  `chunk_duration`, so both live sessions (`create_session` and
  `load_session`) pick up the new 2s/0.3s default automatically.

Files Changed:

- src/audio_capture/core.py

Out of scope / not done:

- Any dedup of the overlap-boundary duplicate words themselves - flagged as
  a pre-existing, separate concern, not fixed here.
- `tests/test_capture_system_chunk.py` passes `chunk_duration=5` explicitly
  and is unaffected.

Validation:

- `python -m py_compile src/audio_capture/core.py` -> OK.
- Not run against real audio hardware in this env; the user will need to
  verify live sessions still capture/transcribe correctly at the new
  cadence, and that per-chunk transcription latency/cost at 2s chunks is
  acceptable.

### BU103 follow-up - 2s chunks hurt transcription quality, settled on 5s

Reported: 2-second chunks update bubbles fast but give the transcription
model too little audio per call, degrading quality; wants bubbles to still
accumulate for up to a minute (already true - `TRANSCRIPT_MAX_GROUP_SECONDS`
in `window.py` was untouched by the chunk-duration change) with a 5-second
chunk duration as the compromise.

Implementation:

- `ChunkedAudioRecorder.CHUNK_DURATION` raised from `2` to `5` seconds.
- `ChunkedAudioRecorder.OVERLAP_DURATION` raised from `0.3` to `0.5` seconds
  (~10% of chunk duration, matching the original 10s/1s ratio).
- No other changes needed: every other default (`DualSourceChunkedRecorder`,
  the two factory functions) and `window.py`'s `TRANSCRIPT_PAUSE_GAP_SECONDS`
  read off these two class attributes, so they all picked up 5s/0.5s
  automatically from this one edit.

Files Changed:

- src/audio_capture/core.py

Validation:

- `python -m py_compile src/audio_capture/core.py` -> OK.
- Not run against real audio hardware in this env.

### BU103 follow-up - invented and missing words at chunk boundaries

Reported: with shorter chunks, some words look "invented" (not actually
said) and some real speech goes missing, both concentrated at chunk
boundaries. Diagnosis: (1) `OVERLAP_DURATION` (0.5s) was thin enough that a
word straddling a chunk edge could land right at the very start/end of a
chunk's audio with little surrounding context, which is exactly when an ASR
model is most likely to guess a plausible-sounding but wrong word, or to clip
it entirely; (2) `LiveTranscriber._deduplicate` (src/transcription/live.py)
only stripped the chunk-overlap re-transcription when the two chunks produced
*exactly* the same words for that overlapping audio - Parakeet doesn't
reliably transcribe the same audio identically twice, so a boundary word that
came out differently each time (a garbled guess on one side, correct on the
other) slipped past the exact match and both copies stayed in the text,
reading as an invented duplicate.

Implementation:

- `ChunkedAudioRecorder.OVERLAP_DURATION` raised from 0.5s to 1.0s (20% of
  the 5s `CHUNK_DURATION`) so a boundary word has a real chance of sitting
  fully inside the overlap - with genuine audio context on both sides - in
  at least one of the two chunks, rather than right at a hard edge.
- `LiveTranscriber._deduplicate` reworked to match phrases of 4, 6 or 8 words
  by `difflib.SequenceMatcher` character-similarity ratio (thresholds 0.85,
  0.80, 0.75 respectively - shorter phrases need a higher bar since fewer
  characters have to coincidentally agree) instead of requiring exact
  equality, so a slightly-different second transcription of the same
  overlapping audio still gets recognized and stripped.
- Deliberately did NOT extend fuzzy matching to the 2-3 word phrase lengths:
  tested `"we can do"` (previous chunk tail) against `"we can also"` (new
  chunk head) - two clearly different, non-overlapping continuations - and
  it matched at ratio 0.80 purely because both start with "we can". Fuzzy
  matching at short lengths would have silently deleted real text on any
  sentence that happens to restart with a common short phrase. Those lengths
  still require an exact match, matching the original behavior.
- Fixed a latent bug found while rewriting this: on a successful dedup match,
  the method returned `' '.join(new_words[num_words:])` where `new_words`
  was built from `new_text.lower().split()` - so any transcript that
  actually triggered dedup lost its capitalization. Now `new_words` keeps
  the original casing and only a separate lower-cased copy is used for
  comparison.

Files Changed:

- src/audio_capture/core.py (`OVERLAP_DURATION`)
- src/transcription/live.py (`_deduplicate`)

Validation:

- `python -m py_compile src/audio_capture/core.py src/transcription/live.py`
  -> OK.
- Exercised `LiveTranscriber._deduplicate` directly (parakeet/numpy/soundfile
  stubbed out, since they're not installed in this dev env) against 7 cases:
  exact short-phrase dup, unrelated text (no false positive), the "we can
  do"/"we can also" short-phrase near-miss (correctly NOT matched), original
  casing preserved through a dedup match, a minor punctuation variant at 4+
  words (correctly matched and stripped, ratio 0.82), a longer 4-word
  coincidental-prefix case (correctly NOT matched), and no-previous-text.
  All passed as expected.
- Not run against real audio/microphone input in this env.

## BU104 - Upload Audio File As Session

Summary:
Sessions could only be created by recording live. Added an "Upload Audio"
button to the All Sessions window (left of Close; the redundant Refresh
button is gone) that imports a WAV or an iPhone Voice Memos `.m4a` as a new
session and transcribes it with the existing batch pipeline.

Files Changed:

- src/audio/importer.py (new - decode, split at pauses, write chunks)
- src/app/session_manager.py (added import_audio_file)
- src/app/window.py (Upload Audio button, Refresh removed, upload handler)
- src/transcription/processor.py (no overlap dedup for imported chunks)
- requirements.txt (av)
- docs/build_plan/BU104.md (new file)
- docs/build_plan/index.md
- docs/current_state.md

Implementation:

- `src/audio/importer.py`: `decode_audio_file` opens the file with PyAV,
  decodes the first audio track and resamples it to 16 kHz mono int16
  (Parakeet's native rate) with `av.AudioResampler`; one path covers WAV and
  `.m4a` (AAC / Apple Lossless) alike. It also returns the container's
  `creation_time` (UTC, converted to local; ignored if missing, pre-2000 or
  in the future). `split_at_pauses` makes ~30 s chunks, cutting each at the
  quietest 50 ms frame of its last 5 s, and stops once the remainder fits in
  one chunk so the tail is never a sliver. `write_chunks` writes PCM_16 WAVs
  named `<start>_<end>_import.wav` - the live recorder's layout plus a
  suffix - so `TranscriptionProcessor` sorts and timestamps them unchanged.
- `SessionManager.import_audio_file`: decodes before touching the DB, so an
  unreadable file creates nothing. Creates a `stopped` session named after
  the file stem, dated from the embedded creation time or else the file's
  mtime minus its duration, with `end_time` = start + duration. Writes the
  chunks into `audio/mic`, rolling back the row and folder on failure (ids
  are AUTOINCREMENT, so the folder is always fresh). Returns the Session with
  a transcription processor attached, ready for `process_transcriptions`.
- `TranscriptionProcessor._transcribe_and_store` skips
  `_deduplicate_transcription` for `_import` chunks.
- `window.py`: the bottom bar is [hint] [Upload Audio] [Close].
  `_upload_audio_from_all_sessions` opens a file picker (last folder, else
  Downloads) and imports under a wait cursor. It then calls the dialog's new
  `_focus_session` hook (clears search / filter, reloads, selects and
  scrolls to the card), marks the Transcript chip busy, and defers
  `_transcribe_uploaded_session` by 50 ms - the same idiom as the card's
  Transcribe action - which runs `SessionManager.process_transcriptions`
  (sets `transcription_status`, RAG-indexes) and reloads the list.

Important Decisions:

- Bypassing the batch dedup was required, not optional: its
  `_try_fuzzy_match` removes any 2+ word phrase from the previous chunk
  found anywhere in the new one (substring match, lower-casing the result).
  Built for the recorder's overlapping chunks; on pause-cut, non-overlapping
  upload chunks it only deletes real words. Replayed on the test upload, it
  cut words from 2 of 3 chunks. Keyed off the filename suffix (like
  `_get_source_from_filename`'s `_system` check), so a retry through
  "•••" → Transcribe also skips it.
- Uses `SessionManager.process_transcriptions(session)` rather than
  `_run_transcription`: the latter goes through `load_session`, which
  replaces `current_timeline` even while another session is recording, and
  it doesn't RAG-index.
- PyAV rather than soundfile: libsndfile can't read AAC / ALAC `.m4a`. PyAV
  was already in the venv (pulled by the old faster-whisper) and its wheels
  bundle FFmpeg; now an explicit requirement.
- Auto-transcribe on upload, blocking the UI like the existing Transcribe
  action. A worker thread would need its own DB connection and Parakeet
  instance; left out of scope.
- Scroll-into-view is deferred (30 ms timer): new cards are shown and laid
  out over several event-loop passes, and an immediate
  `ensureWidgetVisible` after one `processEvents()` scrolled only partway.

Validation:

- `python -m py_compile` on the changed sources -> OK.
- TTS fixtures (WAV 22 kHz, AAC `.m4a` 48 kHz with `creation_time`, stereo
  ALAC `.m4a`): identical 67.8 s decodes, chunks 27.9 / 25.7 / 14.3 s cut in
  silence, 16 kHz mono PCM_16 output, correct start/end times; a non-audio
  file raises a readable error and leaves nothing behind.
- Real Parakeet run on the imported AAC session: 3 accurate timestamped
  transcripts, `transcribed`, RAG-indexed; stored text == raw model output.
- Headless MainWindow against a temp DB: buttons are [Upload Audio]
  [Close]; busy -> ready chip; past-dated upload scrolled into view under
  its day group; simulated transcription failure leaves the card pending.
- `pytest` (hardware / network tests excluded): 366 passed; 18 unrelated
  OpenRouter HTTP 401 failures.

Next:

- none

## BU105 - Performance, Memory, and Threading Rework

Summary:
Requested by the user after a walkthrough of the capture -> transcribe ->
store -> UI pipeline (2.5-3.5 GB RAM, Stop/Pause/Close freezing the UI) and
a read-only audit of `chronicle.db`. Moved live transcription off the audio
capture callback and onto a background queue; collapsed live/batch
transcription onto one shared, unloadable Parakeet model instead of up to
three ~2.6 GB copies; made Stop/Close return immediately and finish
transcribing/indexing/summarizing in the background; reworked
`chronicle.db` (per-thread connections, WAL, versioned migrations,
indexes, incremental FTS, orphan cleanup, startup session repair).

Files Changed:

- src/storage/database.py
- src/transcription/parakeet.py, live.py, processor.py
- src/transcription/worker.py (new)
- src/audio_capture/core.py, system_recorder.py
- src/rag/embeddings.py, indexer.py
- src/app/session_manager.py (rewritten)
- src/app/window.py
- src/config.py
- requirements.txt
- tests/test_rag_indexer.py (+2 regression tests)
- tests/test_database_perf.py, test_live_transcriber.py,
  test_transcription_worker.py, test_transcription_no_duplicate.py,
  test_session_manager_lifecycle.py (new, 37 tests total)

Implementation:

See `docs/build_plan/BU105.md` for the full task breakdown (database,
speech model, live transcription, session lifecycle, UI). The short version:

- `TranscriptionWorker` (new): one background thread + queue.
  `submit()` never blocks; `LiveTranscriber`'s dedup/context state moved
  from one shared `_last_text` to a dict keyed by `(session_id, source)`
  stream - the old shared state compared a mic chunk against the previous
  *system* chunk (or, once a shared engine existed, a previous session),
  which can never find a real overlap.
- `get_shared_engine()` (parakeet.py): one process-wide model instance,
  thread-safe, used by both live and batch transcription, replacing a
  separate `ParakeetEngine` per `LiveTranscriber`/`TranscriptionProcessor`.
  `enable_cpu_mem_arena=False` on both the Parakeet and e5-embedding ONNX
  sessions so memory returns after use instead of staying at its
  high-water mark; idle-unload after 5 minutes via
  `TRANSCRIPTION['idle_unload_seconds']`.
- `ChunkedAudioRecorder`'s mic callback now only copies the incoming
  buffer; resampling (new `_StreamResampler`, PyAV), VAD and the WAV write
  moved to the recorder's own loop. Chunks store at 16 kHz (Parakeet's
  native rate) instead of the device rate when PyAV is available.
- `TranscriptionProcessor._already_transcribed`: skips any audio file a
  transcript row (`transcripts.audio_file`, or a `(source, timestamp)`
  fallback for older rows) already covers - batch transcription used to
  unconditionally re-transcribe and re-insert every chunk.
- `SessionManager` rewritten: a `_JobRunner` background thread for
  finalize/transcribe/summarize; `stop_session(background=True)` returns
  at once and reports completion via `session_finalized_callback`;
  `close()` skips finalizing entirely (picked up by
  `finalize_pending_sessions()` on the next start) and only disconnects
  the database once the job runner is idle.
- `database.py`: per-thread connections + WAL; `SCHEMA_VERSION`-driven
  migration with an automatic pre-migration backup; indexes on every hot
  lookup; `rag_fts` rows keyed by their chunk's rowid so one session's
  re-index updates only its own FTS rows instead of rebuilding the whole
  table; `purge_orphans()` / `repair_interrupted_sessions()` run at every
  startup.
- `window.py`: `_status_ready` / `_session_finalized_ready` /
  `_ui_callback_ready` Qt signals (`Qt.QueuedConnection`) since
  `SessionManager` now calls its callbacks from worker/job threads, not
  just the UI thread; Transcribe/Summarize/Upload Audio moved off the UI
  thread via `SessionManager.submit_job`; a live result is dropped if it
  doesn't match the session actually on screen; screenshot thumbnails
  decode via `QImageReader.setScaledSize` instead of loading the full
  image first; each `QThread` releases its DB connection before exiting.

Important Decisions:

- int8 Parakeet quantization was implemented and measured (~750 MB vs
  ~2.6 GB) but left off by default: on this machine (no AVX-VNNI) it
  changed 26-46% of words versus fp32 on real session audio, including
  several sentences returned empty. `config.TRANSCRIPTION['quantization']`
  documents the trade-off for revisiting on different hardware.
- `close()` doesn't wait for finalization (transcribe/index/summarize) at
  all, even bounded - it flags the session (`needs_finalize=1`) and lets
  the next start's `finalize_pending_sessions()` do it in the background.
  Waiting even briefly on close would reintroduce the "quitting hangs"
  problem for exactly the case (closing mid-session) where the user is
  most likely to expect an instant exit.
- Two real bugs were caught by the new end-to-end lifecycle tests before
  landing, not found any other way: `close()` could disconnect the
  database while a background finalize job was still using it (fixed by
  giving `_JobRunner` a `wait()` the same way `TranscriptionWorker` already
  had one); and, while writing those tests, the first fake speech engine
  used a text template ~93% character-identical between any two chunks
  (only a few timestamp digits differed), which the real fuzzy dedup
  correctly - if confusingly - treated as a duplicate. Not a product bug,
  but worth recording: a canned test double's output needs the same
  "genuinely distinct between chunks" property real speech has, once
  cross-chunk dedup is in the code path being exercised.
- The auto-summary step in `finalize_session` calls the real OpenRouter
  API when `config.SESSION['auto_summary_after_stop']` is on (the
  default), which every new lifecycle test's session (real transcript
  text, no summary yet) triggers exactly like a real one would. Caught
  after a debug run had already made ~3 real calls against the user's key;
  all further lifecycle tests monkeypatch that setting off.

Validation:

- Full existing suite (hardware/interactive-audio and non-pytest script
  files excluded, matching prior runs): 409 passed (403 pre-existing + 6
  new), same 18 pre-existing OpenRouter-401 failures. Confirmed via `git
  stash` that those 18 are identical with and without this BU's changes -
  a pre-existing test-isolation issue, spawned as a separate follow-up
  task rather than fixed here.
- 37 new unit tests (worker, per-stream dedup, no-duplicate-transcription,
  migration, per-thread connections, chunk_text truncation regression)
  plus 6 end-to-end lifecycle tests against a fake audio backend and fake
  speech engine (no devices, no model, no network).
- Migration exercised against a copy of the user's real 7-session
  `chronicle.db` (2.93 MB -> 1.66 MB, 0 orphans, integrity ok) and,
  separately, live via a headless `MainWindow` construction against the
  actual file (backed up first). The automatic pre-migration backup
  independently showed 3 sessions present at the start of this work had
  already been deleted through the still-running old-code app instance
  during this session - unrelated to this BU, confirmed by timestamp.
- Memory (isolated subprocess measurements): fp32 Parakeet loaded+idle
  2593 MB -> 83 MB after `unload()`, reload ~5s; e5 embedder batched vs.
  unbatched peak 1167 MB vs. 1963 MB on a 138-chunk session.
- Not done: real hardware validation (actual mic/system-audio devices at
  16 kHz) - needs the user's machine.

Next:

- none

### BU104 follow-up - WhatsApp voice notes (.opus)

Asked whether Upload Audio accepts WhatsApp voice notes. It didn't: WhatsApp
saves voice notes as `.opus` (Ogg container, Opus codec), which PyAV already
decodes fine - the container format is detected from the file's content, not
its extension - but `src/audio/importer.py`'s `SUPPORTED_EXTENSIONS` (used
only for the upload file picker's filter) didn't list it, so the file picker
hid `.opus` files by default (still reachable via the picker's "All files"
option, but not the intended path).

Implementation:

- `SUPPORTED_EXTENSIONS` gained `.opus` and `.oga` (the alternate extension
  Ogg audio sometimes carries, e.g. from other export paths).

Validation:

- Synthesized a mono Opus-in-Ogg file from the existing TTS fixture and
  confirmed `decode_audio_file` returns the same 16 kHz mono int16 output as
  the WAV/AAC/ALAC fixtures; also confirmed a copy renamed to `.ogg` decodes
  identically (PyAV sniffs content, doesn't trust the extension).
- `python -m py_compile src/audio/importer.py src/app/window.py` -> OK.

Files Changed:

- src/audio/importer.py (`SUPPORTED_EXTENSIONS`)
- src/app/window.py (upload tooltip wording)
- docs/current_state.md

Next:

- none

### BU106-BU110 - Screenshot module overhaul

Asked for a friendlier screenshot module (an image-viewer layout in the
Summary window's theme), a Specific Session assistant that can locate a
screenshot and point the user to it ("The information you asked might be
contained in the screenshot: <ID>"), a preview-first search that only loads
full metadata when needed, plus visible-text search, viewing screenshots
during a live session, and a capture shortcut.

What was there: the assistant got `[Screenshot at <epoch>]: <filepath>` only
for screenshots within 60 s of a retrieved transcript - no content, no id.
`description` was usually NULL, AI context existed only after a manual click
gated on a summary, and the vision model's transcript excerpt was the 2
nearest rows × 200 chars while its prompt claimed ~40 s. The UI was two
~450-line near-duplicate grid dialogs plus a third full-image dialog.
`ScreenshotCapture.register_shortcuts` was never called.

Implementation:

- BU106: `screenshots.preview_description` / `preview_source` (schema v3,
  migration via `_add_column`). `src/screenshots/metadata.py`:
  `transcript_window` (±20 s, nearest-2 fallback), `build_preliminary_description`
  (≤240 chars: note · "Discussed: …" · HH:MM:SS (+offset)), `ensure_previews`
  (rebuilds 'auto' previews only when changed, never touches 'ai'). The context
  generator asks for `short_description`, normalizes its JSON output, and
  stores the short description as the 'ai' preview. DB: `update_screenshot_preview`,
  `get_screenshot_previews`, `get_screenshot_details`.
- `src/screenshots/__init__.py` now loads the Qt classes lazily so the
  Qt-free modules can be imported (and tested) without PySide6 - outside the
  BU106 allowed files, needed for BU107/108 to be importable from the
  assistant package.
- BU107: `src/screenshots/search.py` - Tier 1 `rank_previews` (weighted token
  overlap with keywords 1.5 / preview+note 1.0, temporal boost within 60 s
  decaying to 0 at 180 s, explicit `#42` / "captura 42" references, deictic
  fallback), Tier 2 `promote` (≤3, score ≥ 0.4, one `get_screenshot_details`
  query), `search_session_screenshots` (full cards first, then index in
  capture order, capped at 12).
- BU108: `ScreenshotReference` gained id/preview/tier/details and one renderer
  (`render_screenshot_section`) used by both prompt paths - no file paths.
  `src/assistant/screenshot_contract.py` (`SCREENSHOT_CONTRACT`,
  `parse_screenshot_refs`); the service tracks `_last_screenshot_ids`, appends
  the contract only in Specific Session with screenshots, validates cited ids
  before persisting, and returns `AnswerResponse.screenshot_refs`.
- BU109: `src/app/screenshot_viewer.py` (`ScreenshotViewer`, `_ImageStage`
  QGraphicsView, context threads moved here from window.py, `load_thumbnail`
  moved here). Pure helpers in `src/screenshots/viewer_logic.py` (src/app
  imports the audio stack at package level, so it can't host Qt-free code).
  `PixelCollapsibleSection.set_body`. window.py: `_open_screenshot_viewer`,
  `_open_screenshot_reference`, "View screenshot #ID" buttons under answers;
  deleted both grid dialogs, `_show_full_image`, `_toggle_fullscreen`, the
  dead `_edit_screenshot_description` and its eventFilter branch (~1,200
  lines). No new SVG icons were needed (text glyphs on pixel buttons).
- BU110: viewer search row (Ctrl+F, Enter/F3, This session | All sessions,
  "+ description & summary"), terms emphasized as `**bold**` in the details
  panel, hint + "Generate missing" when screenshots lack visible text.
  Cross-session candidates come from `Database.get_searchable_screenshots`
  and are matched in Python (`viewer_logic.filter_screenshots`) instead of a
  `search_screenshots_text` SQL query as planned: `json.dumps` escapes
  accents, so SQL LIKE can't match accent-insensitively. Viewer is non-modal,
  one per session (`_screenshot_viewers`), `refresh_after_capture` after
  every capture, hidden during the snip. `src/app/global_hotkey.py`
  (RegisterHotKey + QAbstractNativeEventFilter), `src/screenshots/hotkeys.py`
  (`parse_hotkey`, Win+Shift+S reserved check, `ClipboardDeduper`),
  `config.SCREENSHOT`, hotkey synced in `_update_ui_state` (held only while
  live, window-local QShortcut fallback), released on close. Opt-in clipboard
  import via `SessionManager.import_clipboard_screenshot` /
  `ScreenshotCapture.import_image`. Removed dead `register_shortcuts`.

Validation:

- New tests: test_bu106 (17), test_bu107 (12), test_bu108 (12), test_bu109
  (9), test_bu110 (14); updated screenshot assertions in
  test_context_models / test_context_retriever. Full suite: 440 passed,
  22 failed - the same 22 pre-existing failures in test_assistant_service /
  test_openrouter_client as a clean HEAD worktree (confirmed), 14 collection
  errors from files needing PySide6/audio deps in the system Python.
- test_bu108 patches the service module object directly: test_openrouter_client
  purges `src.*` from sys.modules at import, so dotted-name patches hit a
  fresh module copy when the suites run together.
- Offscreen smoke tests (.venv, PySide6 6.6): viewer navigation, zoom,
  description edit (preview refreshed), delete (file + sidecar removed,
  neighbor selected), empty session, search in both scopes, live refresh,
  focus-through-search; rendered the window to PNG to check the theme.
- Real Win32: `RegisterHotKey` succeeds on a native window, and a posted
  `WM_HOTKEY` reaches `GlobalHotkey.activated` through the native event
  filter (other ids ignored).
- Not done (needs the real app): hotkey pressed from another app during a
  session, Win+Shift+S clipboard import, AI context generation through
  OpenRouter, and asking the assistant about a screenshot end to end.

Files Changed:

- src/storage/database.py
- src/screenshots/__init__.py, metadata.py (new), search.py (new),
  viewer_logic.py (new), hotkeys.py (new), context_generator.py, capture.py
- src/assistant/context.py, context_models.py, tools.py, service.py,
  screenshot_contract.py (new)
- src/app/screenshot_viewer.py (new), global_hotkey.py (new), window.py,
  pixel_widgets.py, session_manager.py
- src/config.py
- tests/test_bu106.py ... test_bu110.py (new), test_context_models.py,
  test_context_retriever.py
- docs/build_plan/BU106-BU110.md, index.md, docs/current_state.md

Next:

- none

### BU109/BU110 follow-up - window stacking, full screen on double-click, help

User feedback after the first manual run:

1. The Summary window covered the screenshot viewer and had to be closed to
   use it. Cause: the Summary opened modal (`exec()`), so it stayed on top and
   blocked the now non-modal viewer (the same happened when the viewer was
   opened from the modal All Sessions dialog - it landed behind it). Fix:
   the Summary is non-modal and one per session (`_summary_windows`), and
   both windows go through `MainWindow._present_window`: non-modal normally,
   but made application-modal when another modal dialog is already up, so
   they always stack on top and accept input.
2. Double-clicking the image (or a thumbnail, or pressing F / F11) opens the
   screenshot alone in full screen (`_FullscreenImage`, a child window of the
   viewer). Esc or another double-click closes it; arrows keep browsing and
   zoom keys work. The old "whole viewer full screen" toggle was removed.
3. A "?" button (and F1) opens a help window explaining every button, key
   and label, in collapsible sections (`viewer_logic.help_sections`; the
   capture hotkey is read from `SCREENSHOT["global_hotkey"]`). Help text is
   in English, like the rest of the UI.

Validation:

- Offscreen smoke test: double-click opens full screen, Esc closes it,
  navigation inside full screen syncs the viewer; help window renders;
  `_present_window` stays non-modal normally and becomes modal on top of an
  open modal dialog.
- New HelpTest in tests/test_bu109.py; full suite 441 passed, same 22
  pre-existing failures.
- Not yet checked in the real app: open the viewer, then the Summary, and
  switch between them.

Files Changed:

- src/app/window.py (`_open_summary_window`, `_present_window`, viewer entry)
- src/app/screenshot_viewer.py (`_FullscreenImage`, help, double-click)
- src/screenshots/viewer_logic.py (`help_sections`)
- tests/test_bu109.py

Next:

- none

### BU110 follow-up - highlight search matches

The viewer's search only made matches bold, which was easy to miss. Matches
are now bracketed with private-use marker characters
(`viewer_logic.mark_terms`, `MATCH_START` / `MATCH_END`, replacing
`emphasize_terms`), which survive HTML escaping; `PixelCollapsibleSection.set_body`
swaps them for the find bar's highlight chip (`FIND_TERM_ON_BLUE`, cream on
navy). Sections holding a match are re-expanded and the details panel scrolls
to the first one. Also fixed two labels: "+ description & summary" rendered
as "+ description _summary" (a lone "&" is a Qt mnemonic; now "&&"), and the
"Generate missing" hint button was too narrow.

Validation: tests/test_bu110.py updated (marker output, every occurrence,
accent-insensitive); offscreen smoke test checks the highlight span in the
rendered HTML, no leftover markers, and re-expansion of a collapsed section.

Files Changed:

- src/screenshots/viewer_logic.py, src/app/pixel_widgets.py,
  src/app/screenshot_viewer.py, tests/test_bu110.py

### BU110 follow-up - slide to the match

The details panel used to scroll only to the top of the section holding a
match, so a hit deep in a long Visible Text list stayed off-screen.
`ScreenshotViewer._scroll_to_first_match` lays the section's rich text out in
a `QTextDocument` at the label's width, finds the first fragment with the find
highlight background (`FIND_TERM_ON_BLUE`, the same cream-on-navy chip the
Ctrl+F find bar uses) and scrolls so that line sits in the upper third of the
view. Offscreen check: 40-line list, match on line 35 -> panel scrolls from 0
to the match, which lands inside the viewport.

Files Changed:

- src/app/screenshot_viewer.py

### BU110 follow-up - long sections clipped; search limited to visible text

- Long section bodies (Visible Text, AI Summary) were cut off in the narrow
  details panel: e.g. a 30-line list needed 1640 px but got 1176, the rest
  hidden, so the panel couldn't scroll to it. A word-wrapped QLabel's size
  hint ignores the width it really gets. `PixelCollapsibleSection` now tracks
  the label's resize and sets its minimum height to `heightForWidth` at the
  actual width, measuring with the minimum cleared first - QLabel never reports
  less than its current minimum, so a value taken at a narrow in-between width
  would otherwise stick. Shared widget, so the Summary window gets the fix too.
- Removed the "+ description & summary" chip at the user's request: the search
  matches visible text only (`match_screenshot` / `filter_screenshots` /
  `Database.get_searchable_screenshots` lost their `include_context`
  option), and only the Visible Text section is highlighted, so the panel
  slides to the real match. Help text updated.

Validation: offscreen repro (30 wrapped lines + long summary) - every section
label height == needed height, last line visible, panel scrolls; smoke tests
for viewer / search / scroll-to-match pass; tests/test_bu110.py updated; full
suite 441 passed, same 22 pre-existing failures.

Files Changed:

- src/app/pixel_widgets.py, src/app/screenshot_viewer.py,
  src/screenshots/viewer_logic.py, src/storage/database.py, tests/test_bu110.py

### All Sessions - screenshot count on session cards

Each All Sessions card shows how many screenshots the session has: a new
`PixelShotsChip` (the app's own pixel `icon_camera.svg` + the number, no word)
before the Transcript / Summary chips. Solid blue with cream digits when there
are screenshots; dashed and muted (like a pending chip) at 0; tooltip says
"N screenshots". The count comes from the same single
`list_sessions_with_flags` query (`screenshot_count` subquery, indexed on
session_id). To keep the row from looking crowded, card spacing went from 10 to 8 px and
the status chips' reserved padding from +22 to +18 px; the shots chip reserves two
digits so the chip columns line up across cards. Rendered at 1095 px and 760 px
(long names elide first). Full suite unchanged (441 passed, same 22 pre-existing
failures).

Files Changed:

- src/storage/database.py, src/app/pixel_widgets.py, src/app/window.py

- Follow-up: the camera chip only shows when the session has screenshots (hidden at 0); icon 14 -> 18 px, count 8.5 -> 7.5 pt; fixed two-digit width so it keeps the same size on every card.
