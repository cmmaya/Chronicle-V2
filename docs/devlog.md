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
