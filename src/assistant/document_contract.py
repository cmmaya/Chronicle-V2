"""Document citation contract for Specific Session answers (BU149).

When the session has documents, the prompt lists them by id (``[D<id>]``) and
tells the model how to weigh them against the transcript. An answer that used
one ends with a fixed line the UI turns into a badge; the ids it cites are
checked against the ids that were actually in the prompt.
"""
import re
from typing import Iterable, List, Optional, Tuple

CITATION_PREFIX = "Documents used:"

DOCUMENT_CONTRACT = f"""Session documents:
The context includes documents the user added to this session, each headed [D<id>] with its file name. The transcript is the primary source.
- Use a document to add information the transcript lacks.
- If a document contradicts the transcript, say so explicitly and name both: what was said in the session and what the document states.
- Never present document content as something said in the session; say that it comes from the document, by name.
- If your answer used any document, end your answer with exactly one final line:
{CITATION_PREFIX} D<id>, D<id>
  Use ids from the headers only, and keep that line in English, whatever language the rest of the answer is in. If the answer also ends with a screenshot line, put the screenshot line before this one so that this one is last.
- If your answer did not use a document, do not add the line."""

# The prefix is matched as a line start, so a sentence that merely mentions
# "documents used" is never taken for the citation.
_CITATION_LINE = re.compile(r'^\s*documents?\s+used\s*:(.*)$', re.IGNORECASE)
_ID = re.compile(r'\bD\s*(\d+)\b', re.IGNORECASE)


def citation_line(ids: Iterable[int]) -> str:
    return f"{CITATION_PREFIX} " + ", ".join(f"D{i}" for i in ids)


def parse_document_refs(answer: str, allowed_ids: Optional[Iterable[int]]) -> Tuple[str, List[int]]:
    """Split the ``Documents used:`` line off ``answer``.

    Looks at the last three non-empty lines for one that starts with the
    prefix and carries ``D<id>`` tokens. Ids not in ``allowed_ids`` are
    dropped (``None`` keeps them all, for a stored conversation whose
    documents may since have been removed); the line is always removed once it parses. A line with no
    ``D<id>`` at all is not a citation: the answer is returned untouched.
    Never raises. Returns ``(answer_without_line, cited_ids)``.
    """
    try:
        allowed = None if allowed_ids is None else set(allowed_ids)
        lines = (answer or "").rstrip().split("\n")
        candidates = [i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()][:3]
        for index in candidates:
            match = _CITATION_LINE.match(lines[index])
            if not match:
                continue
            found = [int(m) for m in _ID.findall(match.group(1))]
            if not found:
                continue
            cited = []
            for document_id in found:
                if (allowed is None or document_id in allowed) and document_id not in cited:
                    cited.append(document_id)
            del lines[index]
            return "\n".join(lines).rstrip(), cited
    except Exception:  # noqa: BLE001 - a parse problem must never lose the answer
        pass
    return answer, []
