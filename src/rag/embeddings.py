"""Local, offline sentence-embedding service for RAG session routing.

The model is a multilingual ONNX encoder loaded directly with the same
``onnxruntime`` that Parakeet already pins (``onnxruntime==1.20.1``). The only
new dependency is ``tokenizers``. We deliberately do **not** use ``fastembed``:
it declares its own ``onnxruntime`` requirement and pip may promote the pin,
which would break Parakeet transcription.

If onnxruntime / tokenizers / huggingface-hub are missing, or the model cannot
be downloaded or run, :pyattr:`EmbeddingService.is_available` stays ``False`` and
callers must degrade to lexical-only retrieval. Nothing here raises on load
failure.

Meetings are recorded in Spanish, so the model must be multilingual. Preferred:
``intfloat/multilingual-e5-small`` (384-dim, retrieval-tuned). E5 requires input
prefixes -- ``query: `` for questions and ``passage: `` for indexed text. Those
prefixes are applied inside this service so query and passage encoding can never
drift apart at a call site.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

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
        self._lock = threading.Lock()
        self._loaded = False
        self._available = False
        self._session = None
        self._tokenizer = None
        self._input_names: set = set()

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
        from huggingface_hub import hf_hub_download

        model_path = hf_hub_download(
            self._repo_id, _ONNX_MODEL_FILE, revision=self._revision
        )
        tokenizer_path = hf_hub_download(
            self._repo_id, _TOKENIZER_FILE, revision=self._revision
        )

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)

        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = 2
        session_options.inter_op_num_threads = 1
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

    def embed_passages(self, texts: List[str]) -> Optional[np.ndarray]:
        """Return an ``(n, dim)`` float32 matrix of unit vectors, or ``None``."""
        return self._embed([_PASSAGE_PREFIX + (t or "") for t in (texts or [])])

    def _embed(self, prefixed_texts: List[str]) -> Optional[np.ndarray]:
        self._ensure_loaded()
        if not self._available or not prefixed_texts:
            return None
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
