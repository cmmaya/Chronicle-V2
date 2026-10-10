"""Read a transcript the user picked from disk into paragraphs (BU135).

The text counterpart of ``src.audio.importer``: exports from Teams, Zoom,
Otter or Chronicle's own download are usually ``.txt``, UTF-8 (often with a
BOM) or cp1252. Paragraphs are blocks separated by blank lines; single
newlines inside a block are kept so speaker lines stay on their own line.
The text is stored as-is - no speaker or timestamp parsing.
"""
import re
from pathlib import Path
from typing import List

SUPPORTED_EXTENSIONS = ('.txt', '.md')
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024  # a ~5 h meeting is well under this

_ENCODINGS = ('utf-8-sig', 'cp1252')
_BINARY_SNIFF_BYTES = 4096
_BLANK_LINES = re.compile(r'\n[ \t]*\n')


class TranscriptImportError(ValueError):
    """The file can't be imported; the message is shown to the user."""


def read_transcript_paragraphs(path) -> List[str]:
    """Return the non-empty paragraphs of the transcript at ``path``, in order."""
    path = Path(path)
    name = path.name
    if not path.is_file():
        raise TranscriptImportError(f"{name} doesn't exist")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise TranscriptImportError(
            f"{name} isn't a supported transcript (use {', '.join(SUPPORTED_EXTENSIONS)})")
    if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise TranscriptImportError(
            f'{name} is too large (the limit is {MAX_TRANSCRIPT_BYTES // (1024 * 1024)} MB)')

    data = path.read_bytes()
    if b'\x00' in data[:_BINARY_SNIFF_BYTES]:
        raise TranscriptImportError(f"{name} is not a text file")
    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise TranscriptImportError(f"{name} uses a text encoding Chronicle can't read")

    text = text.replace('\r\n', '\n').replace('\r', '\n')
    paragraphs = []
    for block in _BLANK_LINES.split(text):
        lines = [line.rstrip() for line in block.split('\n')]
        paragraph = '\n'.join(lines).strip('\n')
        if paragraph.strip():
            paragraphs.append(paragraph)
    if not paragraphs:
        raise TranscriptImportError(f'{name} is empty')
    return paragraphs
