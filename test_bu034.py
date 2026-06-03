"""Test assistant conversation storage for BU034."""
import os
import sys
import tempfile

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from storage.database import Database


def test_conversation_with_session_id():
    """Test creating a conversation with session_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create a session first
        from datetime import datetime
        session_id = db.create_session('Test Session', datetime.now(), 'active')
        
        # Create conversation with session_id
        conv_id = db.create_conversation(session_id=session_id, title='Test Conversation')
        
        assert conv_id > 0
        
        # Retrieve and verify
        conv = db.get_conversation(conv_id)
        assert conv['session_id'] == session_id
        assert conv['title'] == 'Test Conversation'
        
        # List conversations for session
        convs = db.list_conversations(session_id=session_id)
        assert len(convs) == 1
        assert convs[0]['id'] == conv_id
        
        db.disconnect()
        print("[PASS] Test conversation with session_id passed")


def test_conversation_without_session_id():
    """Test creating a conversation with null session_id for all-session scope."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation without session_id (all-session scope)
        conv_id = db.create_conversation(title='Global Conversation')
        
        assert conv_id > 0
        
        # Retrieve and verify
        conv = db.get_conversation(conv_id)
        assert conv['session_id'] is None
        assert conv['title'] == 'Global Conversation'
        
        # List all conversations (no filter)
        convs = db.list_conversations()
        assert len(convs) == 1
        
        db.disconnect()
        print("[PASS] Test conversation without session_id passed")


def test_messages_chronological_order():
    """Test appending and listing messages in chronological order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation
        conv_id = db.create_conversation(title='Chat Test')
        
        # Add messages
        msg1_id = db.add_message(conv_id, 'user', 'Hello')
        msg2_id = db.add_message(conv_id, 'assistant', 'Hi there!')
        msg3_id = db.add_message(conv_id, 'user', 'How are you?')
        
        assert msg1_id > 0
        assert msg2_id > 0
        assert msg3_id > 0
        
        # Get messages - should be in chronological order
        messages = db.get_messages(conv_id)
        
        assert len(messages) == 3
        assert messages[0]['content'] == 'Hello'
        assert messages[0]['role'] == 'user'
        assert messages[1]['content'] == 'Hi there!'
        assert messages[1]['role'] == 'assistant'
        assert messages[2]['content'] == 'How are you?'
        assert messages[2]['role'] == 'user'
        
        # Verify timestamps are in order
        assert messages[0]['timestamp'] <= messages[1]['timestamp'] <= messages[2]['timestamp']
        
        db.disconnect()
        print("[PASS] Test messages chronological order passed")


def test_message_roles():
    """Test different message roles."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation
        conv_id = db.create_conversation()
        
        # Add different role messages
        user_msg = db.add_message(conv_id, 'user', 'User message')
        assistant_msg = db.add_message(conv_id, 'assistant', 'Assistant message')
        system_msg = db.add_message(conv_id, 'system', 'System message')
        
        # Verify roles stored correctly
        messages = db.get_messages(conv_id)
        roles = [m['role'] for m in messages]
        
        assert 'user' in roles
        assert 'assistant' in roles
        assert 'system' in roles
        
        db.disconnect()
        print("[PASS] Test message roles passed")


def test_update_conversation():
    """Test updating conversation title."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation
        conv_id = db.create_conversation(title='Original Title')
        
        # Update title
        db.update_conversation(conv_id, title='Updated Title')
        
        # Verify update
        conv = db.get_conversation(conv_id)
        assert conv['title'] == 'Updated Title'
        
        db.disconnect()
        print("[PASS] Test update conversation passed")


def test_delete_conversation():
    """Test deleting conversation and its messages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation with messages
        conv_id = db.create_conversation()
        db.add_message(conv_id, 'user', 'Message 1')
        db.add_message(conv_id, 'assistant', 'Message 2')
        
        # Verify messages exist
        messages = db.get_messages(conv_id)
        assert len(messages) == 2
        
        # Delete conversation
        db.delete_conversation(conv_id)
        
        # Verify conversation deleted
        try:
            db.get_conversation(conv_id)
            assert False, "Should have raised error"
        except Exception:
            pass  # Expected
        
        db.disconnect()
        print("✓ Test delete conversation passed")


def test_delete_message():
    """Test deleting a single message."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation and add messages
        conv_id = db.create_conversation()
        msg1_id = db.add_message(conv_id, 'user', 'Message to delete')
        db.add_message(conv_id, 'assistant', 'Message to keep')
        
        # Delete one message
        db.delete_message(msg1_id)
        
        # Verify only one message remains
        messages = db.get_messages(conv_id)
        assert len(messages) == 1
        assert messages[0]['content'] == 'Message to keep'
        
        db.disconnect()
        print("✓ Test delete message passed")


def test_get_message():
    """Test retrieving a specific message by ID."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test.db')
        db = Database(db_path)
        db.connect()
        
        # Create conversation and add message
        conv_id = db.create_conversation()
        msg_id = db.add_message(conv_id, 'user', 'Specific message')
        
        # Get specific message
        msg = db.get_message(msg_id)
        
        assert msg['id'] == msg_id
        assert msg['content'] == 'Specific message'
        assert msg['role'] == 'user'
        
        db.disconnect()
        print("✓ Test get message passed")


if __name__ == '__main__':
    print("Running BU034 tests...\n")
    
    test_conversation_with_session_id()
    test_conversation_without_session_id()
    test_messages_chronological_order()
    test_message_roles()
    test_update_conversation()
    test_delete_conversation()
    test_delete_message()
    test_get_message()
    
    print("\n✓ All tests passed!")
