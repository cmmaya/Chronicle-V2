"""Live Q&A over the transcript stream (BU113, BU114, BU116, BU117).

Two halves, and they stay separable on purpose:

- question assembly (BU113, BU116): turning the chunks a user selected in the
  detached window - or the question the detector found - into the one question
  string handed to the assistant, and choosing the instruction the answer is
  written under - including the three-tier one an attached reference document
  earns (BU117; the document itself lives in ``reference_doc``). Pure text, no
  network;
- question detection (BU114): watching the live stream and emitting
  de-duplicated candidates. This half does call OpenRouter, on its own worker
  thread, and is best-effort - it must never raise into the transcription path.

No Qt anywhere in this module; the window owns all of that.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, List, Optional

from ..config import LIVE_QA, REFERENCE_DOC

logger = logging.getLogger(__name__)

# The recorder overlaps consecutive chunks, so a chunk usually opens with the
# tail of the one before it. Anything longer than this many words is not
# overlap, it is the speaker actually repeating themselves.
MAX_OVERLAP_WORDS = 12

_WORD_RE = re.compile(r"\w+", re.UNICODE)

QUESTION_PREAMBLE = (
    "Answer the question in the following transcript excerpt from the session "
    "being viewed. Use the session's own evidence."
)

# BU116: a detected candidate already carries the question the detector paid to
# identify, so it is stated outright and the transcript follows under its own
# heading. The old path sent the window alone and made the model re-find the
# question inside it.
CANDIDATE_QUESTION_PREAMBLE = (
    "Answer this question, asked during the session being viewed:"
)
# The transcript that leads up to the question is the evidence to check, not
# background: the answer to a question is usually said minutes before it, and
# during a live session the search index does not exist yet, so this block is
# the only place the model can find it.
CANDIDATE_CONTEXT_PREAMBLE = (
    "Session transcript up to the question, oldest first. This is the "
    "evidence to check. Answer only the question above, not other questions "
    "in it."
)
EARLIER_TRANSCRIPT_PREAMBLE = (
    "Session transcript before the excerpt, oldest first. This is the "
    "evidence to check."
)


def _source_label(source: Optional[str]) -> str:
    return "Mic" if (source or "").lower() == "mic" else "System"


def _time_label(record) -> str:
    start = getattr(record, "start_dt", None)
    return start.strftime("%H:%M:%S") if start is not None else "--:--:--"


def _normalize(word: str) -> str:
    return word.lower().strip(".,;:!?\"'()[]")


def collapse_overlap(previous: str, current: str) -> str:
    """Drop the leading words of ``current`` that merely repeat ``previous``.

    The recorder's chunks overlap, so chunk N+1 typically restates the last few
    words of chunk N. Joining them raw makes a selection read as a stutter and
    wastes tokens. Matching is done on normalized words (case and trailing
    punctuation ignored) but the surviving text is returned verbatim, so
    nothing is silently reworded.

    Returns ``current`` unchanged when no overlap is found, and an empty string
    when ``current`` is wholly contained in ``previous``.
    """
    if not previous or not current:
        return current

    prev_words = _WORD_RE.findall(previous)
    curr_tokens = list(_WORD_RE.finditer(current))
    if not prev_words or not curr_tokens:
        return current

    prev_norm = [_normalize(w) for w in prev_words]
    curr_norm = [_normalize(m.group()) for m in curr_tokens]

    # Longest run of leading current words that is also the trailing run of
    # previous. Longest first, so "the value at the" wins over "the".
    limit = min(MAX_OVERLAP_WORDS, len(prev_norm), len(curr_norm))
    for size in range(limit, 0, -1):
        if prev_norm[-size:] == curr_norm[:size]:
            if size == len(curr_tokens):
                return ""
            return current[curr_tokens[size].start():].lstrip()
    return current


def _context_budget() -> int:
    return int(LIVE_QA.get("answer_context_chars", 0) or 0)


def build_question_from_records(records: Iterable, earlier: Iterable = ()) -> str:
    """Assemble the question text for a set of selected transcript chunks.

    Chunks are ordered by start time, each line prefixed with its source label
    and ``HH:MM:SS``, and the recorder's chunk-overlap duplication is collapsed
    between consecutive chunks from the same source.

    ``earlier`` is the transcript before the selection. Its newest lines, up
    to ``LIVE_QA["answer_context_chars"]``, follow the excerpt as evidence.

    Returns an empty string when there is nothing to ask about.

    This is the *manual* path: the user picked the chunks, so the intent is
    implicit in the selection and the excerpt is the question. A detected
    candidate goes through ``build_question_for_candidate`` instead.
    """
    body = transcript_block(records)
    if not body:
        return ""
    question = f"{QUESTION_PREAMBLE}\n\n{body}"
    evidence = transcript_block(earlier, max_chars=_context_budget())
    if evidence:
        question += f"\n\n{EARLIER_TRANSCRIPT_PREAMBLE}\n\n{evidence}"
    return question


def build_question_for_candidate(candidate, records: Iterable) -> str:
    """Assemble the question text for a detected candidate (BU116).

    The detector's own ``candidate.text`` is stated as the question, and
    ``records`` - the session transcript up to and including the question -
    follow as the evidence, trimmed to its newest lines within
    ``LIVE_QA["answer_context_chars"]``. Falls back to the question alone when
    there is no transcript: less evidence is never a reason to send no
    question.
    """
    question = (getattr(candidate, "text", "") or "").strip()
    if not question:
        # Nothing was detected worth asking; the excerpt is all there is.
        return build_question_from_records(records)

    context = transcript_block(records, max_chars=_context_budget())
    if not context:
        return question
    return (f"{CANDIDATE_QUESTION_PREAMBLE}\n\n{question}\n\n"
            f"{CANDIDATE_CONTEXT_PREAMBLE}\n\n{context}")


def strip_transcript_evidence(question: str) -> str:
    """``question`` without the transcript evidence block the builders append.

    For matching the question against something else (the reference
    document): minutes of transcript would outvote the question's own words.
    """
    for preamble in (CANDIDATE_CONTEXT_PREAMBLE, EARLIER_TRANSCRIPT_PREAMBLE):
        question = question.split(f"\n\n{preamble}", 1)[0]
    return question


def answer_instruction(mode: str, reference_name: str = "") -> str:
    """The system instruction one answer mode answers with (BU116, BU117).

    An unknown mode falls back to ``transcripts``: the evidence-first prompt is
    the safe one to be wrong with, because it labels where its answer came
    from.

    ``reference_name`` is the attached document's filename (BU117). It adds the
    middle evidence tier, and only to Transcripts mode - General Knowledge is
    the mode that does not check evidence, and a document is evidence.
    """
    instructions = LIVE_QA.get("answer_instructions", {})
    if mode == "general":
        return instructions.get("general", "")
    if reference_name:
        template = REFERENCE_DOC.get("answer_instruction")
        if template:
            return template.format(name=reference_name)
    return instructions.get(mode) or instructions.get("transcripts", "")


def transcript_block(records: Iterable, max_chars: int = 0) -> str:
    """The ``[HH:MM:SS] Mic: ...`` lines for ``records``, overlap collapsed.

    The shared body of both question builders. Empty when there is nothing
    quotable in ``records``. ``max_chars`` keeps only the newest whole lines
    that fit, so a long session costs a bounded amount per answer; 0 keeps
    everything.
    """
    ordered = _ordered(records)
    if not ordered:
        return ""

    lines: List[str] = []
    last_text_by_source = {}
    for record in ordered:
        source = (getattr(record, "source", "") or "").lower()
        text = (getattr(record, "text", "") or "").strip()
        if not text:
            continue
        # Overlap only happens between consecutive chunks of the same stream;
        # mic and system are recorded independently.
        collapsed = collapse_overlap(last_text_by_source.get(source, ""), text)
        last_text_by_source[source] = text
        if not collapsed:
            continue
        lines.append(f"[{_time_label(record)}] {_source_label(source)}: {collapsed}")

    if max_chars > 0:
        kept, used = [], 0
        for line in reversed(lines):
            used += len(line) + 1
            if used > max_chars and kept:
                break
            kept.append(line)
        lines = kept[::-1]
    return "\n".join(lines)


def timestamp_range(records: Iterable) -> str:
    """``HH:MM:SS`` for one chunk, ``HH:MM:SS - HH:MM:SS`` for several."""
    ordered = _ordered(records)
    if not ordered:
        return ""
    first = _time_label(ordered[0])
    last_end = getattr(ordered[-1], "end_dt", None) or getattr(ordered[-1], "start_dt", None)
    last = last_end.strftime("%H:%M:%S") if last_end is not None else first
    return first if first == last else f"{first} - {last}"


def _ordered(records: Iterable) -> list:
    """Records sorted by start time, with unknown times kept in arrival order.

    Timed and untimed records are sorted separately rather than through one
    key: a datetime and a fallback index are not comparable, and an untimed
    chunk has no defensible place among timed ones anyway, so it keeps the
    order it arrived in and follows them.
    """
    materialized = [r for r in (records or []) if r is not None]
    timed = [r for r in materialized if getattr(r, "start_dt", None) is not None]
    untimed = [r for r in materialized if getattr(r, "start_dt", None) is None]
    timed.sort(key=lambda r: r.start_dt)
    return timed + untimed


# =========================
# Question detection (BU114)
# =========================

# Neutral labels only. Class-vs-meeting policy is a local filter the caller
# applies to these fields (BU115), not a second prompt - one call serves both,
# and a prompt that already knows the answer it is supposed to give is a prompt
# that stops measuring anything.
DETECTOR_SYSTEM_PROMPT = """\
You find questions in a live meeting or lecture transcript.

