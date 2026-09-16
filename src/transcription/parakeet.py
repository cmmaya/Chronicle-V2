"""Parakeet ASR engine integration using ONNX Runtime via onnx-asr.

This module intentionally avoids importing torch, openai-whisper, faster-whisper,
or ROCm/CUDA libraries. It is designed for CPU-first transcription on Windows
machines where PyTorch GPU stacks may be unavailable or unstable.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import numpy as np
import soundfile as sf

try:
    from ..config import TRANSCRIPTION as _TRANSCRIPTION
except Exception:  # pragma: no cover - config should always import
    _TRANSCRIPTION = {}

logger = logging.getLogger(__name__)

# onnx-asr accepts these input rates directly (it resamples internally).
_SUPPORTED_SAMPLE_RATES = (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000)


class ParakeetError(Exception):
    """Base exception for Parakeet transcription errors."""


class ModelLoadError(ParakeetError):
    """Failed to load Parakeet model."""


class TranscriptionError(ParakeetError):
    """Failed to transcribe audio."""


class ParakeetEngine:
    """Wrapper for NVIDIA Parakeet TDT v3 through the onnx-asr package.

    The default model name follows onnx-asr's public quickstart:
    ``nemo-parakeet-tdt-0.6b-v3``. It loads from Hugging Face on first use and
    then uses the local cache.

    Parakeet does not support Whisper-style ``initial_prompt``. The method
    accepts that parameter only to remain compatible with the existing
    TranscriptionProcessor interface; deduplication/context remains handled by
    the processor.

    ``load()`` and inference are thread-safe: concurrent first calls load the
    model once, and inferences run one at a time (in parallel they would only
    fight over the same CPU cores). Use :func:`get_shared_engine` so the whole
    app holds a single copy of the model.
    """

    DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v3"
    DEFAULT_LANGUAGE = "auto"
    _UNSET = object()

    def __init__(
        self,
        model_path: Optional[str] = None,
        scorer_path: Optional[str] = None,
        language: str = DEFAULT_LANGUAGE,
        intra_threads: Optional[int] = None,
        inter_threads: int = 1,
        quantization: Any = _UNSET,
    ):
        """Initialize Parakeet engine.

        Args:
            model_path: onnx-asr model name or local model directory. Defaults
                to ``config.TRANSCRIPTION['model']``.
            scorer_path: Kept for API compatibility; unused.
            language: Kept for API compatibility. Parakeet v3 can auto-detect
                supported languages.
            intra_threads: ONNX Runtime intra-op thread count. Defaults to
                ``config.TRANSCRIPTION['intra_threads']``, else up to 4.
            inter_threads: ONNX Runtime inter-op thread count.
            quantization: onnx-asr weight quantization (``"int8"``) or None for
                full precision. Defaults to ``config.TRANSCRIPTION['quantization']``.
        """
        self.model_path = model_path or _TRANSCRIPTION.get("model") or self.DEFAULT_MODEL
        self.scorer_path = scorer_path
        self.language = language or self.DEFAULT_LANGUAGE
        self.intra_threads = (
            intra_threads
            or _TRANSCRIPTION.get("intra_threads")
            or max(1, min(os.cpu_count() or 1, 4))
        )
        self.inter_threads = max(1, inter_threads)
        self.quantization = (
            _TRANSCRIPTION.get("quantization") if quantization is self._UNSET else quantization
        )

        self._model: Any = None
        self._loaded = False
        self._model_type = "parakeet-onnx"
        self._model_name: Optional[str] = None
        self._loaded_quantization: Optional[str] = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._last_used = time.monotonic()

    def idle_seconds(self) -> float:
        """Seconds since the model was last loaded or used."""
        return time.monotonic() - self._last_used

    def load(self) -> None:
        """Load Parakeet model through onnx-asr.

        Raises:
            ModelLoadError: If onnx-asr is missing or the model cannot be loaded.
        """
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            self._load()

    def _load(self) -> None:
        try:
            # Import lazily so the app can start even if the dependency is missing.
            import onnx_asr
        except ImportError as exc:
            raise ModelLoadError(
                "Failed to import onnx-asr. Install it with: "
                "pip install \"onnx-asr[cpu,hub]\""
            ) from exc

        try:
            start = time.perf_counter()
            self._model = self._load_onnx_asr_model(onnx_asr)
            self._model_name = self.model_path
            self._loaded = True
            self._last_used = time.monotonic()
            logger.info(
                "Loaded Parakeet model '%s' (%s) using onnx-asr in %.2fs "
                "(intra_threads=%s, inter_threads=%s)",
                self.model_path,
                self._loaded_quantization or "full precision",
                time.perf_counter() - start,
                self.intra_threads,
                self.inter_threads,
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load Parakeet model '{self.model_path}': {exc}"
            ) from exc

    def _session_options(self) -> Any:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.intra_threads
        options.inter_op_num_threads = self.inter_threads
        # The arena keeps every allocation's high-water mark for the life of
        # the model (and over-allocates in powers of two); without it, memory
        # between inferences goes back to the OS.
        options.enable_cpu_mem_arena = False
        return options

    def _load_onnx_asr_model(self, onnx_asr: Any) -> Any:
        """Load the model with the configured quantization and thread counts.

        Falls back to full precision if the quantized weights can't be loaded
        (e.g. offline before their first download), and to the bare
        ``load_model(name)`` call on onnx-asr versions without these options.
        """
        try:
            options = self._session_options()
        except Exception as exc:  # noqa: BLE001 - thread tuning is optional
            logger.warning("Could not build ONNX Runtime session options: %s", exc)
            options = None

        variants = []
        if self.quantization:
            variants.append({"quantization": self.quantization})
        variants.append({})

        last_exc: Optional[Exception] = None
        for extra in variants:
            kwargs = dict(extra)
            if options is not None:
                kwargs["sess_options"] = options
            try:
                model = onnx_asr.load_model(self.model_path, **kwargs)
            except TypeError as exc:
                # onnx-asr predating quantization / sess_options.
                logger.warning("onnx-asr rejected %s (%s); loading with defaults", list(kwargs), exc)
                self._loaded_quantization = None
                return onnx_asr.load_model(self.model_path)
            except Exception as exc:  # noqa: BLE001 - try the next variant
                last_exc = exc
                if extra:
                    logger.warning(
                        "Could not load %s weights for '%s' (%s); falling back to full precision",
                        self.quantization, self.model_path, exc,
                    )
                continue
            self._loaded_quantization = extra.get("quantization")
            return model

        raise last_exc if last_exc else ModelLoadError("No model variant could be loaded")

    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded

    def transcribe(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe an audio file to text.

        Args:
            audio_path: Path to a WAV (or other soundfile-readable) file.
            initial_prompt: Ignored. Kept for compatibility with previous
                Whisper-based processor calls.

        Returns:
            Transcribed text string.

        Raises:
            TranscriptionError: If transcription fails.
            ModelLoadError: If model cannot be loaded.
        """
        audio_path = str(audio_path)
        try:
            samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
        except Exception as exc:
            raise TranscriptionError(f"Could not read audio file {audio_path}: {exc}") from exc
        return self.transcribe_array(samples, sample_rate, label=audio_path)

    def transcribe_array(
        self, samples: np.ndarray, sample_rate: int, label: str = "<array>"
    ) -> str:
        """Transcribe in-memory audio (mono or multichannel, any supported rate)."""
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        if sample_rate not in _SUPPORTED_SAMPLE_RATES:
            raise TranscriptionError(f"Unsupported sample rate {sample_rate} Hz for {label}")

        # Lock order everywhere: _infer_lock, then _load_lock (see unload()).
        with self._infer_lock:
            if self._model is None:
                self.load()  # raises ModelLoadError
            model = self._model
            try:
                start = time.perf_counter()
                result = model.recognize(samples, sample_rate=sample_rate)
                self._last_used = time.monotonic()
            except Exception as exc:
                raise TranscriptionError(f"Parakeet transcription failed: {exc}") from exc
        try:
            text = self._extract_text(result)
            logger.info(
                "Parakeet transcription for %s took %.2fs model='%s'",
                label,
                time.perf_counter() - start,
                self.model_path,
            )
            return text
        except Exception as exc:
            raise TranscriptionError(f"Parakeet transcription failed: {exc}") from exc

    def transcribe_stream(self, audio_chunk: Any, initial_prompt: Optional[str] = None) -> str:
        """Transcribe an in-memory 16 kHz mono chunk (kept for older call sites)."""
        return self.transcribe_array(audio_chunk, 16000, label="<stream>")

    @staticmethod
    def _extract_text(result: Any) -> str:
        """Extract text from possible onnx-asr result shapes."""
        if result is None:
            return ""

        if isinstance(result, str):
            return result.strip()

        # Common typed result objects may expose .text or .transcript.
        for attr in ("text", "transcript", "sentence"):
            value = getattr(result, attr, None)
            if isinstance(value, str):
                return value.strip()

        if isinstance(result, dict):
            for key in ("text", "transcript", "sentence"):
                value = result.get(key)
                if isinstance(value, str):
                    return value.strip()
            # Some APIs return segments/tokens; join recognizable text fields.
            for key in ("segments", "tokens", "results"):
                value = result.get(key)
                if isinstance(value, list):
                    parts = []
                    for item in value:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            item_text = item.get("text") or item.get("token")
                            if isinstance(item_text, str):
                                parts.append(item_text)
                        else:
                            item_text = getattr(item, "text", None)
                            if isinstance(item_text, str):
                                parts.append(item_text)
                    if parts:
                        return " ".join(parts).strip()

        if isinstance(result, (list, tuple)):
            parts = []
            for item in result:
                text = ParakeetEngine._extract_text(item)
                if text:
                    parts.append(text)
            if parts:
                return " ".join(parts).strip()

        return str(result).strip()

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        return {
            "loaded": self._loaded,
            "model_path": self.model_path,
            "model_name": self._model_name,
            "scorer_path": self.scorer_path,
            "model_type": self._model_type,
            "language": self.language,
            "device": "cpu",
            "runtime": "onnx-asr",
            "quantization": self._loaded_quantization if self._loaded else self.quantization,
            "intra_threads": self.intra_threads,
            "inter_threads": self.inter_threads,
            "sample_rate": 16000,
        }

    def unload(self) -> None:
        """Unload model to free memory (waits for a running inference)."""
        with self._infer_lock, self._load_lock:
            if self._model is None:
                return
            self._model = None
            self._loaded = False
        logger.info("Parakeet model unloaded")


_shared_engine: Optional[ParakeetEngine] = None
_shared_engine_lock = threading.Lock()


def get_shared_engine() -> ParakeetEngine:
    """The process-wide engine used by live and batch transcription.

    One model copy serves the whole app; creating a ParakeetEngine per caller
    is what used to load the ~2.6 GB model two or three times over.
    """
    global _shared_engine
    if _shared_engine is None:
        with _shared_engine_lock:
            if _shared_engine is None:
                _shared_engine = ParakeetEngine()
    return _shared_engine


def set_shared_engine(engine: Optional[ParakeetEngine]) -> None:
    """Test hook: inject an engine, or pass None to reset the singleton."""
    global _shared_engine
    with _shared_engine_lock:
        _shared_engine = engine


# Backwards-compatible aliases for call sites that used Whisper names.
WhisperError = ParakeetError
WhisperEngine = ParakeetEngine
