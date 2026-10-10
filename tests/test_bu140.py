"""BU140: inserted transcript text is labelled "Text" in assistant context."""
from datetime import datetime
from unittest.mock import MagicMock

from src.assistant.context import AssistantContextRetriever
from src.assistant.context_models import TranscriptExcerpt

TS = int(datetime(2026, 1, 1, 10, 0, 0).timestamp())


def _label(source):
    line = TranscriptExcerpt(1, 's', TS, source, 'hello').to_prompt_text()
    return line.split('(')[1].split(')')[0]


def test_labels_per_source():
    assert _label('inserted') == 'Text'
    assert _label('microphone') == 'Mic'
    assert _label('system') == 'Sys'


def test_context_keeps_inserted_source():
    db = MagicMock()
    db.get_transcripts.return_value = [
        {'text': 'budget review', 'timestamp': TS, 'source': 'inserted'}]
    excerpts = AssistantContextRetriever(db)._get_transcripts(1, 's', 'budget')

    assert [e.source for e in excerpts] == ['inserted']
    assert excerpts[0].to_prompt_text().endswith('(Text): budget review')