Each line is "[HH:MM:SS] Mic: ..." or "[HH:MM:SS] System: ...". "Mic" is the \
person running the recording; "System" is everyone else, arriving through \
their speakers. The transcript is automatic speech-to-text cut into chunks of \
a few seconds: a question is often split across two or three lines, may have \
no question mark, and may contain misheard words, stutters and filler. Read \
the lines as one continuous stream.

The excerpt has up to two parts. Lines under "Earlier:" were already checked; \
they are there only so you can read a question that started before the new \
lines. Report only questions that end in the lines under "New:". Skip a \
question still unfinished at the last line - it comes back, complete, with \
the next excerpt.

Return ONLY a JSON array. One object per question, and [] when there is none. \
Never wrap it in prose.

Each object:
{
  "text": the whole question, joined across lines and lightly cleaned of \
stutters and filler,
  "asker": "mic" or "system",
  "kind": one of:
      "genuine"    - a real request for information,
      "rhetorical" - asked to make a point, no answer wanted,
      "discourse"  - a filler check like "any questions?" or "right?",
      "request"    - asking for an action, not information,
  "directed_at_user": true if aimed at the person running the recording,
  "answerable": false only when the answer is something only a participant \
can supply now - their opinion, a decision, a plan. Factual questions are \
answerable, including ones about what a speaker said, has or did earlier in \
the session, which you cannot see,
  "confidence": 0.0 to 1.0
}

