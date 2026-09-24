"""Local, offline sentence-embedding service for RAG session routing.

The model is a multilingual ONNX encoder loaded directly with the same
``onnxruntime`` that Parakeet already pins (``onnxruntime==1.20.1``). The only
new dependency is ``tokenizers``. We deliberately do **not** use ``fastembed``:
it declares its own ``onnxruntime`` requirement and pip may promote the pin,
which would break Parakeet transcription.

If onnxruntime / tokenizers are missing, or the model is not installed or cannot
run, :pyattr:`EmbeddingService.is_available` stays ``False`` and callers must
degrade to lexical-only retrieval. Nothing here raises on load failure. The
model is never downloaded here (BU123): it is installed up front by
``src/model_manager.py`` and loaded from its local files.

Meetings are recorded in Spanish, so the model must be multilingual. Preferred:
``intfloat/multilingual-e5-small`` (384-dim, retrieval-tuned). E5 requires input
prefixes -- ``query: `` for questions and ``passage: `` for indexed text. Those
prefixes are applied inside this service so query and passage encoding can never
drift apart at a call site.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Passages per inference call. One call per whole session padded every
# passage to the longest one and grew ONNX Runtime's memory by ~1 GB for a
# long meeting; small batches keep the peak flat and run faster.
EMBED_BATCH_SIZE = 16

# Pinned by repo id + revision so changing either invalidates stored vectors.
EMBED_REPO_ID = "intfloat/multilingual-e5-small"
EMBED_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBED_DIM = 384

_ONNX_MODEL_FILE = "onnx/model.onnx"
_TOKENIZER_FILE = "onnx/tokenizer.json"
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "
_MAX_TOKENS = 512


class EmbeddingService:
    """Lazily-loaded singleton embedding model producing L2-normalised vectors."""

    def __init__(self, repo_id: str = EMBED_REPO_ID, revision: str = EMBED_REVISION):
        self._repo_id = repo_id
        self._revision = revision
        self._lock = threading.RLock()  # guards loading, inference and unload
        self._loaded = False
        self._available = False
        self._session = None
        self._tokenizer = None
        self._input_names: set = set()
        self._last_used = time.monotonic()

    @property
    def model_id(self) -> str:
        """Stable identifier of the form ``"<repo_id>@<revision>"``."""
        return f"{self._repo_id}@{self._revision}"

    @property
    def dim(self) -> int:
        return EMBED_DIM

    @property
    def is_available(self) -> bool:
        self._ensure_loaded()
        return self._available

    @property
    def is_loaded(self) -> bool:
        """True while the model is in memory (never triggers a load)."""
        return self._session is not None

    def idle_seconds(self) -> float:
        """Seconds since the model was last loaded or used."""
        return time.monotonic() - self._last_used

    def unload(self) -> None:
        """Drop the model from memory; the next call loads it again."""
        with self._lock:
            if self._session is None:
                return
            self._session = None
            self._tokenizer = None
            self._loaded = False
            self._available = False
        logger.info("Embedding model unloaded")

    # -- loading ---------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                self._load()
                self._available = True
                self._last_used = time.monotonic()
                logger.info("Embedding model loaded: %s", self.model_id)
            except Exception as exc:  # noqa: BLE001 - degrade, never crash
                self._available = False
                logger.warning(
                    "Embedding model unavailable (%s): %s; "
                    "session routing will be lexical-only",
                    self.model_id,
                    exc,
                )
            finally:
                self._loaded = True

    def _load(self) -> None:
        import onnxruntime as ort  # noqa: WPS433 - lazy on purpose
        from tokenizers import Tokenizer
        from .. import model_manager

        if (self._repo_id, self._revision) != (model_manager.E5_REPO_ID, model_manager.E5_REVISION):
            raise model_manager.ModelNotInstalledError(
                f"{self.model_id} is not the embedding model the model manager installs"
            )
        # Raises ModelNotInstalledError (with the setup hint) if missing.
        model_path = str(model_manager.local_path(model_manager.EMBEDDINGS, _ONNX_MODEL_FILE))
        tokenizer_path = str(model_manager.local_path(model_manager.EMBEDDINGS, _TOKENIZER_FILE))

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)

        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = 2
        session_options.inter_op_num_threads = 1
        # Without the arena, memory used by one batch is returned afterwards
        # instead of being kept at its high-water mark.
        session_options.enable_cpu_mem_arena = False
        self._session = ort.InferenceSession(
            model_path,
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {i.name for i in self._session.get_inputs()}

    # -- encoding ------------------------------------------------------------

    def embed_query(self, text: str) -> Optional[np.ndarray]:
        """Return a unit vector for a search query, or ``None`` if unavailable."""
        vecs = self._embed([_QUERY_PREFIX + (text or "")])
        return None if vecs is None else vecs[0]

    def embed_passages(
        self, texts: List[str], batch_size: int = EMBED_BATCH_SIZE
    ) -> Optional[np.ndarray]:
        """Return an ``(n, dim)`` float32 matrix of unit vectors, or ``None``.

        Passages are embedded in batches of similar length (less padding),
        and the rows come back in the input order.
        """
        prefixed = [_PASSAGE_PREFIX + (t or "") for t in (texts or [])]
        if len(prefixed) <= batch_size:
            return self._embed(prefixed)
        order = sorted(range(len(prefixed)), key=lambda i: len(prefixed[i]))
        result: Optional[np.ndarray] = None
        for start in range(0, len(order), batch_size):
            batch_idx = order[start:start + batch_size]
            vecs = self._embed([prefixed[i] for i in batch_idx])
            if vecs is None:
                return None
            if result is None:
                result = np.zeros((len(prefixed), vecs.shape[1]), dtype=np.float32)
            result[batch_idx] = vecs
        return result

    def _embed(self, prefixed_texts: List[str]) -> Optional[np.ndarray]:
        with self._lock:
            return self._embed_locked(prefixed_texts)

    def _embed_locked(self, prefixed_texts: List[str]) -> Optional[np.ndarray]:
        self._ensure_loaded()
        if not self._available or not prefixed_texts:
            return None
        self._last_used = time.monotonic()
        try:
            encodings = self._tokenizer.encode_batch(prefixed_texts)
            batch = len(encodings)
            max_len = max(len(e.ids) for e in encodings)

            input_ids = np.zeros((batch, max_len), dtype=np.int64)
            attention = np.zeros((batch, max_len), dtype=np.int64)
            for row, enc in enumerate(encodings):
                length = len(enc.ids)
                input_ids[row, :length] = enc.ids
                attention[row, :length] = enc.attention_mask

            feeds = {"input_ids": input_ids, "attention_mask": attention}
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros((batch, max_len), dtype=np.int64)
            feeds = {k: v for k, v in feeds.items() if k in self._input_names}

            last_hidden = self._session.run(None, feeds)[0]
            pooled = self._mean_pool(np.asarray(last_hidden, dtype=np.float32), attention)
            return self._l2_normalise(pooled).astype(np.float32)
        except Exception as exc:  # noqa: BLE001 - degrade, never crash
            logger.warning("Embedding inference failed: %s", exc)
            return None

    @staticmethod
    def _mean_pool(last_hidden: np.ndarray, attention: np.ndarray) -> np.ndarray:
        # Some exports already emit a pooled 2D tensor.
        if last_hidden.ndim == 2:
            return last_hidden
        mask = attention[:, :, None].astype(np.float32)
        summed = (last_hidden * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        return summed / counts

    @staticmethod
    def _l2_normalise(vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.clip(norms, 1e-12, None)


_service: Optional[EmbeddingService] = None
_service_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide embedding service singleton."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = EmbeddingService()
    return _service


def set_embedding_service(service: Optional[EmbeddingService]) -> None:
    """Test hook: inject a fake service, or pass ``None`` to reset the singleton."""
    global _service
    with _service_lock:
        _service = service
