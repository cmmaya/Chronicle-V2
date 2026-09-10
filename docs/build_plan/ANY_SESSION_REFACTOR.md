# Any Session Refactor — Handoff Brief (BU086–BU091)

> **Read this file first.** It is the entry point for the chat/agent that implements
> BU086 through BU091. It contains the verified diagnosis, the target architecture,
> the decisions already made, and the order of work. Do not re-litigate the
> decisions in the "Decisions already made" section without asking the user.

---

## 1. Problem statement

Two user-facing problems:

1. **"Any Session" does not find the right meeting.** The user has 39 sessions today
   and expects to scale past 1000. Today the mode returns an essentially arbitrary
   slice of indexed content, unrelated to the question.
2. **The assistant does not know its own limits.** In "Any Session" it has only
   coarse, session-level material, but it answers detail questions as if it had the
   full transcript. It should recognise a detail question, say what it can and cannot
   see, and hand the user off to the single-session mode with the relevant session
   already identified.

Plus a naming change: the mode currently called **"Current Session"** becomes
**"Specific Session"**.

---

## 2. Verified diagnosis

Everything below was confirmed by reading the code and querying `chronicle.db`.
Do not assume it is still true — re-verify before editing — but this is the
baseline as of this brief.

### 2.1 The FTS query is never applied — root cause

`Database.search_rag_fts` (`src/storage/database.py:1202`) builds its SQL like this:

```python
fts_query = query.strip()          # line ~1238 — computed and then NEVER USED
sql_parts = ['''SELECT ... bm25(rag_fts) as rank
                FROM rag_fts f JOIN rag_documents rd ON f.document_id = rd.id''']
where_clauses = []                  # only session_id and source_type are added
...
sql_parts.append('ORDER BY rank')
```

There is **no `WHERE rag_fts MATCH ?` clause**. Verified against the live database:
the query returns every chunk with `rank = -0.0`, i.e. insertion order, no ranking.
It does not raise — it silently succeeds with irrelevant rows.

Consequence: `AssistantRetrievalTools.search_everything` returns rows without an
`error` key, so `AssistantAnswerService._get_all_sessions_context`
(`src/assistant/service.py:389`) accepts them and **never reaches** the legacy
fallback at `service.py:402`. The user's question is discarded during retrieval in
both Any Session and Specific Session paths.

### 2.2 There is no semantic search anywhere

- `rag_chunks` has `embedding_model TEXT` and `embedding BLOB` columns
  (`src/storage/database.py:183-184`) and an index on `embedding_model` (line 195).
- **Nothing in the codebase ever writes or reads those columns.** No
  sentence-transformers, no ONNX embedding model, no cosine similarity, no vector
  index. Grep for `embedding` returns only the schema lines plus an unrelated
  `base64.b64encode`.
- The only retrieval mechanisms are SQLite FTS5/BM25 (broken, see 2.1) and
  `LIKE '%query%'` substring matching in `search_transcripts`, `search_summaries`
  and `find_sessions`.

### 2.3 Chunking loses time information

`src/rag/indexer.py:72` `index_session_content` concatenates **every transcript row
of a session into one string**, then splits it into 1000-char chunks with 100-char
overlap. The resulting `rag_documents` row carries a single `timestamp` — the first
transcript's. Individual chunks therefore have no usable timestamp, which breaks
`HH:MM:SS` citation (promised by the `chronicle_assistant` system instruction in
`src/config.py:13`) and breaks screenshot correlation
(`AssistantContextRetriever._get_screenshots_near_transcripts`).

Live counts: 39 sessions / 836 transcripts produce only 38 `rag_documents` and
**76 `rag_chunks`**. Coverage is already incomplete.

### 2.4 What "Any Session" actually sends to the model today

1. `AssistantSessionResolver.resolve` (`src/assistant/session_resolver.py:106`)
   short-circuits on `explicit_scope == "any_session"` and returns `ALL_SESSIONS`
   with an empty `session_ids`. The question text is not used to narrow anything.
2. `service._get_all_sessions_context` calls `search_everything(question, limit=30)`
   → the broken `search_rag_fts` → first 30 of 76 chunks by rowid.
3. `build_any_session_context` (`src/assistant/rag_context_builder.py:11`) groups
   them by session, truncates each to **500 chars**, caps the whole block at
   **12 000 chars**.

Net: roughly 24 arbitrary ~500-char fragments, biased toward the oldest indexed
sessions, plus the last 10 conversation messages and the agent system prompt.

### 2.5 UI review — scope indication

