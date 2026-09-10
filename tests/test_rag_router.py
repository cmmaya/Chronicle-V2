"""Unit tests for the Tier-1 session router (BU089)."""

import os
import re
import tempfile
import time
import unittest
from datetime import datetime

import numpy as np

from src.storage.database import Database
from src.rag.indexer import index_session_profile, extract_keywords
from src.rag.router import route_sessions, invalidate_profile_cache
from src.rag.embeddings import set_embedding_service
from src.assistant.rag_context_builder import build_routed_session_context


_TOKEN_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


class FakeEmbeddingService:
    """Deterministic bag-of-words embedder in the E5 cosine band.

    A large shared baseline component pushes every pair's cosine into ~[0.7, 1.0]
    (like a retrieval-tuned encoder), while per-word buckets separate topics.
    """

    model_id = "fake/multilingual@v1"
    dim = 384
    is_available = True

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        v[0] = 6.0  # shared baseline -> high floor cosine
        for tok in _TOKEN_RE.findall((text or "").lower()):
            v[(hash(tok) % (self.dim - 1)) + 1] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def embed_query(self, text):
        return self._vec(text)

    def embed_passages(self, texts):
        return np.vstack([self._vec(t) for t in texts]).astype(np.float32)


class UnavailableEmbeddingService:
    model_id = "fake/unavailable@v1"
    dim = 384
    is_available = False

    def embed_query(self, text):
        return None

    def embed_passages(self, texts):
        return None


class RouterTestBase(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = Database(self.temp_db.name)
        self.db.connect()
        invalidate_profile_cache()
        set_embedding_service(FakeEmbeddingService())

    def tearDown(self):
        set_embedding_service(None)
        invalidate_profile_cache()
        self.db.disconnect()
        os.unlink(self.temp_db.name)

    def _add_session(self, name, summary, when=datetime(2024, 3, 1, 10, 0)):
        session_id = self.db.create_session(
            name=name, start_time=when, status="completed",
            transcription_status="transcribed", summary_status="completed",
        )
        if summary:
            self.db.add_summary(session_id, "full", summary, "test-model")
        index_session_profile(self.db, session_id, force=True)
        return session_id


class TestRouting(RouterTestBase):
    def setUp(self):
        super().setUp()
        self.billing = self._add_session(
            "Billing platform sync",
            "The team reviewed the invoicing microservice, discussed Stripe "
            "webhook retries and the dunning email schedule for overdue accounts.",
        )
        self.hiring = self._add_session(
            "Hiring committee",
            "We debated candidate scorecards, the take-home exercise rubric and "
            "the onsite interview loop for the backend engineer opening.",
        )
        self.roadmap = self._add_session(
            "Q3 roadmap planning",
            "Prioritised the mobile offline mode, the search revamp and the "
            "analytics dashboard for the third quarter roadmap.",
        )
        self.retro_es = self._add_session(
            "Retrospectiva del sprint",
            "El equipo habló sobre los bloqueos del sprint, la deuda técnica "
            "acumulada y cómo mejorar las estimaciones de las tareas.",
        )

    def test_paraphrased_question_routes_matching_session_to_top(self):
        results = route_sessions(
            self.db, "how are we handling failed payment retries and overdue billing?"
        )
        self.assertTrue(results)
        self.assertEqual(results[0].session_id, self.billing)

    def test_spanish_question_routes_spanish_session_to_top(self):
        results = route_sessions(
            self.db, "¿qué se dijo sobre la deuda técnica y los bloqueos del sprint?"
        )
        self.assertTrue(results)
        self.assertEqual(results[0].session_id, self.retro_es)

    def test_unrelated_question_yields_no_candidates(self):
        results = route_sessions(
            self.db, "recomiéndame una receta vegetariana para la cena"
        )
        self.assertEqual(results, [])

    def test_results_carry_score_and_reason(self):
        results = route_sessions(self.db, "interview loop and candidate scorecards")
        self.assertTrue(results)
        top = results[0]
        self.assertEqual(top.session_id, self.hiring)
        self.assertIsInstance(top.score, float)
        self.assertTrue(top.reason)
        self.assertEqual(top.name, "Hiring committee")

    def test_degrades_to_lexical_only_when_embedder_unavailable(self):
        set_embedding_service(UnavailableEmbeddingService())
        invalidate_profile_cache()
        # Re-index so profiles have no usable vector.
        for sid in (self.billing, self.hiring, self.roadmap, self.retro_es):
            index_session_profile(self.db, sid, force=True)

        results = route_sessions(self.db, "roadmap planning mobile offline search")
        self.assertTrue(results)
        self.assertEqual(results[0].session_id, self.roadmap)

    def test_cache_invalidated_after_profile_write(self):
        # Nothing about kubernetes yet.
        self.assertEqual(
            route_sessions(self.db, "kubernetes cluster autoscaling incident"), []
        )
        infra = self._add_session(
            "Infra incident review",
            "Postmortem of the kubernetes cluster outage: autoscaling failed, "
            "the node pool exhausted memory and pods were evicted.",
        )
        results = route_sessions(self.db, "kubernetes cluster autoscaling incident")
        self.assertTrue(results)
        self.assertEqual(results[0].session_id, infra)

    def test_context_stays_within_budget(self):
        routed = route_sessions(self.db, "billing retries and roadmap and hiring loop")
        context = build_routed_session_context(self.db, "billing", routed, max_chars=1200)
        self.assertLessEqual(len(context), 1200)

    def test_context_header_carries_session_id_and_date(self):
        routed = route_sessions(self.db, "billing retries and dunning")
        self.assertTrue(routed)
        context = build_routed_session_context(self.db, "billing", routed)
        self.assertIn(f"[S{routed[0].session_id}]", context)
        self.assertIn("2024-03-01", context)  # _add_session default start_time
        # The router's internal match reason must not leak into the model context.
        if routed[0].reason:
            self.assertNotIn(routed[0].reason, context)


class TestRoutingScale(RouterTestBase):
    def test_thousand_profiles_route_fast_after_warmup(self):
        model_id = FakeEmbeddingService.model_id
        rng = np.random.default_rng(7)
        base = datetime(2023, 1, 1, 9, 0)
        for i in range(1000):
            sid = self.db.create_session(
                name=f"Session {i}", start_time=base, status="completed",
            )
            vec = rng.standard_normal(384).astype(np.float32)
            vec /= np.linalg.norm(vec)
            self.db.upsert_session_profile(
                session_id=sid,
                profile_text=f"session {i} notes about topic number {i}",
                keywords=f"topic{i}",
                embedding=vec.tobytes(),
                embedding_model=model_id,
                content_hash=f"h{i}",
            )

        route_sessions(self.db, "topic number 42")  # warm the cache

        start = time.perf_counter()
        for _ in range(5):
            route_sessions(self.db, "topic number 42")
        avg = (time.perf_counter() - start) / 5
        self.assertLess(avg, 0.15, f"routing too slow: {avg * 1000:.1f} ms")


class TestKeywordExtraction(unittest.TestCase):
    def test_filters_stopwords_both_languages(self):
        kws = extract_keywords(
            "The team discussed the roadmap and el equipo habló del roadmap"
        )
        self.assertIn("roadmap", kws)
        self.assertNotIn("the", kws)
        self.assertNotIn("del", kws)


if __name__ == "__main__":
    unittest.main()