Do not invent questions that are not in the excerpt.\
"""

# Question words that signal a question wherever they fall. Speech-to-text
# chunks are cut every few seconds, so "question um what is the law" is common
# and a start-of-clause rule alone misses it. Auxiliaries ("is", "do") are too
# common mid-sentence to count anywhere, so they only count at a clause start.
_WH_WORDS = frozenset({
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
})

_INTERROGATIVE_WORDS = frozenset({
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "is", "are", "was", "were", "do", "does", "did", "can", "could", "should",
    "would", "will", "shall", "may", "might", "have", "has", "had", "am",
    "any", "anyone", "anybody",
})

# Normalized discourse phrases, built once from config.
_DISCOURSE = frozenset(
    re.sub(r"[^\w\s]", "", p).lower().strip()
    for p in LIVE_QA.get("discourse_blocklist", [])
)


@dataclass
class DetectedQuestion:
    """One candidate the detector surfaced, with neutral labels only."""

    text: str
    asker: str  # 'mic' | 'system'
    kind: str  # genuine | rhetorical | discourse | request
    directed_at_user: bool
    answerable: bool
    confidence: float
    record_indices: list = field(default_factory=list)
    detected_at: Optional[datetime] = None


def normalize_question(text: str) -> str:
    """Casefold, strip punctuation and collapse whitespace, for dedupe."""
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def is_discourse(text: str) -> bool:
    """Whether ``text`` is a filler check the blocklist already covers.

    Applied before any paid call. Every model tested classified these
    correctly, so spending a call on them buys nothing.
    """
    normalized = normalize_question(text)
    return bool(normalized) and normalized in _DISCOURSE


def local_gate(window_text: str) -> bool:
    """Free pre-filter: could this window plausibly contain a question?

    Measured at a 37% pass rate on session 46 - it exists to cut calls and
    latency, not to decide anything. It is deliberately generous: a window with
    a "?" always passes, so does a wh-word anywhere, and so does a clause
    opening with an auxiliary, because a transcript of speech frequently has
    no question mark at all. It must never be the only thing concluding a
    question is absent, which is why it only ever gates a call, never a result.
    """
    if not window_text:
        return False
    if "?" in window_text:
        return True
    for line in window_text.splitlines():
        # Drop the "[HH:MM:SS] Mic:" prefix before looking at the words.
        body = line.split(":", 3)[-1] if "]" in line else line
        if any(_normalize(w) in _WH_WORDS for w in body.split()):
            return True
        for clause in re.split(r"[.,;!]", body):
            words = clause.split()
            if words and _normalize(words[0]) in _INTERROGATIVE_WORDS:
                return True
    return False


def parse_detection(raw: str) -> List[DetectedQuestion]:
    """Parse the detector's reply into candidates.

    Strict about the shape and tolerant about the packaging: a markdown fence
    around the JSON is common and is stripped. Anything that does not parse
    returns ``[]`` - a detector that answered badly must not take down the
    recording it is watching.
    """
    if not raw or not raw.strip():
        return []

    body = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", body, re.DOTALL)
    if fence:
        body = fence.group(1).strip()

    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        logger.debug("Detector returned unparseable JSON: %r", raw[:200])
        return []

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []

    detections = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        asker = (item.get("asker") or "").lower()
        detections.append(DetectedQuestion(
            text=text,
            asker=asker if asker in ("mic", "system") else "system",
            kind=(item.get("kind") or "genuine").lower(),
            directed_at_user=bool(item.get("directed_at_user")),
            answerable=bool(item.get("answerable")),
            confidence=max(0.0, min(1.0, confidence)),
        ))
    return detections


def window_is_all_discourse(records: Iterable) -> bool:
    """Whether every utterance in a window is filler the blocklist covers.

    Applied to the spoken text, not to the formatted "[HH:MM:SS] Mic: ..."
    line - a blocklist phrase never matches a whole formatted line. Suppression
    needs *all* of them to be filler: a window holding "any questions?" next to
    a real question is still worth a call.
    """
    texts = [
        (getattr(r, "text", "") or "").strip()
        for r in (records or ())
    ]
    texts = [t for t in texts if t]
    return bool(texts) and all(is_discourse(t) for t in texts)


def dedupe(candidate: DetectedQuestion, seen: set) -> bool:
    """Whether ``candidate`` is new. Records it in ``seen`` when it is.

    ``seen`` is session-global and never expires. Measured on session 46:
    duplicates are overwhelmingly exact repeats of the same utterance across
    overlapping windows, and a non-expiring set also catches the case a TTL
    would miss - the same stale question resurfacing 40 s later.
    """
    key = normalize_question(candidate.text)
    if not key or key in seen:
        return False
    seen.add(key)
    return True


def passes_mode_policy(candidate, directed_at_user_only: bool = False) -> bool:
    """Whether a candidate is worth putting in front of the user (BU115).

    The detector emits neutral labels; this is where the product decision
    lives, deliberately outside the prompt so it can change without a
    re-measurement. Rhetorical and discourse candidates are never surfaced, and
    a candidate nobody can answer from the evidence is not worth offering.

    ``directed_at_user_only`` is the meeting-style filter: in a meeting the
    only questions worth surfacing are the ones aimed at the person running the
    recording. In a lecture it is off, because a classmate's question is
    usually the one the user wants answered too.
    """
    return candidate is not None and mode_policy_reason(
        candidate, directed_at_user_only) is None


def mode_policy_reason(candidate, directed_at_user_only: bool = False) -> Optional[str]:
    """Why ``passes_mode_policy`` hides ``candidate``, or None if it does not.

    Shown to the user: a detection that is counted but never appears reads as
    a bug unless the reason is visible.
    """
    if candidate.kind not in ("genuine", "request"):
        return f"judged {candidate.kind}"
    if not candidate.answerable:
        return "judged not answerable"
    if directed_at_user_only and not candidate.directed_at_user:
        return "not aimed at you (Only questions aimed at me is on)"
    return None

# Approximate OpenRouter cost of one detection call, per model. Used only for
# the BU115 spend chip: OpenRouter does not return per-call cost on the chat
# endpoint, so this is the measured per-session figure divided by the calls
# that session made. It is an estimate and is labelled as one in the UI.
DETECTOR_COST_PER_CALL = {
    "google/gemini-3.8-flash": 0.0024,
    "qwen/qwen3-14b": 0.0004,
    "google/gemini-2.5-flash": 0.0005,
}
DEFAULT_COST_PER_CALL = 0.001


def build_window_text(records: Iterable) -> str:
    """Format records as detector lines, one ``[HH:MM:SS] Mic: ...`` each."""
    lines = []
    for record in records or ():
        text = (getattr(record, "text", "") or "").strip()
        if not text:
            continue
        lines.append(
            f"[{_time_label(record)}] {_source_label(getattr(record, 'source', ''))}: {text}"
        )
    return "\n".join(lines)


def build_detector_excerpt(earlier: Iterable, new: Iterable) -> str:
    """The detector's user message: already-checked lines, then new ones.

    The split is what lets every chunk be checked exactly once without losing
    a question that straddles two calls (see ``DETECTOR_SYSTEM_PROMPT``).
    """
    new_text = build_window_text(new)
    if not new_text:
        return ""
    earlier_text = build_window_text(earlier)
    if not earlier_text:
        return f"New:\n{new_text}"
    return f"Earlier:\n{earlier_text}\n\nNew:\n{new_text}"


class QuestionDetector:
    """Watches the live transcript and emits de-duplicated question candidates.

    Runs on its own worker thread. ``feed`` is called from whatever thread the
    transcription pipeline happens to be on and only ever appends to a queue,
    so it never blocks the caller and never raises into it. Network failures,
    rate limits and malformed replies are logged and skipped: detection is
    best-effort, and recording outranks it.

    Every chunk is checked exactly once. Chunks that arrive inside the spend
    interval wait in ``_pending`` and go out together with the next call,
    which is made as soon as the interval allows - whether or not another
    chunk arrives, so a question followed by silence is still checked. Each
    call also carries the last ``window_chunks`` already-checked chunks as
    lead-in, so a question that started before the new chunks can be read
    whole.

    The detector model is its own setting and is deliberately *not*
    ``get_selected_model()`` - that one stays the answering model.
    """

    _STOP = object()

    def __init__(self, client, on_candidates: Callable, config=None, clock=time.monotonic):
        """
        Args:
            client: something with ``chat(messages, model=, temperature=)``.
            on_candidates: called with ``list[DetectedQuestion]`` from the
                worker thread. The caller is responsible for hopping to its
                own UI thread.
            config: overrides for the LIVE_QA block, for tests.
            clock: monotonic time source, injectable so the interval cap can
                be tested without sleeping.
        """
        cfg = dict(LIVE_QA)
        cfg.update(config or {})
        self._cfg = cfg
        self._client = client
        self._on_candidates = on_candidates
        self._clock = clock

        self.detector_model = cfg.get("detector_model")
        self.window_chunks = self._clamp_window(cfg.get("window_chunks", 3))
        self.min_detect_interval_seconds = cfg.get("min_detect_interval_seconds", 15)
        self.min_confidence = cfg.get("min_confidence", 0.8)
        self.max_detections_per_session = cfg.get("max_detections_per_session", 40)

        self.call_count = 0
        self.detection_count = 0
        self.estimated_cost = 0.0
        self.cap_reached = False

        self._checked = []  # (index, record) lead-in, newest last
        self._pending = []  # (index, record) not yet sent to the detector
        self._seen = set()
        self._last_call_at = None
        self._queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    # --- configuration that may change mid-session ----------------------

    def _clamp_window(self, value) -> int:
        low, high = self._cfg.get("window_chunks_range", (1, 5))
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return low

    def set_window_chunks(self, value):
        """Widen or narrow the lead-in. Takes effect on the next detection."""
        with self._lock:
            self.window_chunks = self._clamp_window(value)
            del self._checked[:-self.window_chunks]

    def set_detector_model(self, model_id: str):
        """Switch detector model. Takes effect on the next detection."""
        if model_id:
            self.detector_model = model_id

    # --- lifecycle --------------------------------------------------------

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="QuestionDetector", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        """Ask the worker to finish and release its thread."""
        thread = self._thread
        if thread is None:
            return
        self._queue.put(self._STOP)
        thread.join(timeout=timeout)
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- input -------------------------------------------------------------

    def feed(self, record, index=None):
        """Hand the detector one new transcript chunk.

        ``index`` is the chunk's position in the caller's record list; it is
        carried through to the candidate's ``record_indices`` so the UI can
        scroll back to the bubbles a detection came from. Passed explicitly
        rather than read off the record, because the position is the caller's
        fact about its own list, not a property of the chunk.

        Never blocks, never raises: this is called from the transcription path.
        """
        try:
            if record is not None and not self.cap_reached:
                self._queue.put_nowait((index, record))
        except Exception as e:  # noqa: BLE001 - the recording must not care
            logger.debug("Detector feed dropped a record: %s", e)

    # --- worker -----------------------------------------------------------

    def _run(self):
        while True:
            try:
                item = self._queue.get(timeout=self._seconds_until_due())
            except queue.Empty:
                item = None  # the interval ended with chunks still waiting
            if item is self._STOP:
                return
            try:
                if item is not None:
                    self._consume(item)
                else:
                    self._check_pending()
            except Exception as e:  # noqa: BLE001
                logger.error("Question detection failed, skipping: %s", e)

    def _seconds_until_due(self) -> Optional[float]:
        """How long the worker may block: forever with nothing pending."""
        if not self._pending or self.cap_reached:
            return None
        if self._last_call_at is None:
            return 0.0
        remaining = (self._last_call_at + self.min_detect_interval_seconds
                     - self._clock())
        return max(0.0, remaining)

    def _consume(self, entry):
        """Queue one chunk for checking, then check if the interval allows."""
        with self._lock:
            self._pending.append(entry)
        self._check_pending()

    def _check_pending(self):
        if self.cap_reached or not self._pending:
            return
        if (self._last_call_at is not None
                and self._clock() - self._last_call_at < self.min_detect_interval_seconds):
            return  # stays pending; _run wakes up when the interval ends

        with self._lock:
            new, self._pending = self._pending, []
            earlier = list(self._checked)
            self._checked = (self._checked + new)[-self.window_chunks:]
            model = self.detector_model

        new_records = [r for _, r in new]
        # Two free filters before anything is spent. They suppress a call;
        # they never conclude anything, and they do not start the interval.
        # The gate also reads the last checked chunk: a question cut off there
        # ("... What is") is finished by new chunks that carry no question
        # signal of their own ("one of the biggest bands").
        tail = [r for _, r in earlier[-1:]]
        if (not local_gate(build_window_text(tail + new_records))
                or window_is_all_discourse(new_records)):
            return

        self._last_call_at = self._clock()
        excerpt = build_detector_excerpt([r for _, r in earlier], new_records)
        raw = self._call_detector(excerpt, model)
        if raw is None:
            return

        indices = [i for i, _ in earlier + new if i is not None]
        candidates = []
        for candidate in parse_detection(raw):
            if candidate.confidence < self.min_confidence:
                continue
            if candidate.kind == "discourse" or is_discourse(candidate.text):
                continue
            if not dedupe(candidate, self._seen):
                continue
            candidate.record_indices = indices
            candidate.detected_at = datetime.now()
            candidates.append(candidate)

        if not candidates:
            return

        self.detection_count += len(candidates)
        if self.detection_count >= self.max_detections_per_session:
            self.cap_reached = True

        try:
            self._on_candidates(candidates)
        except Exception as e:  # noqa: BLE001
            logger.error("Detector callback raised: %s", e)

    def _call_detector(self, excerpt: str, model: str) -> Optional[str]:
        """One detector call. Returns None on any failure, having logged it."""
        try:
            raw = self._client.chat(
                messages=[
                    {"role": "system", "content": DETECTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": excerpt},
                ],
                model=model,
                temperature=0,
            )
        except Exception as e:  # noqa: BLE001 - network, rate limit, anything
            logger.warning("Detector call failed (%s), skipping window", e)
            return None
        self.call_count += 1
        self.estimated_cost += DETECTOR_COST_PER_CALL.get(model, DEFAULT_COST_PER_CALL)
        return raw

    # --- reporting ---------------------------------------------------------

    def spend_summary(self) -> dict:
        """Counters the BU115 spend chip renders."""
        return {
            "calls": self.call_count,
            "detections": self.detection_count,
            "estimated_cost": self.estimated_cost,
            "cap_reached": self.cap_reached,
        }
