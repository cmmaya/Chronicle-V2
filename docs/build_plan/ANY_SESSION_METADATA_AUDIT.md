# Any Session — Metadata Passed To The Model (Investigation, no code changes)

> Question investigated: *what metadata does an "Any Session" question send to the
> LLM, and is the session date/time among it?*
>
> Short answer: **the session date/time is not passed** on the primary path or the
> first fallback. It only appears on the rarely-hit legacy fallback, and even
> there only as a bare session list, never attached to the evidence the model
> reasons over. Several other useful signals are also dropped, and one signal the
> answer contract *depends on* (session ids) is never given to the model at all.

---

## 1. The call that gets built

`AssistantAnswerService.ask` → `_build_messages`
(`src/assistant/service.py:624`) produces exactly three things for an Any Session
turn:

| Message | Content | Notes |
|---|---|---|
| `system` | `agent.system_instruction` + `"\n\nContext:\n"` + `context_text` + `"\n\n"` + `RESPONSE_CONTRACT` | contract only in Any Session mode |
| history | every prior message of the conversation, `{role, content}` only | `db.get_messages` — **uncapped**, no timestamps, no scope tag |
| `user` | the raw question string | no date anchor added |

`agent.system_instruction` — the **default agent is `research_helper`**
(`src/config.py:6`), whose prompt says nothing about dates or timestamps.
`chronicle_assistant` *does* say "include the session name and timestamp
(HH:MM:SS) when available" (`src/config.py:18`) but it is not the default, and no
date is ever made available to cite regardless.

`RESPONSE_CONTRACT` (`src/assistant/response_contract.py:25`) asks the model to
emit `{"intent":..., "evidence":..., "sessions":[<ids you relied on>]}`.

All the real "metadata" therefore lives inside **`context_text`**.

---

## 2. How `context_text` is built — three different shapes

`_get_all_sessions_context` (`src/assistant/service.py:487`) tries three tiers in
order and returns the first that produces something:

### Tier A — routed context (primary): `build_routed_session_context`
`src/assistant/rag_context_builder.py:103`

Per routed session (max `k=5`, total budget 12 000 chars):

```
## <session_name>  (<reason>)
[summary]: <session summary, truncated to 500 chars>
[<source_type> @<HH:MM:SS>][<title>]: <chunk text, truncated to 500 chars>
[<source_type> @<HH:MM:SS>][<title>]: <chunk text ...>       (up to 4 chunks)
```

- `<reason>` is router-internal debug text: one of / a mix of `topic match`,
  `keyword match`, `mentions <kw>, <kw>`, `weak match`
  (`src/rag/router.py:219`).
- `<source_type>` is `transcript` or `summary`.
- `<HH:MM:SS>` is `datetime.fromtimestamp(chunk.start_timestamp)` — an absolute
  Unix time rendered **time-of-day only, date stripped** (`_format_result`,
  `rag_context_builder.py:186`). For chunks indexed before BU087 it silently
  falls back to the *document* timestamp (first transcript of the session).

**Not included on this path:** session date, session id, router score,
mic-vs-system speaker/source (`search_rag_fts` returns `source` — `'microphone'`
/ `'system'` — and the builder drops it), screenshots.

### Tier B — `build_any_session_context`
`src/assistant/rag_context_builder.py:16` (fires when routing returns nothing but
`search_everything` works)

```
## <session_name>
[<source_type> @<HH:MM:SS>][<title>]: <chunk text, truncated to 500 chars>
```

Same omissions as Tier A — no date, no id, no speaker.

### Tier C — legacy fallback: `_get_all_sessions_context_legacy`
`src/assistant/service.py:529`

This is the **only** path that renders dates:

```
## Sessions
[<name> (YYYY-MM-DD HH:MM)] - Transcribed: <status>, Summarized: <status>
...
## Relevant Summaries
[<session_name>]: <summary content, 500 chars>
## Relevant Transcripts
[<session_name>]: <transcript text, 300 chars>
```

- Date/time is present, but only in a flat session **list**, disconnected from
  the summary/transcript evidence below it.
- `Transcribed:/Summarized:` status is internal pipeline state, not useful to a
  content answer.

---

## 3. What IS passed today (summary)

| Signal | Tier A (primary) | Tier B | Tier C (legacy) |
|---|---|---|---|
| Session name | ✅ header | ✅ header | ✅ |
| Session **date/time** | ❌ | ❌ | ✅ (list only) |
| Session id | ❌ | ❌ | ❌ |
| Session summary text | ✅ | ❌ | ✅ |
| Transcript chunk text | ✅ (≤4/session) | ✅ | ✅ (≤10 total) |
| Chunk time-of-day | ✅ `HH:MM:SS` | ✅ `HH:MM:SS` | ❌ |
| Chunk **date** | ❌ | ❌ | ❌ |
| Speaker / mic-vs-system | ❌ | ❌ | ❌ |
| Router match reason | ✅ (`weak match` etc.) | ❌ | ❌ |
| Router score | ❌ | ❌ | ❌ |
| Screenshots | ❌ | ❌ | ❌ |
| "Today's date" anchor | ❌ | ❌ | ❌ |

Note: `build_session_profile` (`src/rag/indexer.py:379`) *does* fold
`YYYY-MM-DD` into the router's profile text and embedding, so the router can
match on date — but that date is never propagated into the answer context.

---

## 4. Findings

