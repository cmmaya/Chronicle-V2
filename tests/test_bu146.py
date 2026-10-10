"""BU146: documents stored per session as extracted text."""
import sqlite3
from datetime import datetime

import pytest

from src.config import SESSION_DOCUMENTS
from src.storage.database import SCHEMA_VERSION, Database, DatabaseError


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / 'chronicle.db'))
    database.connect()
    yield database
    database.disconnect()


@pytest.fixture
def session_id(db):
    return db.create_session('Class', datetime(2026, 10, 5, 21, 0))


def _add(db, session_id, name='syllabus.pdf', text='Week 1: sampling.'):
    return db.add_session_document(session_id, name, 'pdf', 1234, text)


def test_add_and_read_back(db, session_id):
    doc_id = _add(db, session_id)
    [doc] = db.get_session_documents(session_id)
    assert doc['id'] == doc_id
    assert (doc['name'], doc['file_type'], doc['file_bytes']) == ('syllabus.pdf', 'pdf', 1234)
    assert doc['text'] == 'Week 1: sampling.'
    assert doc['char_count'] == len('Week 1: sampling.')
    assert doc['added_at'] > 0


def test_listing_omits_text_and_is_oldest_first(db, session_id):
    first, second = _add(db, session_id, 'a.pdf'), _add(db, session_id, 'b.pdf')
    listed = db.list_session_documents(session_id)
    assert [d['id'] for d in listed] == [first, second]
    assert all('text' not in d for d in listed)
    assert [d['id'] for d in db.get_session_documents(session_id)] == [first, second]
    assert db.count_session_documents(session_id) == 2


def test_documents_are_per_session(db, session_id):
    other = db.create_session('Other', datetime(2026, 10, 6, 21, 0))
    _add(db, session_id)
    assert db.get_session_documents(other) == []
    assert db.count_session_documents(other) == 0


def test_limit_refused_without_inserting(db, session_id):
    for i in range(SESSION_DOCUMENTS['max_per_session']):
        _add(db, session_id, f'doc{i}.txt')
    with pytest.raises(DatabaseError, match='already has'):
        _add(db, session_id, 'one-too-many.txt')
    assert db.count_session_documents(session_id) == SESSION_DOCUMENTS['max_per_session']


def test_duplicate_name_refused_case_insensitively(db, session_id):
    _add(db, session_id, 'Syllabus.PDF')
    with pytest.raises(DatabaseError, match='named syllabus.pdf'):
        _add(db, session_id, 'syllabus.pdf')
    other = db.create_session('Other', datetime(2026, 10, 6, 21, 0))
    _add(db, other, 'syllabus.pdf')  # another session may reuse the name


def test_refusal_leaves_the_connection_usable(db, session_id):
    _add(db, session_id)
    with pytest.raises(DatabaseError):
        _add(db, session_id)
    _add(db, session_id, 'second.pdf')
    assert db.count_session_documents(session_id) == 2


def test_delete_document(db, session_id):
    keep, drop = _add(db, session_id, 'keep.pdf'), _add(db, session_id, 'drop.pdf')
    db.delete_session_document(drop)
    assert [d['id'] for d in db.list_session_documents(session_id)] == [keep]


def test_purge_session_removes_its_documents(db, session_id):
    other = db.create_session('Other', datetime(2026, 10, 6, 21, 0))
    _add(db, session_id)
    _add(db, other)
    db.purge_session(session_id)
    assert db.count_session_documents(session_id) == 0
    assert db.count_session_documents(other) == 1


def test_purge_orphans_reports_documents_of_deleted_sessions(db, session_id):
    _add(db, session_id)
    db.connection.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
    db.connection.commit()
    assert db.purge_orphans()['session_documents'] == 1
    assert db.count_session_documents(session_id) == 0


def test_migration_adds_the_table_to_a_v4_database(tmp_path):
    path = str(tmp_path / 'old.db')
    database = Database(path)
    database.connect()
    session = database.create_session('Class', datetime(2026, 10, 5, 21, 0))
    database.disconnect()

    raw = sqlite3.connect(path)
    raw.execute('DROP TABLE session_documents')
    raw.execute('PRAGMA user_version = 4')
    raw.commit()
    raw.close()

    database = Database(path)
    database.connect()
    try:
        assert database.connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION == 5
        _add(database, session)
        assert database.count_session_documents(session) == 1
    finally:
        database.disconnect()
