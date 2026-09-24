"""A dropped .txt file used as answer-side evidence (BU117).

A lecture that says "as in exercise 4" cannot be answered from the recording,
because exercise 4 is in a handout the recording never contains. Dropping that
handout on the detached window makes it a second evidence tier, between the
transcript and general knowledge.

Scope, deliberately narrow:

- plain text only. No parsing, no extraction, no format detection;
- scratch state belonging to the open window. Nothing here touches the
  database, the RAG corpus or the recording pipeline;
- Qt-free and network-free, like ``live_qa``'s question-assembly half, so the
  loading rules and the excerpt choice can be tested without a window.
"""
from __future__ import annotations

import locale
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ..config import REFERENCE_DOC

# The labelled block appended to a question. Distinct from the transcript's
# "[HH:MM:SS] Mic: ..." lines so the model can attribute an answer to one
# source or the other, which is the whole point of the labelled tiers.
REFERENCE_BLOCK_PREAMBLE = (
    "Reference document the user attached, for evidence. It is not the "
    "question and it is not transcript:"
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Terms this short are in every question ever asked and match every paragraph,
# so scoring on them ranks nothing.
_MIN_TERM_LENGTH = 3


class ReferenceDocError(Exception):
    """A document could not be loaded. Carries a message fit to show a user."""


@dataclass
class ReferenceDoc:
    """One attached document. Held by the open window and nowhere else."""

    name: str
    path: str
    text: str
    char_count: int
    loaded_at: datetime

    @property
    def size_label(self) -> str:
        """``12 KB`` / ``840 B``, for the chip in the answers rail."""
        if self.char_count >= 1024:
            return f"{self.char_count // 1024} KB"
        return f"{self.char_count} B"


def is_allowed_extension(path: str) -> bool:
    """Whether ``path`` has one of the allowed extensions, case-insensitively."""
    allowed = REFERENCE_DOC.get("allowed_extensions", [".txt"])
    return os.path.splitext(path or "")[1].lower() in allowed


def load_reference_text(path: str, max_bytes: Optional[int] = None) -> ReferenceDoc:
    """Read ``path`` as a reference document.

    Decoded as UTF-8 first. A file saved by a Windows editor in the platform
    encoding (cp1252 here) is not valid UTF-8, so the decode is retried in
    ``locale.getpreferredencoding``; only if that also fails is the file
    refused. Line endings are normalised to ``\\n`` so paragraph splitting does
    not have to know which platform wrote the file.

    Raises ``ReferenceDocError`` - with a message meant for the status bar - for
    a missing file, a directory, a disallowed extension, a file over the size
    cap, an unreadable file, or binary content behind a ``.txt`` name. It never
    returns a partially loaded document: either the whole text is in hand, or
    nothing is.
    """
    cap = REFERENCE_DOC.get("max_bytes", 512 * 1024) if max_bytes is None else max_bytes

    if not path or not os.path.exists(path):
        raise ReferenceDocError("That file no longer exists")
    if os.path.isdir(path):
        raise ReferenceDocError("Folders cannot be attached - drop a .txt file")
    if not is_allowed_extension(path):
        allowed = ", ".join(REFERENCE_DOC.get("allowed_extensions", [".txt"]))
        raise ReferenceDocError(f"Only {allowed} files can be attached")

    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise ReferenceDocError(f"Could not read that file: {e}") from e
    if size > cap:
        raise ReferenceDocError(
            f"That file is {size // 1024} KB; the limit is {cap // 1024} KB"
        )

    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as e:
        raise ReferenceDocError(f"Could not read that file: {e}") from e

    # A .txt name proves nothing about the contents, and a binary blob in the
    # prompt is spend with no chance of an answer.
    if b"\x00" in raw:
        raise ReferenceDocError("That file is not readable text")

    text = _decode(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return ReferenceDoc(
        name=os.path.basename(path),
        path=path,
        text=text,
        char_count=len(text),
        loaded_at=datetime.now(),
    )


def _decode(raw: bytes) -> str:
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


def select_relevant_excerpt(doc: Optional[ReferenceDoc], question: str,
                            max_chars: Optional[int] = None) -> str:
    """The part of ``doc`` worth sending with ``question``.

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
    cap = (REFERENCE_DOC.get("max_excerpt_chars", 4000)
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


def build_reference_block(excerpt: str) -> str:
    """The labelled block appended to a question. Empty for an empty excerpt."""
    excerpt = (excerpt or "").strip()
    if not excerpt:
        return ""
    return f"{REFERENCE_BLOCK_PREAMBLE}\n\n{excerpt}"


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
