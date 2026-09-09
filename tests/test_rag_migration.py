"""Tests for the RAG corpus backfill / reindex (BU091)."""

import os
import re
import tempfile
import unittest
from datetime import datetime

import numpy as np

from src.storage.database import Database
from src.rag.embeddings import set_embedding_service
from src.rag.indexer import index_session_content
from src.rag.migration import backfill_corpus
from src.rag.router import invalidate_profile_cache

_TOKEN_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


class FakeEmbeddingService:
    dim = 384
    is_available = True

    def __init__(self, model_id="fake/e5@v1"):
        self.model_id = model_id

    def _vec(self, text):
        v = np.zeros(self.dim, dtype=np.float32)
        v[0] = 6.0
        for tok in _TOKEN_RE.findall((text or "").lower()):
            v[(hash(tok) % (self.dim - 1)) + 1] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_query(self, text):
        return self._vec(text)

    def embed_passages(self, texts):
        return np.vstack([self._vec(t) for t in texts]).astype(np.float32)


class UnavailableEmbeddingService:
    model_id = "fake/none@v1"
    dim = 384
    is_available = False

    def embed_query(self, text):
        return None

    def embed_passages(self, texts):
        return None


class MigrationTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.db.connect()
        invalidate_profile_cache()
        set_embedding_service(FakeEmbeddingService())
        self._seed()

    def tearDown(self):
        set_embedding_service(None)
        self.db.disconnect()
        os.unlink(self.tmp.name)

    def _seed(self):
        self.session_ids = []
        for i in range(3):
            sid = self.db.create_session(
                name=f"Meeting {i}",
                start_time=datetime(2024, 1, 10 + i, 9, 0),
                status="completed",
            )
            self.db.add_transcript(
                session_id=sid,
                timestamp=datetime(2024, 1, 10 + i, 9, 0),
                text=f"We discussed the budget and the roadmap for quarter {i}.",
                source="microphone",
            )
            self.db.add_summary(
                session_id=sid,
                summary_type="full",
                content=f"Summary {i}: budget approved, roadmap set.",
                model_used="test",
            )
            self.session_ids.append(sid)

    def _count(self, sql, *params):
        cur = self.db.connection.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]


class TestBackfill(MigrationTestBase):
    def test_backfill_produces_chunks_embeddings_and_profiles(self):
        summary = backfill_corpus(self.db)

        self.assertEqual(summary["sessions"], 3)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(summary["embeddings"])

        self.assertGreater(self._count("SELECT COUNT(*) FROM rag_chunks"), 0)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NULL"), 0
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM session_profiles"), 3
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM session_profiles WHERE embedding IS NULL"),
            0,
        )

    def test_rerun_on_unchanged_data_writes_nothing(self):
        backfill_corpus(self.db)

        cur = self.db.connection.cursor()
        cur.execute("SELECT id, created_at FROM rag_chunks ORDER BY id")
        before = cur.fetchall()

        backfill_corpus(self.db)

        cur.execute("SELECT id, created_at FROM rag_chunks ORDER BY id")
        after = cur.fetchall()
        self.assertEqual(before, after)  # same rows, same created_at -> no rewrite

    def test_interrupted_backfill_resumes_without_duplicating_chunks(self):
        # Simulate a partial run: only the first session got indexed.
        index_session_content(self.db, self.session_ids[0])
        first_total = self._count("SELECT COUNT(*) FROM rag_chunks")

        backfill_corpus(self.db)
        full_total = self._count("SELECT COUNT(*) FROM rag_chunks")
        self.assertGreater(full_total, first_total)

        # Re-entering again must not duplicate.
        backfill_corpus(self.db)
        self.assertEqual(self._count("SELECT COUNT(*) FROM rag_chunks"), full_total)
        # one transcript doc + one summary doc per session
        self.assertEqual(self._count("SELECT COUNT(*) FROM rag_documents"), 6)

    def test_changing_embedding_model_recomputes_vectors(self):
        backfill_corpus(self.db)
        cur = self.db.connection.cursor()
        cur.execute("SELECT DISTINCT embedding_model FROM rag_chunks")
        self.assertEqual([r[0] for r in cur.fetchall()], ["fake/e5@v1"])

        set_embedding_service(FakeEmbeddingService(model_id="fake/e5@v2"))
        summary = backfill_corpus(self.db)
        self.assertTrue(summary["embeddings"])

        cur.execute("SELECT DISTINCT embedding_model FROM rag_chunks")
        self.assertEqual([r[0] for r in cur.fetchall()], ["fake/e5@v2"])

    def test_unavailable_embedding_service_still_builds_lexical_index(self):
        set_embedding_service(UnavailableEmbeddingService())

        summary = backfill_corpus(self.db)
        self.assertFalse(summary["embeddings"])
        self.assertEqual(summary["failed"], 0)

        # Chunks + FTS exist; vectors are simply NULL.
        self.assertGreater(self._count("SELECT COUNT(*) FROM rag_chunks"), 0)
        self.assertGreater(self._count("SELECT COUNT(*) FROM rag_fts"), 0)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NOT NULL"), 0
        )

        # A later run with a working embedder repairs the vectors.
        set_embedding_service(FakeEmbeddingService())
        backfill_corpus(self.db)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NULL"), 0
        )


class TestSessionEventReindex(MigrationTestBase):
    def test_stopping_a_session_refreshes_chunks_embeddings_profile_and_fts(self):
        sid = self.session_ids[0]

        # Nothing indexed yet for this session.
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM rag_chunks WHERE session_id = ?", sid), 0
        )

        # This is exactly what session_manager calls when a session stops.
        self.assertTrue(index_session_content(self.db, sid))

        self.assertGreater(
            self._count("SELECT COUNT(*) FROM rag_chunks WHERE session_id = ?", sid), 0
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) FROM rag_chunks WHERE session_id = ? AND embedding IS NULL",
                sid,
            ),
            0,
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM session_profiles WHERE session_id = ?", sid),
            1,
        )
        self.assertGreater(self._count("SELECT COUNT(*) FROM rag_fts"), 0)


if __name__ == "__main__":
    unittest.main()