1. **Session date/time is effectively never given to the model.** On the two
   paths that normally fire (A and B) it is absent entirely. This breaks the
   whole class of questions the contract calls `intent=overview`: "which
   meeting", "when did we…", "the meeting last Tuesday", anything needing
   chronological ordering across sessions.

2. **Session ids are never in the context, yet the contract asks for them.**
   `RESPONSE_CONTRACT` requests `"sessions":[<ids you relied on>]`, and
   `parse_answer` → `build_scope_offer(cited_session_ids=…)`
   (`src/assistant/service.py:387`) feeds those ids straight into the BU092
   scope-offer decision. The model has to invent ids from nothing, so
   `cited_session_ids` is unreliable and the "first cited session" branch of
   `_pick_target` (`scope_offer.py:36`) rarely does what it should.

3. **Chunk timestamps carry no date.** `%H:%M:%S` only. Multi-day sessions and
   any cross-session "when" reasoning are impossible even though the underlying
   value is a full Unix timestamp.

4. **Speaker / source is dropped.** `search_rag_fts` returns per-chunk `source`
   (`microphone`/`system`); `build_routed_session_context` ignores it.
   `intent=detail` is explicitly defined as "who said what… from inside one
   meeting" — unanswerable without at least the mic/system split.

5. **No "today" anchor in the prompt.** Relative dates ("yesterday", "last
   week") cannot be resolved.

6. **Router debug text leaks into the model context.** `(weak match)`,
   `(mentions foo, bar)` in the header is internal scoring commentary. `weak
   match` can make the model distrust otherwise-good evidence; `mentions …` is
   derived from crude keyword overlap and can misdirect.

7. **Three different context formats.** The model's input shape changes
   depending on which tier fired, so prompt behaviour is inconsistent and hard
   to tune.

8. **Conversation history is uncapped.** `_build_messages` calls
   `db.get_messages` (all messages), not `get_recent_messages`. Long
   conversations crowd out the 12 000-char context budget. (Brief §2.4 assumes
   "last 10" — the code does not enforce it.)

---

## 5. Recommendations

### Add

| # | What | Where | Why |
|---|---|---|---|
| A1 | Session **date + time** in the routed header, e.g. `## <name> — 2026-08-14 15:30` | `build_routed_session_context`, `build_any_session_context` | `RoutedSession.start_time` is already carried into `_last_routed_sessions`; only formatting is missing. Unlocks all "which/when" questions. |
| A2 | Session **id** in the header, e.g. `## [S12] <name> — <date>` | both routed builders + the contract text | Makes `"sessions":[…]` in the trailer real; fixes the BU092 `cited_session_ids` path. |
| A3 | **Date** on chunk lines (or one `Date:` line per session block) | `_format_result` | Cross-session and multi-day "when did we discuss X". |
| A4 | **Speaker/source** tag on transcript chunks (`mic`/`system`, or real speaker if ever available) | `build_routed_session_context` (value already returned by `search_rag_fts`) | Required for `intent=detail` "who said what". |
| A5 | **"Current date: YYYY-MM-DD"** line in the Any Session system prompt | `_build_messages` / `RESPONSE_CONTRACT` block | Resolve relative dates. |
| A6 | A compact **routed-session index** preamble (`id · name · date · one-line summary`) before the per-session evidence | `build_routed_session_context` | Clean source for overview/"which meeting" answers instead of inferring from headers. |

### Remove / change

| # | What | Why |
|---|---|---|
| R1 | Drop the `(<reason>)` router text from the header (keep it in logs / the UI candidate list) | Internal debug signal; `weak match` actively undermines the answer. |
| R2 | Replace `title` (`"Session 12 Transcript"`, `"Session 12 - detailed"`) with the `summary_type` alone, or drop it | Redundant with the header once id+name+date are there. |
| R3 | In the legacy fallback, drop `Transcribed:/Summarized:` status | Internal pipeline state, irrelevant to a content answer. |
| R4 | Cap conversation history (use `get_recent_messages`, ~10–20) in `_build_messages` | Stop history from eating the context budget. |
| R5 | Unify the header/metadata format across `build_routed_session_context` and `build_any_session_context` | One predictable input shape to tune the contract against. |

### Keep out (already correct)

- Router `score` — not rendered into context today; leave it that way.
- `intent` / `evidence` — internal, logged only; do not render.

---

## 6. Relevant files

- `src/assistant/service.py` — `_build_messages` (`:624`), `_get_all_sessions_context` (`:487`), `_get_all_sessions_context_legacy` (`:529`), `_finalize_answer` (`:359`)
- `src/assistant/rag_context_builder.py` — `build_routed_session_context` (`:103`), `build_any_session_context` (`:16`), `_format_result` (`:186`)
- `src/assistant/response_contract.py` — `RESPONSE_CONTRACT` (`:25`), `parse_answer` (`:53`)
- `src/assistant/scope_offer.py` — `build_scope_offer` / `_pick_target` (consumers of `cited_session_ids`)
- `src/rag/router.py` — `RoutedSession` (`:50`, carries `start_time`), `_reason` (`:219`)
- `src/rag/indexer.py` — `build_session_profile` (`:379`, folds date into the profile)
- `src/storage/database.py` — `search_rag_fts` (`:1304`, returns `timestamp` / `start_timestamp` / `source`), `add_transcript` (`:479`, stores absolute Unix time)
