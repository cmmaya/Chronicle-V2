"""BU136: SessionManager.import_transcript_file."""
from unittest.mock import MagicMock, patch

import pytest

from src.app.session_manager import SessionManager
from src.transcription.text_import import TranscriptImportError


@pytest.fixture
def manager(tmp_path):
    mgr = SessionManager(base_path=str(tmp_path / 'sessions'), db_path=str(tmp_path / 'chronicle.db'))
    mgr.transcription_processor_factory = MagicMock()
    yield mgr
    mgr.close()


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / 'Weekly sync.txt'
    path.write_text('Ana: hello\n\nBob: hi\n\nAna: bye\n', encoding='utf-8')
    return path


def _session_count(mgr):
    return mgr.db.connection.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]


def test_import_creates_transcribed_text_session(manager, transcript):
    with patch('src.app.session_manager.index_session_content') as index:
        session = manager.import_transcript_file(str(transcript))

    row = manager.db.get_session(session.id)
    assert row['name'] == 'Weekly sync'
    assert row['origin'] == 'text'
    assert row['status'] == 'stopped'
    assert row['transcription_status'] == 'transcribed'
    assert row['needs_finalize'] == 0
    assert manager.db.is_inserted_transcript(session.id)

    rows = manager.db.get_transcripts(session.id)
    assert [r['text'] for r in rows] == ['Ana: hello', 'Bob: hi', 'Ana: bye']
    assert {r['source'] for r in rows} == {'inserted'}
    index.assert_called_once_with(manager.db, session.id)


def test_parse_error_creates_nothing(manager, tmp_path):
    bad = tmp_path / 'empty.txt'
    bad.write_text('\n\n', encoding='utf-8')
    with pytest.raises(TranscriptImportError):
        manager.import_transcript_file(str(bad))
    assert _session_count(manager) == 0


def test_failure_after_session_row_rolls_back(manager, transcript):
    with patch.object(manager.db, 'add_transcript', side_effect=RuntimeError('boom')):
        with pytest.raises(RuntimeError):
            manager.import_transcript_file(str(transcript))
    assert _session_count(manager) == 0
    assert not any(manager.base_path.iterdir())


def test_index_failure_is_not_raised(manager, transcript):
    with patch('src.app.session_manager.index_session_content', side_effect=RuntimeError('x')):
        session = manager.import_transcript_file(str(transcript))
    assert manager.db.get_session(session.id) is not None


def test_transcribe_session_skips_audio_pipeline(manager, transcript):
    with patch('src.app.session_manager.index_session_content'):
        session = manager.import_transcript_file(str(transcript))
        result = manager.transcribe_session(session.id)
        outcome = manager.finalize_session(session.id)
    manager.transcription_processor_factory.assert_not_called()
    assert result['transcribed'] == 0 and result['has_transcripts']
    assert outcome['error'] is None
    assert manager.db.get_session(session.id)['transcription_status'] == 'transcribed'


def test_resume_inserted_session_raises(manager, transcript):
    with patch('src.app.session_manager.index_session_content'):
        session = manager.import_transcript_file(str(transcript))
    with pytest.raises(ValueError, match="can't be recorded into"):
        manager.resume_stopped_session(session.id)
    with pytest.raises(ValueError, match="can't be recorded into"):
        manager.load_session(session.id, with_capture=True)
    assert manager.db.get_session(session.id)['status'] == 'stopped'