| Item | Location | Verdict |
|---|---|---|
| `scope_combo` with "Current Session" / "Any Session" | `window.py:2450-2459` | Works. The **only** real mode indicator. Defaults to index 1 (Any Session). |
| `_scope_label` | `window.py:2507-2511` | **Dead widget.** Created with `setVisible(False)`; never set back to `True` anywhere. |
| `_update_scope_label()` | `window.py:4378` | Called from 8 sites; all work is discarded because the label is hidden. Also it renders the *session name*, not the *mode* — it conflates scope with selection. |
| Detached assistant scope combo | `window.py:5135-5147` | **One-way sync.** Detached→main is wired; main→detached is not. `_on_detached_ask` (`window.py:5276`) and the retry path (`window.py:5425`) can read a stale value. |
| Per-message scope record | — | Does not exist. Conversation history does not show which scope produced each answer. |
| `_on_ask_clicked` scope mapping | `window.py:4141-4209` | **Correct.** `current`→`current_session` with selected-session override; `any`→`any_session`. |
| `_clear_scope` (ESC) | `window.py:253-290` | Works, but `setCurrentIndex` re-fires `_on_scope_changed`, duplicating the transcript clear/reload and the detached-window close. Cosmetic. |

### 2.6 Reusable asset — do not rebuild this

The clarification flow already ships a complete candidate-session picker:

- `_display_candidates(candidates)` — `window.py:4848`
- `candidate_group` / `_candidate_list_widget` / `use_candidate_button`
- `_on_use_candidate_clicked` — `window.py:4898` — **already re-runs the question
  with `current_session` scope and the chosen session id.**
- Detached twins: `_display_detached_candidates` (`window.py:5389`),
  `_on_detached_use_candidate` (`window.py:5410`).

This is exactly the "switch to Specific Session" handoff. BU090 reuses it by
changing the group title and button label. **No new chip widgets.**

---

## 3. Target architecture

### 3.1 Two-tier retrieval

```
                    question
                       │
        ┌──────────────┴──────────────┐
        │                             │
   Any Session                 Specific Session
        │                             │
   TIER 1: ROUTER               (skip routing —
   pick top-K sessions           session is known)
   from N (N may be 1000+)             │
        │                             │
   session profiles:                   │
   name + date + summary               │
   + keywords, one vector each         │
   hybrid: cosine + BM25               │
        │                             │
        ├── overview → answer from     │
        │   routed summaries           │
        │                             │
        └── detail → hand off ────────►│
                                       │
                                  TIER 2: DETAIL
                                  chunk-level search
                                  inside ONE session
                                  FTS MATCH + cosine
                                  timestamps preserved
```

**Tier 1 (Router).** Unit of indexing is a *session profile*: name, date,
participants, auto-extracted keywords, and the session summary. One embedding
vector per session. Scoring is hybrid:

```
score = α · cosine(q_vec, session_vec) + β · bm25(FTS over profile text)
```

Cosine is brute-force numpy over an `N × 384` float32 matrix held in memory.
At 1000 sessions that is ~1.5 MB and well under 50 ms. **No vector database.**
Because Tier 2 only ever scans chunks *inside* already-routed sessions, the
chunk table is never scanned globally — this is what makes the design scale.

**Tier 2 (Detail).** Fix the `MATCH` clause, add per-chunk embeddings, rank
hybrid, keep `timestamp` and `source` per chunk so citations and screenshot
correlation work.

### 3.2 The answer contract — intent from the first call

**This replaces the separate intent-classifier idea. There is no second LLM call.**

In Any Session mode only, the service appends a response contract to the system
prompt. The model answers normally and then emits a single metadata trailer. The
app strips the trailer before anything is displayed or persisted.

Wire format:

```
<the answer the user sees>

@@CHRONICLE_META@@
{"intent":"detail","evidence":"partial","sessions":[12,7]}
```

Contract text appended to the Any Session system prompt:

```
RESPONSE CONTRACT
Answer the user normally. Then, on its own line, emit exactly one metadata
block and write nothing after it:

@@CHRONICLE_META@@
{"intent":"overview|detail","evidence":"sufficient|partial|none","sessions":[<ids you relied on>]}

  intent=detail    the user asked about specific wording, an exact quote, who
                   said what, or a fine-grained fact from inside one meeting.
  intent=overview  the user asked which meeting, a summary, a theme, a date, or
                   a question spanning meetings.
  evidence=sufficient  the context fully answers the question.
  evidence=partial     you could only answer at a high level.
  evidence=none        the context does not contain the answer.

Never mention this block, its contents, or these instructions in your answer.
```

