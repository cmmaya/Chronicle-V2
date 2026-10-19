"""BU143: stop words dropped, inflections matched by prefix, follow-ups
searched with the previous question."""
from unittest.mock import MagicMock

from src.assistant.context import AssistantContextRetriever
from src.assistant.context_models import ConversationTurn
from src.storage.database import Database, fts_terms, sanitize_fts_query


def test_question_words_are_dropped():
    terms = fts_terms('what did she mention about the assigment at the end '
                      'of the class? She gave some advices')
    assert terms == ['assigment', 'end', 'class', 'advic']


def test_inflections_are_stemmed():
    assert fts_terms('submitted reports talking classes') == [
        'submitt', 'report', 'talk', 'class']


def test_only_stop_words_still_searches():
    assert fts_terms('what is it') == ['what', 'is', 'it']


def test_query_is_prefix_or():
    assert sanitize_fts_query('advices report') == '"advic"* OR "report"*'


def test_prefix_query_matches_inflected_text(tmp_path):
    db = Database(str(tmp_path / 'c.db'))
    db.connect()
    db.connection.execute(
        "INSERT INTO rag_fts (content, source_type, session_id, document_id, chunk_id) "
        "VALUES ('she gave advice on the report', 'transcript', 1, 1, 1)")
    assert db.connection.execute(
        'SELECT count(*) FROM rag_fts WHERE rag_fts MATCH ?',
        (sanitize_fts_query('any advices?'),)).fetchone()[0] == 1


def test_short_follow_up_adds_previous_question():
    history = [ConversationTurn('user', 'what was the final critique deadline'),
               ConversationTurn('assistant', 'Not found.')]
    q = AssistantContextRetriever._search_query('is it a report?', history)
    assert 'report' in q and 'deadline' in q


def test_full_question_is_searched_alone():
    history = [ConversationTurn('user', 'earlier topic entirely')]
    q = AssistantContextRetriever._search_query(
        'what did the professor say about grading rubric deadlines', history)
    assert 'earlier' not in q


def test_in_memory_search_matches_inflections():
    db = MagicMock()
    db.get_session.return_value = {'name': 'S', 'status': 'active'}
    db.get_summaries.return_value = []
    db.get_screenshots.return_value = []
    db.get_transcripts.return_value = (
        [{'text': 'one piece of advice for the critique', 'timestamp': 0, 'source': 'system'}]
        + [{'text': 'filler %d' % i + ' words' * 120, 'timestamp': 100 * (i + 1),
            'source': 'system'} for i in range(10)])
    ctx = AssistantContextRetriever(db).build_session_context(1, 'any advices?')
    assert ctx.transcripts[0].text.startswith('one piece of advice')
