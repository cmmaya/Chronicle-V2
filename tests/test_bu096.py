"""BU096 - Delete past conversation: cascade, idempotency, sibling isolation,
and the answer service's in-memory cleanup hook."""
import os
import tempfile

from src.storage.database import Database
from src.assistant.service import AssistantAnswerService


def _message_count(db, conv_id):
    cursor = db.connection.cursor()
    cursor.execute(
        'SELECT COUNT(*) FROM assistant_messages WHERE conversation_id = ?',
        (conv_id,),
    )
    return cursor.fetchone()[0]


def _conversation_exists(db, conv_id):
    cursor = db.connection.cursor()
    cursor.execute(
        'SELECT COUNT(*) FROM assistant_conversations WHERE id = ?', (conv_id,)
    )
    return cursor.fetchone()[0] == 1


def test_delete_conversation_cascades_to_messages():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(os.path.join(tmpdir, 'test.db'))
        db.connect()

        conv_id = db.create_conversation(title='Doomed')
        db.add_message(conv_id, 'user', 'one')
        db.add_message(conv_id, 'assistant', 'two')
        assert _message_count(db, conv_id) == 2

        db.delete_conversation(conv_id)

        assert not _conversation_exists(db, conv_id)
        assert _message_count(db, conv_id) == 0
        assert all(c['id'] != conv_id for c in db.list_conversations())

        db.disconnect()


def test_delete_conversation_is_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(os.path.join(tmpdir, 'test.db'))
        db.connect()

        conv_id = db.create_conversation(title='Once')
        db.add_message(conv_id, 'user', 'hi')

        db.delete_conversation(conv_id)
        db.delete_conversation(conv_id)  # second call must be a silent no-op
        db.delete_conversation(999999)  # unknown id must not raise

        db.disconnect()


def test_delete_conversation_leaves_siblings_untouched():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(os.path.join(tmpdir, 'test.db'))
        db.connect()

        keep = db.create_conversation(title='Keep')
        db.add_message(keep, 'user', 'keep me')
        db.add_message(keep, 'assistant', 'still here')

        drop = db.create_conversation(title='Drop')
        db.add_message(drop, 'user', 'delete me')

        db.delete_conversation(drop)

        assert _conversation_exists(db, keep)
        assert _message_count(db, keep) == 2
        assert [m['content'] for m in db.get_messages(keep)] == ['keep me', 'still here']

        db.disconnect()


def test_forget_conversation_drops_declined_offers():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(os.path.join(tmpdir, 'test.db'))
        db.connect()

        service = AssistantAnswerService(db=db)
        service.decline_scope_offer(conversation_id=7, session_id=3)
        assert 7 in service._declined_offers

        service.forget_conversation(7)
        assert 7 not in service._declined_offers

        service.forget_conversation(7)  # unknown / already-gone id: no-op
        service.forget_conversation(None)

        db.disconnect()