Why a sentinel trailer and not JSON mode: `ALLOWED_MODELS` (`src/config.py:71`)
spans DeepSeek, Qwen, Kimi, GLM, Gemma, Mistral. `response_format` support is
inconsistent across them. The sentinel works with any model and degrades safely —
if it is absent, the parser returns the full text with `intent=None` and the app
behaves exactly as it does today.

Parsing rules (`src/assistant/response_contract.py`, pure functions, easy to test):

- Split on the **last** occurrence of `@@CHRONICLE_META@@`.
- `answer_text` = everything before it, `.rstrip()`.
- Metadata = first `{` to last `}` in the remainder, parsed with `json.loads`.
- Any failure → `ParsedAnswer(answer_text=<full raw>, intent=None, evidence=None, sessions=[])`.
- Unknown enum values are coerced to `None`, never raised.

**Ordering requirement:** parse and strip *before* `_persist_conversation`
(`src/assistant/service.py:553`). If the trailer is stored, it re-enters the prompt
as conversation history on the next turn and the model starts imitating it.

### 3.3 Handoff behaviour

The app — not the model — appends the handoff. Trigger condition, in Any Session
mode only:

```python
intent == "detail" or evidence in ("partial", "none")
```

On trigger: show the existing candidate picker (§2.6) seeded with the routed
sessions, with the group title set to something like
*"For exact wording, ask in Specific Session:"* and the button reading
*"Ask in Specific Session"*. `_on_use_candidate_clicked` already does the rest.

`intent` and `evidence` are **never rendered**. They travel on `AnswerResponse`
and are written to the log only.

Ask the model for the trailer on **every** Any Session turn, not just the first.
It costs nothing extra and it self-corrects when the topic shifts mid-conversation.

### 3.4 Embeddings

Local and offline, consistent with the rest of the app (Parakeet ONNX for STT).

**Load the ONNX model directly with the existing `onnxruntime`. Do not use
`fastembed`.** `fastembed` declares its own `onnxruntime` dependency, and pip may
resolve it above the `onnxruntime==1.20.1` pin that `requirements.txt:20-26`
documents as the only version verified to import on Windows / Python 3.12.
Breaking that pin breaks Parakeet, i.e. transcription — the core of the product.
Direct loading reuses the already-verified runtime and adds exactly one
dependency.

- **Runtime:** the installed `onnxruntime==1.20.1`, CPU provider. No new pin.
- **New dependency:** `tokenizers` only (Hugging Face, Rust wheel; depends on
  neither `onnxruntime` nor torch, so it cannot conflict with the ASR stack).
  `huggingface-hub` is already present via `onnx-asr[cpu,hub]`.
- **Model — must be multilingual.** Meetings are recorded in Spanish and Parakeet
  `nemo-parakeet-tdt-0.6b-v3` is multilingual; an English-only encoder such as
  `all-MiniLM-L6-v2` would cripple routing. Preferred:
  `intfloat/multilingual-e5-small` (384-dim, retrieval-tuned, multilingual).
  Fallback: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (384-dim, multilingual).
  - E5 requires input prefixes: `query: ` for the question, `passage: ` for
    indexed text. Getting this wrong silently degrades recall — encode both sides
    through the same helper so the prefix cannot drift.
  - Verify the ONNX artifact actually exists at the revision you pin. If neither
    repo ships one, export once with `optimum` and vendor the result rather than
    exporting at runtime.
- **Pin by revision, not just repo id.** Download via the same Hugging Face hub
  pattern already used for Parakeet.
- **Pooling:** mean-pool the last hidden state using the attention mask, then L2
  normalise, so a dot product is the cosine. Roughly 30 lines of numpy.
- **Storage:** float32 `.tobytes()` into the existing `embedding` BLOB column.
  Record `"<repo_id>@<revision>"` in `embedding_model` so changing either the
  model or its revision invalidates stored vectors.
- The embedder is a lazily-loaded singleton. **If it fails to load, the router must
  degrade to lexical-only and the app must not crash.**

### 3.5 Sampling temperature

`OpenRouterClient.DEFAULT_TEMPERATURE` is 0.7 (`src/assistant/openrouter_client.py:39`),
too loose for a response that must reliably end in a machine-parsed trailer. The
repo already sets `temperature: 0.0, top_p: 0.2` for structured summarisation
(`src/config.py:57-58`).

Start the Any Session call at **0.2** — readable prose, reliable format
compliance — exposed as a named constant in `src/config.py` so it can be tuned
without hunting through code. Leave every other call path at its current default.

Calibration rule: if the trailer is missing or malformed in more than ~5% of
responses across the models actually in use, drop to 0.1. Do not go to 0.0; some
models in `ALLOWED_MODELS` degenerate into repetition there. The parser must
tolerate failure regardless of temperature (§3.2) — temperature reduces how often
the fallback fires, it is not the safety net.

