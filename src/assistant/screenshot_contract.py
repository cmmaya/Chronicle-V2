"""Screenshot citation contract for Specific Session answers (BU108).

The Specific Session prompt lists the session's screenshots by id. When an
answer is backed by one of them, the model ends it with a fixed pointer line
the UI turns into a "View screenshot" button. The ids it cites are checked
against the ids that were actually in the context.
"""
import re
from typing import Iterable, List, Tuple

MAX_CITED_SCREENSHOTS = 3

CITATION_PREFIX = "The information you asked might be contained in the screenshot:"

SCREENSHOT_CONTRACT = f"""Screenshots:
The context lists this session's screenshots by id (#<id>). Full details (summary, visible text, keywords) are included only for the most relevant ones; the others show a short preview.
- If your answer relies on a listed screenshot, or the information the user asked for is likely shown in one, end your answer with exactly one final line:
{CITATION_PREFIX} #<id>
- Cite at most {MAX_CITED_SCREENSHOTS} ids, comma-separated (for example: #12, #15). Keep that line in English, whatever language the rest of the answer is in.
- Only cite ids that appear in the context. Never invent what a screenshot shows.
- If a screenshot only has a preview and the question needs more detail, say what the preview tells you and still point to it.
- If no screenshot is relevant, do not add the line."""

_CITATION_LINE = re.compile(r'screen\s*shot|captura|pantallazo', re.IGNORECASE)
_ID = re.compile(r'#\s*(\d+)')


def citation_line(ids: Iterable[int]) -> str:
    return f"{CITATION_PREFIX} " + ", ".join(f"#{i}" for i in ids)


def parse_screenshot_refs(answer: str, allowed_ids: Iterable[int]) -> Tuple[str, List[int]]:
    """Split the screenshot pointer line off ``answer``.

    Looks at the last two non-empty lines for one that mentions a screenshot
    and carries ``#<id>`` tokens. Ids not in ``allowed_ids`` are dropped; if
    none survive, the line is removed. If only some survive, the line is
    rewritten with the valid ids. Returns ``(answer, cited_ids)``.
    """
    allowed = set(allowed_ids)
    lines = (answer or "").rstrip().split("\n")
    candidates = [i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()][:2]

    for index in candidates:
        line = lines[index]
        found = [int(m) for m in _ID.findall(line)]
        if not found or not _CITATION_LINE.search(line):
            continue
        cited = []
        for screenshot_id in found:
            if screenshot_id in allowed and screenshot_id not in cited:
                cited.append(screenshot_id)
        cited = cited[:MAX_CITED_SCREENSHOTS]
        if not cited:
            del lines[index]
        elif cited != found:
            lines[index] = citation_line(cited)
        return "\n".join(lines).rstrip(), cited

    return answer, []
