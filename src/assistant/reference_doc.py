"""Text decoding and per-question excerpts for session documents (BU117, BU151).

A lecture that says "as in exercise 4" cannot be answered from the recording,
because exercise 4 is in a handout the recording never contains. Session
documents (``session_documents``) carry that handout; this module holds the two
pieces they share:

- ``decode_text``: bytes to text, UTF-8 first and then the platform encoding;
- ``select_relevant_excerpt``: the part of a document worth sending with one
  question, chosen deterministically.

Qt-free and network-free, so both can be tested without a window.
"""
from __future__ import annotations

import locale
import re
from typing import List, Optional

from ..config import SESSION_DOCUMENTS

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Terms this short are in every question ever asked and match every paragraph,
# so scoring on them ranks nothing.
_MIN_TERM_LENGTH = 3


class ReferenceDocError(Exception):
    """A document could not be loaded. Carries a message fit to show a user."""


def decode_text(raw: bytes) -> str:
    """``raw`` as text: UTF-8, else the platform encoding (BU117, BU147)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    fallback = locale.getpreferredencoding(False) or "cp1252"
    try:
        return raw.decode(fallback)
    except (UnicodeDecodeError, LookupError) as e:
        raise ReferenceDocError(
            f"That file is not UTF-8 or {fallback} text"
        ) from e


def select_relevant_excerpt(doc, question: str, max_chars: Optional[int] = None) -> str:
    """The part of ``doc`` (anything with a ``text`` attribute) worth sending
    with ``question``.

    A syllabus does not fit in every prompt, so paragraphs are scored against
    the question's terms - the same whitespace-split, case-insensitive
    substring matching the find bar uses - and the best *contiguous* run under
    the cap is returned. Contiguous rather than best-N, because a problem set's
    answer usually straddles the paragraph that names it and the one after.

    Deterministic throughout: equal-scoring spans resolve to the earliest one,
    so the same question always yields the same excerpt. A question that
    matches nothing falls back to the top of the document, which for a document
    that fits under the cap is the whole of it.
    """
    cap = (SESSION_DOCUMENTS["excerpt_chars_per_document"]
           if max_chars is None else max_chars)
    paragraphs = _paragraphs(getattr(doc, "text", "") or "")
    if not paragraphs or cap <= 0:
        return ""

    terms = _question_terms(question)
    scores = [_score(p, terms) for p in paragraphs]

    # Ranked by score, then by the earliest start, then by the longest span -
    # in that order, and with no other input, so the result is a function of
    # the document and the question alone.
    best = None
    for start in range(len(paragraphs)):
        length = 0
        for end in range(start, len(paragraphs)):
            # +2 for the blank line that rejoins two paragraphs.
            length += len(paragraphs[end]) + (2 if end > start else 0)
            if length > cap:
                break  # lengths only grow, so no longer span from here fits
            key = (sum(scores[start:end + 1]), -start, end)
            if best is None or key > best[0]:
                best = (key, start, end)

    if best is None:
        # Every paragraph on its own is over the cap; send the head of the
        # first one rather than nothing.
        return paragraphs[0][:cap].strip()
    _, start, end = best
    return "\n\n".join(paragraphs[start:end + 1])


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _question_terms(question: str) -> set:
    return {
        w.lower() for w in _WORD_RE.findall(question or "")
        if len(w) >= _MIN_TERM_LENGTH
    }


def _score(paragraph: str, terms: set) -> int:
    """How many distinct question terms this paragraph contains."""
    if not terms:
        return 0
    lowered = paragraph.lower()
    return sum(1 for term in terms if term in lowered)