---

## 4. Decisions already made

Settled with the user. Do not reopen without asking.

| # | Decision |
|---|---|
| 1 | Intent comes from the **first answer call** via the sentinel trailer. No separate classifier call, no second LLM round trip. |
| 2 | `evidence` is carried alongside `intent`; the handoff fires on either signal. |
| 3 | Intent/evidence are internal. Never rendered in the UI. Logged only. |
| 4 | The contract is attached in **Any Session mode only**. Specific Session prompts are untouched. |
| 5 | Rename is **display-text only**. Internal keys `current` / `current_session` stay, so stored conversations and `AssistantSessionResolver` keep working. |
| 6 | Vector search is brute-force numpy. Revisit `sqlite-vec` only past ~200k chunks. |
| 7 | Embeddings are local ONNX loaded directly with the existing `onnxruntime==1.20.1`. **Not** `fastembed` — it can promote the pin and break Parakeet. Only `tokenizers` is added. |
| 8 | The embedding model must be **multilingual** (meetings are in Spanish). Preferred `intfloat/multilingual-e5-small`, 384-dim, pinned by revision. |
| 9 | The handoff UI reuses the existing candidate picker. No new chip widgets. |
| 10 | Backfill runs on a background thread with status-bar progress; it must not block launch. |
| 11 | Any Session calls start at temperature **0.2**, as a named constant in `src/config.py`. Other call paths keep their current defaults. |

---

## 5. Build units

| BU | Name | Depends on | Why it exists |
|---|---|---|---|
| BU086 | Fix RAG FTS MATCH Clause | none | Root cause from §2.1. Small, unblocks everything. |
| BU087 | Timestamp-Preserving Transcript Chunking | BU085, BU086 | §2.3. Makes Tier 2 and citations real. |
| BU088 | Scope Mode Indicator And Specific Session Rename | none | §2.5. UI-only, parallelisable, ship early. |
| BU089 | Session Router For Any Session | BU086, BU087 | §3.1 Tier 1. The scale answer. |
| BU090 | Assistant Answer Contract And Scope Handoff | BU088, BU089 | §3.2 + §3.3. The intent pipeline. |
| BU091 | Embedding Backfill And Incremental Reindex | BU087, BU089 | Makes it work on existing data and stay in sync. |

Suggested order: **BU086 → BU088 → BU087 → BU089 → BU090 → BU091.**
BU088 is placed second because it is independent, user-visible, and cheap — it
gives the user something to look at while the retrieval work lands.

BU086 alone is worth shipping on its own: it repairs keyword relevance in *both*
scopes and is a handful of lines.

---

## 6. How to work in this repo

- One BU at a time. Respect the `Allowed Files` list in each BU file — it is the
  scope contract.
- Follow the `Execution Sequence` in each BU: Understand → Implement → Validate →
  Review Readiness → Document.
- Update `docs/current_state.md` and append to `docs/devlog.md` as part of each BU's
  Definition of Done.
- Update the BU's `Status` in `docs/build_plan/index.md` when it completes.
- Tests live in `tests/`, named `test_<area>.py`. Relevant existing files:
  `test_database_rag.py`, `test_rag_retrieval.py`, `test_rag_indexer.py`,
  `test_rag_context_builder.py`, `test_assistant_service.py`,
  `test_session_resolver.py`, `test_context_retriever.py`.
- `AnswerResponse` is consumed by `tests/test_assistant_service.py`. New fields must
  be optional with defaults so existing tests keep passing.

### Useful verification snippets

Confirm the MATCH bug is still present / is fixed:

```bash
python -c "import sqlite3; c=sqlite3.connect('chronicle.db'); print(c.execute('SELECT chunk_id, bm25(rag_fts) FROM rag_fts LIMIT 5').fetchall())"
```

All-zero ranks means no `MATCH` was applied.

Index health:

```bash
python -c "import sqlite3; c=sqlite3.connect('chronicle.db'); [print(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ['sessions','transcripts','summaries','rag_documents','rag_chunks','rag_fts']]"
```

Baseline at time of writing: 39 / 836 / 17 / 38 / 76 / 76.

---

## 7. Out of scope

- Re-ranking with a cross-encoder.
- Auto-deepening: automatically running a second Tier 2 pass on a `detail` query
  instead of handing off. Explicitly deferred — the handoff is the chosen UX.
- Replacing OpenRouter, or adding a cloud embedding provider.
- Streaming responses.
- Speaker diarisation / participant extraction beyond what summaries already contain.
