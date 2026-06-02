"""Parakeet ASR engine integration using ONNX Runtime via onnx-asr.

This module intentionally avoids importing torch, openai-whisper, faster-whisper,
or ROCm/CUDA libraries. It is designed for CPU-first transcription on Windows
machines where PyTorch GPU stacks may be unavailable or unstable.
"""

from __future__ import annotations

import logging
import os
import time
import wave
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


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
    """

    DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v3"
    DEFAULT_LANGUAGE = "auto"

    def __init__(
        self,
        model_path: Optional[str] = None,
        scorer_path: Optional[str] = None,
        language: str = DEFAULT_LANGUAGE,
        intra_threads: Optional[int] = None,
        inter_threads: int = 1,
    ):
        """Initialize Parakeet engine.

        Args:
            model_path: onnx-asr model name or local model directory. Defaults
                to ``nemo-parakeet-tdt-0.6b-v3``.
            scorer_path: Kept for API compatibility; unused.
            language: Kept for API compatibility. Parakeet v3 can auto-detect
                supported languages.
            intra_threads: Optional ONNX Runtime intra-op thread count. If not
                supplied, a conservative CPU default is used.
            inter_threads: Optional ONNX Runtime inter-op thread count.
        """
        self.model_path = model_path or self.DEFAULT_MODEL
        self.scorer_path = scorer_path
        self.language = language or self.DEFAULT_LANGUAGE
        self.intra_threads = intra_threads or max(1, min(os.cpu_count() or 1, 8))
        self.inter_threads = max(1, inter_threads)

        self._model: Any = None
        self._loaded = False
        self._model_type = "parakeet-onnx"
        self._model_name: Optional[str] = None

    def load(self) -> None:
        """Load Parakeet model through onnx-asr.

        Raises:
            ModelLoadError: If onnx-asr is missing or the model cannot be loaded.
        """
        if self._loaded:
            return

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
            logger.info(
                "Loaded Parakeet model '%s' using onnx-asr in %.2fs "
                "(intra_threads=%s, inter_threads=%s)",
                self.model_path,
                time.perf_counter() - start,
                self.intra_threads,
                self.inter_threads,
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load Parakeet model '{self.model_path}': {exc}"
            ) from exc

    def _load_onnx_asr_model(self, onnx_asr: Any) -> Any:
        """Load onnx-asr model while tolerating API differences by version."""
        # onnx-asr has evolved quickly. Try thread/provider-aware signatures
        # first, then fall back to the documented minimal call.
        attempts = [
            {
                "providers": ["CPUExecutionProvider"],
                "intra_threads": self.intra_threads,
                "inter_threads": self.inter_threads,
            },
            {
                "provider": "cpu",
                "intra_threads": self.intra_threads,
                "inter_threads": self.inter_threads,
            },
            {
                "intra_threads": self.intra_threads,
                "inter_threads": self.inter_threads,
            },
            {},
        ]

        last_exc: Optional[Exception] = None
        for kwargs in attempts:
            try:
                return onnx_asr.load_model(self.model_path, **kwargs)
            except TypeError as exc:
                # Signature mismatch; try next variant.
                last_exc = exc
                continue

        if last_exc:
            raise last_exc
        return onnx_asr.load_model(self.model_path)

    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded

    def _ensure_mono(self, audio_path: str) -> str:
        """Return a mono WAV path, converting stereo/multichannel audio if needed.

        Parakeet through onnx-asr expects mono audio. If the input file is
        already mono, the original path is returned. If it has more than one
        channel, a temporary mono WAV is created by averaging channels.
        The caller is responsible for deleting temporary files.
        """
        data, sample_rate = sf.read(audio_path)

        if len(data.shape) == 1:
            return audio_path

        logger.info(
            "Converting multichannel audio to mono before transcription: %s",
            audio_path,
        )

        mono = np.mean(data, axis=1)

        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_file.close()

        sf.write(temp_file.name, mono, sample_rate)
        return temp_file.name

    def transcribe(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe a WAV audio file to text.

        Args:
            audio_path: Path to WAV audio file.
            initial_prompt: Ignored. Kept for compatibility with previous
                Whisper-based processor calls.

        Returns:
            Transcribed text string.

        Raises:
            TranscriptionError: If transcription fails.
            ModelLoadError: If model cannot be loaded.
        """
        if not self._loaded:
            self.load()

        if self._model is None:
            raise ModelLoadError("Parakeet model failed to load")

        temp_audio_path: Optional[str] = None

        try:
            audio_path = str(audio_path)
            mono_audio_path = self._ensure_mono(audio_path)

            if mono_audio_path != audio_path:
                temp_audio_path = mono_audio_path

            start = time.perf_counter()
            result = self._model.recognize(mono_audio_path)
            text = self._extract_text(result)
            logger.info(
                "Parakeet transcription for %s took %.2fs model='%s'",
                audio_path,
                time.perf_counter() - start,
                self.model_path,
            )
            return text
        except Exception as exc:
            raise TranscriptionError(f"Parakeet transcription failed: {exc}") from exc
        finally:
            if temp_audio_path:
                try:
                    os.remove(temp_audio_path)
                except Exception:
                    logger.debug(
                        "Could not remove temporary mono audio file: %s",
                        temp_audio_path,
                        exc_info=True,
                    )

    def transcribe_stream(self, audio_chunk: Any, initial_prompt: Optional[str] = None) -> str:
        """Transcribe an in-memory audio chunk.

        onnx-asr can recognize NumPy arrays in current versions. This method is
        kept for API compatibility with older call sites.
        """
        if not self._loaded:
            self.load()

        if self._model is None:
            raise ModelLoadError("Parakeet model failed to load")

        try:
            start = time.perf_counter()
            result = self._model.recognize(audio_chunk)
            text = self._extract_text(result)
            logger.info(
                "Parakeet stream transcription took %.2fs model='%s'",
                time.perf_counter() - start,
                self.model_path,
            )
            return text
        except Exception as exc:
            raise TranscriptionError(f"Parakeet stream transcription failed: {exc}") from exc

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
            "intra_threads": self.intra_threads,
            "inter_threads": self.inter_threads,
            "sample_rate": 16000,
        }

    def unload(self) -> None:
        """Unload model to free memory."""
        self._model = None
        self._loaded = False
        logger.info("Parakeet model unloaded")


# Backwards-compatible aliases for call sites that used Whisper names.
WhisperError = ParakeetError
WhisperEngine = ParakeetEngine
