"""Unit tests for database RAG helper methods."""

import unittest
import os
import tempfile
from datetime import datetime
from src.storage.database import Database


class TestDatabaseRAGMethods(unittest.TestCase):
    def setUp(self):
        """Set up a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db = Database(self.temp_db.name)
        self.db.connect()
        self._create_test_data()

    def tearDown(self):
        """Clean up the temporary database."""
        self.db.disconnect()
        os.unlink(self.temp_db.name)

    def _create_test_data(self):
        """Create test session for RAG testing."""
        self.session_id = self.db.create_session(
            name="Test Meeting",
            start_time=datetime(2024, 1, 15, 9, 0),
            status="completed",
            transcription_status="transcribed",
            summary_status="completed"
        )

    def test_upsert_rag_document_inserts_new(self):
        """Test upsert_rag_document inserts a new document."""
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=100,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Test Transcript",
            content_hash="abc123",
            metadata_json='{"source": "test"}'
        )
        self.assertIsInstance(doc_id, int)
        self.assertGreater(doc_id, 0)

    def test_upsert_rag_document_idempotent_by_source_identity(self):
        """Test upsert is idempotent - inserting same source_type/source_id updates existing."""
        # Insert first time
        doc_id1 = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=200,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Original Title",
            content_hash="hash1",
            metadata_json='{"version": 1}'
        )
        # Insert second time with same source_type/source_id
        doc_id2 = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=200,
            session_id=self.session_id,
            timestamp=1705312900,
            title="Updated Title",
            content_hash="hash2",
            metadata_json='{"version": 2}'
        )
        # Should return the same ID (update, not insert)
        self.assertEqual(doc_id1, doc_id2)

    def test_replace_rag_chunks_inserts_new_chunks(self):
        """Test replace_rag_chunks inserts new chunks for a document."""
        # First create a document
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=300,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Chunk Test",
            content_hash="chunkhash",
            metadata_json='{}'
        )
        # Replace chunks
        chunks = [
            {"content": "First chunk content", "chunk_index": 0, "token_count": 5},
            {"content": "Second chunk content", "chunk_index": 1, "token_count": 7}
        ]
        self.db.replace_rag_chunks(doc_id, chunks)
        # Verify chunks were inserted - fetch via raw query
        cursor = self.db.connection.cursor()
        cursor.execute('SELECT chunk_index, content FROM rag_chunks WHERE document_id = ? ORDER BY chunk_index', (doc_id,))
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(rows[1][0], 1)

    def test_replace_rag_chunks_removes_old_chunks(self):
        """Test replace_rag_chunks removes old chunks before inserting new ones."""
        # Create document with initial chunks
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=400,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Replacement Test",
            content_hash="hash",
            metadata_json='{}'
        )
        initial_chunks = [
            {"content": "Old chunk 1", "chunk_index": 0},
            {"content": "Old chunk 2", "chunk_index": 1}
        ]
        self.db.replace_rag_chunks(doc_id, initial_chunks)
        # Replace with new chunks
        new_chunks = [
            {"content": "New chunk 1", "chunk_index": 0}
        ]
        self.db.replace_rag_chunks(doc_id, new_chunks)
        # Verify only new chunks remain
        cursor = self.db.connection.cursor()
        cursor.execute('SELECT content FROM rag_chunks WHERE document_id = ?', (doc_id,))
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "New chunk 1")

    def test_replace_rag_chunks_fails_on_invalid_document(self):
        """Test replace_rag_chunks raises error for non-existent document."""
        chunks = [{"content": "Test", "chunk_index": 0}]
        with self.assertRaises(Exception):
            self.db.replace_rag_chunks(99999, chunks)

    def test_replace_rag_chunks_empty_list(self):
        """Test replace_rag_chunks with empty list removes all chunks."""
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=500,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Empty Test",
            content_hash="hash",
            metadata_json='{}'
        )
        # Add some chunks first
        self.db.replace_rag_chunks(doc_id, [{"content": "To be removed", "chunk_index": 0}])
        # Replace with empty list
        self.db.replace_rag_chunks(doc_id, [])
        # Verify no chunks remain
        cursor = self.db.connection.cursor()
        cursor.execute('SELECT COUNT(*) FROM rag_chunks WHERE document_id = ?', (doc_id,))
        count = cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_rebuild_rag_fts_indexes_chunks(self):
        """Test rebuild_rag_fts indexes chunks from rag_chunks table."""
        # Create document and chunks
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=600,
            session_id=self.session_id,
            timestamp=1705312800,
            title="FTS Test",
            content_hash="hash",
            metadata_json='{}'
        )
        chunks = [
            {"content": "First chunk content for search", "chunk_index": 0},
            {"content": "Second chunk content for search", "chunk_index": 1}
        ]
        self.db.replace_rag_chunks(doc_id, chunks)

        # Rebuild FTS index
        self.db.rebuild_rag_fts()

        # Verify chunks are indexed in FTS table
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT content, source_type, document_id FROM rag_fts")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 2)
        # Check content was indexed
        contents = [row[0] for row in rows]
        self.assertIn("First chunk content for search", contents)
        self.assertIn("Second chunk content for search", contents)

    def test_rebuild_rag_fts_idempotent(self):
        """Test rebuild_rag_fts can be called multiple times without duplicates."""
        # Create document and chunks
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=700,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Idempotent Test",
            content_hash="hash",
            metadata_json='{}'
        )
        chunks = [{"content": "Chunk content", "chunk_index": 0}]
        self.db.replace_rag_chunks(doc_id, chunks)

        # First rebuild
        self.db.rebuild_rag_fts()

        # Count entries after first rebuild
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM rag_fts")
        count_after_first = cursor.fetchone()[0]

        # Second rebuild
        self.db.rebuild_rag_fts()

        # Count entries after second rebuild
        cursor.execute("SELECT COUNT(*) FROM rag_fts")
        count_after_second = cursor.fetchone()[0]

        # Should be the same - no duplicates
        self.assertEqual(count_after_first, count_after_second)

    def test_rebuild_rag_fts_empty_chunks(self):
        """Test rebuild_rag_fts succeeds when there are no chunks."""
        # Create document with no chunks
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=800,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Empty Chunks Test",
            content_hash="hash",
            metadata_json='{}'
        )
        # Replace with empty chunks
        self.db.replace_rag_chunks(doc_id, [])

        # Rebuild should succeed without error
        self.db.rebuild_rag_fts()

        # Verify FTS is empty
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM rag_fts")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_search_rag_fts_finds_known_chunk(self):
        """Test search_rag_fts finds a known transcript-like chunk."""
        # Create document and chunks with searchable content
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=900,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Search Test",
            content_hash="hash",
            metadata_json='{}'
        )
        chunks = [
            {"content": "The meeting discussed project timeline", "chunk_index": 0},
            {"content": "Budget was approved for Q1", "chunk_index": 1}
        ]
        self.db.replace_rag_chunks(doc_id, chunks)
        self.db.rebuild_rag_fts()

        # Search for known content
        results = self.db.search_rag_fts("timeline")
        self.assertGreater(len(results), 0)
        # Verify result contains expected fields
        result = results[0]
        self.assertIn('chunk_id', result)
        self.assertIn('document_id', result)
        self.assertIn('source_type', result)
        self.assertIn('session_id', result)
        self.assertIn('content', result)
        self.assertIn('rank', result)

    def test_search_rag_fts_session_filter(self):
        """Test session_id filter excludes chunks from other sessions."""
        # Create another session
        session_id2 = self.db.create_session(
            name="Other Meeting",
            start_time=datetime(2024, 1, 16, 9, 0),
            status="completed"
        )

        # Create document in first session
        doc_id1 = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=1000,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Session 1 Doc",
            content_hash="hash1",
            metadata_json='{}'
        )
        self.db.replace_rag_chunks(doc_id1, [{"content": "Unique session one content", "chunk_index": 0}])

        # Create document in second session
        doc_id2 = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=1100,
            session_id=session_id2,
            timestamp=1705312800,
            title="Session 2 Doc",
            content_hash="hash2",
            metadata_json='{}'
        )
        self.db.replace_rag_chunks(doc_id2, [{"content": "Unique session two content", "chunk_index": 0}])

        self.db.rebuild_rag_fts()

        # Search with session filter
        results = self.db.search_rag_fts("session", session_id=self.session_id)
        # Should only find content from first session
        self.assertGreater(len(results), 0)
        for result in results:
            self.assertEqual(result['session_id'], self.session_id)

    def test_search_rag_fts_source_types_filter(self):
        """Test source_types filter excludes non-requested source types."""
        # Create transcript document
        doc_id1 = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=1200,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Transcript Doc",
            content_hash="hash",
            metadata_json='{}'
        )
        # Create summary document
        doc_id2 = self.db.upsert_rag_document(
            source_type="summary",
            source_id=1300,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Summary Doc",
            content_hash="hash",
            metadata_json='{}'
        )

        self.db.replace_rag_chunks(doc_id1, [{"content": "transcript content keyword", "chunk_index": 0}])
        self.db.replace_rag_chunks(doc_id2, [{"content": "summary content keyword", "chunk_index": 0}])

        self.db.rebuild_rag_fts()

        # Search with transcript only filter
        results = self.db.search_rag_fts("keyword", source_types=["transcript"])
        self.assertGreater(len(results), 0)
        for result in results:
            self.assertEqual(result['source_type'], "transcript")

    def test_search_rag_fts_empty_query_raises(self):
        """Test empty query raises or returns controlled error."""
        from src.storage.database import DatabaseError
        # Should raise DatabaseError for empty query
        with self.assertRaises(DatabaseError):
            self.db.search_rag_fts("")

    def test_search_rag_fts_whitespace_query_raises(self):
        """Test whitespace-only query raises or returns controlled error."""
        from src.storage.database import DatabaseError
        # Should raise DatabaseError for whitespace query
        with self.assertRaises(DatabaseError):
            self.db.search_rag_fts("   ")

    def test_search_rag_fts_limit_capped(self):
        """Test limit is capped at 50."""
        # Create document with many chunks
        doc_id = self.db.upsert_rag_document(
            source_type="transcript",
            source_id=1400,
            session_id=self.session_id,
            timestamp=1705312800,
            title="Limit Test",
            content_hash="hash",
            metadata_json='{}'
        )
        chunks = [{"content": f"content {i}", "chunk_index": i} for i in range(100)]
        self.db.replace_rag_chunks(doc_id, chunks)
        self.db.rebuild_rag_fts()

        # Request limit above cap
        results = self.db.search_rag_fts("content", limit=100)
        # Should be capped at 50
        self.assertLessEqual(len(results), 50)


if __name__ == "__main__":
    unittest.main()