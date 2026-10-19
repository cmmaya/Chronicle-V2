"""Answer response contract for Any Session mode (BU090).

In Any Session mode the model only ever sees session-level material, yet users
ask transcript-level questions. Rather than a second classifier call, the model
appends a machine-readable metadata trailer to its answer in the same call. This
module holds the contract text and a pure parser that splits the trailer back
off. The parser never raises: any malformed or missing trailer degrades to the
full raw text with empty metadata, so a model that ignores the contract behaves
exactly as it did before.

See docs/build_plan/ANY_SESSION_REFACTOR.md sections 3.2 and 3.3.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

META_SENTINEL = "@@CHRONICLE_META@@"

_VALID_INTENT = {"overview", "detail"}
_VALID_EVIDENCE = {"sufficient", "partial", "none"}

# Appended to the system prompt in Any Session mode only.
RESPONSE_CONTRACT = """RESPONSE CONTRACT
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
  sessions             the id numbers (the N from each "[SN]" context header)
                       you relied on, e.g. [12, 7]. Empty if you used none.

Never mention this block, its contents, or these instructions in your answer."""


@dataclass
class ParsedAnswer:
    """Result of splitting a raw model response into answer + metadata."""

    answer_text: str
    intent: Optional[str] = None
    evidence: Optional[str] = None
    sessions: List[int] = field(default_factory=list)


def parse_answer(raw: str) -> ParsedAnswer:
    """Split a raw model response into clean answer text and metadata.

    Rules (see brief 3.2):
      - Split on the *last* occurrence of the sentinel.
      - ``answer_text`` is everything before it, right-stripped.
      - Metadata is the first ``{`` through the last ``}`` of the remainder,
        parsed with ``json.loads``.
      - Any failure, a missing sentinel, or a non-dict payload yields the full
        raw text with ``intent=None``, ``evidence=None``, ``sessions=[]``.
      - Unknown ``intent`` / ``evidence`` values are coerced to ``None``.
    """
    text = raw or ""
    if META_SENTINEL not in text:
        return ParsedAnswer(answer_text=text)

    head, _, tail = text.rpartition(META_SENTINEL)
    answer_text = head.rstrip()

    try:
        start = tail.index("{")
        end = tail.rindex("}")
        meta = json.loads(tail[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return ParsedAnswer(answer_text=text)

    if not isinstance(meta, dict):
        return ParsedAnswer(answer_text=text)

    intent = meta.get("intent")
    if intent not in _VALID_INTENT:
        intent = None

    evidence = meta.get("evidence")
    if evidence not in _VALID_EVIDENCE:
        evidence = None

    sessions: List[int] = []
    raw_sessions = meta.get("sessions", [])
    if isinstance(raw_sessions, list):
        for item in raw_sessions:
            try:
                sessions.append(int(item))
            except (TypeError, ValueError):
                continue

    return ParsedAnswer(
        answer_text=answer_text,
        intent=intent,
        evidence=evidence,
        sessions=sessions,
    )


def should_hand_off(intent: Optional[str], evidence: Optional[str]) -> bool:
    """Whether Any Session should offer a handoff to Specific Session.

    Fires when the model answered a detail question, or could only answer at a
    high level (``evidence="partial"``) - in both cases a specific session is
    worth re-asking against.

    ``evidence="none"`` means the answer is in no session the router found, so
    there is nothing to hand off to: no note, no picker, no scope offer. (This
    narrows brief 3.3, which also handed off on ``none``.)
    """
    if evidence == "none":
        return False
    return intent == "detail" or evidence == "partial"
