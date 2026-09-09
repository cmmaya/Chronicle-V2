"""Unit tests for the local embedding service (BU089)."""

import os
import re
import unittest

import numpy as np

from src.rag.embeddings import (
    EMBED_DIM,
    EmbeddingService,
    get_embedding_service,
    set_embedding_service,
)


REQUIREMENTS = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "requirements.txt"
)


class TestEmbeddingServiceContract(unittest.TestCase):
    def tearDown(self):
        set_embedding_service(None)

    def test_model_id_is_repo_at_revision(self):
        svc = EmbeddingService(repo_id="acme/model", revision="deadbeef")
        self.assertEqual(svc.model_id, "acme/model@deadbeef")

    def test_dim_is_384(self):
        self.assertEqual(EmbeddingService().dim, 384)
        self.assertEqual(EMBED_DIM, 384)

    def test_singleton_and_injection(self):
        first = get_embedding_service()
        self.assertIs(first, get_embedding_service())
        sentinel = object()
        set_embedding_service(sentinel)
        self.assertIs(get_embedding_service(), sentinel)

    def test_unavailable_model_degrades_without_raising(self):
        svc = EmbeddingService(repo_id="does-not/exist", revision="0" * 40)
        # Must not raise on load or on encode.
        self.assertFalse(svc.is_available)
        self.assertIsNone(svc.embed_query("hola mundo"))
        self.assertIsNone(svc.embed_passages(["a", "b"]))

    def test_query_and_passage_prefixes_are_applied_by_the_service(self):
        seen = {}

        class RecordingService(EmbeddingService):
            def _embed(self, prefixed_texts):
                seen.setdefault("calls", []).append(list(prefixed_texts))
                # Return unit vectors so callers get a normal-looking result.
                return np.ones((len(prefixed_texts), EMBED_DIM), dtype=np.float32)

        svc = RecordingService()
        svc.embed_query("¿qué se decidió?")
        svc.embed_passages(["acta de la reunión", "notas"])

        self.assertEqual(seen["calls"][0], ["query: ¿qué se decidió?"])
        self.assertEqual(
            seen["calls"][1],
            ["passage: acta de la reunión", "passage: notas"],
        )


class TestRequirementsGuard(unittest.TestCase):
    """The onnxruntime pin must never be promoted and fastembed must stay out."""

    def _read(self):
        with open(REQUIREMENTS, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_onnxruntime_pin_untouched(self):
        self.assertIn("onnxruntime==1.20.1", self._read())

    def test_fastembed_absent(self):
        text = self._read().lower()
        self.assertNotIn("fastembed", text)

    def test_tokenizers_declared(self):
        self.assertRegex(self._read(), r"(?im)^\s*tokenizers\b")


@unittest.skipUnless(
    EmbeddingService().is_available,
    "real embedding model not available in this environment",
)
class TestRealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = EmbeddingService()

    def test_vectors_are_384d_float32_unit_norm(self):
        vecs = self.svc.embed_passages(["primera acta", "second minutes"])
        self.assertEqual(vecs.shape, (2, 384))
        self.assertEqual(vecs.dtype, np.float32)
        norms = np.linalg.norm(vecs, axis=1)
        self.assertTrue(np.allclose(norms, 1.0, atol=1e-3))

    def test_query_vector_is_unit_norm(self):
        vec = self.svc.embed_query("what did we decide")
        self.assertEqual(vec.shape, (384,))
        self.assertAlmostEqual(float(np.linalg.norm(vec)), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
