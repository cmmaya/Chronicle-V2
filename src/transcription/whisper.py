"""Whisper transcription engine integration.

This module supports two backends:

1. openai-whisper + PyTorch, used only when a dedicated GPU is detected and
   PyTorch can initialize that GPU safely.
2. faster-whisper, used by default on CPU or on systems with integrated GPUs
   where PyTorch/ROCm is not a good target for low-latency transcription.

For AMD/Radeon ROCm, PyTorch exposes the GPU through the torch.cuda API. Some
unsupported integrated Radeon GPUs can crash the Python process when
torch.cuda.is_available() is called, so GPU probing is done in a subprocess.
"""
import logging
import os
import platform
import subprocess
import sys
import time
import wave
import json
import tempfile
from typing import Optional, Dict, Any, List

import numpy as np

logger = logging.getLogger(__name__)


class WhisperError(Exception):
    """Base exception for Whisper transcription errors."""
    pass


class ModelLoadError(WhisperError):
    """Failed to load Whisper model."""
    pass


class TranscriptionError(WhisperError):
    """Failed to transcribe audio."""
    pass


class WhisperEngine:
    """Speech-to-text engine with automatic backend/device selection.

    Backend selection:
        - backend="auto":
            * dedicated GPU + working PyTorch GPU => openai-whisper on GPU
            * otherwise => faster-whisper on CPU
        - backend="openai":
            Force openai-whisper.
        - backend="faster":
            Force faster-whisper.

    Notes:
        The user's integrated "AMD Radeon(TM) Graphics" class of GPU should not
        be treated as a dedicated accelerator for Whisper. It commonly shares
        system RAM and may not be supported by ROCm/PyTorch on Windows.
    """

    DEFAULT_MODEL = "tiny"
    DEFAULT_LANGUAGE = "en"
    DEFAULT_BACKEND = "auto"

    def __init__(
        self,
        model_path: Optional[str] = None,
        scorer_path: Optional[str] = None,
        language: str = DEFAULT_LANGUAGE,
        device: Optional[str] = None,
        prefer_gpu: bool = True,
        fp16: Optional[bool] = None,
        backend: str = DEFAULT_BACKEND,
        faster_compute_type: Optional[str] = None,
    ):
        """Initialize transcription engine.

        Args:
            model_path: Whisper model name/path. If None, defaults to "base".
            scorer_path: Not used; kept for API compatibility.
            language: Language code. Defaults to English ("en").
            device: Explicit device. For openai-whisper use "cuda", "cpu", or
                    "mps". For faster-whisper use "cuda", "cpu", or "auto".
            prefer_gpu: Prefer a dedicated GPU when backend="auto".
            fp16: For openai-whisper decoding. If None, enabled only on GPU.
            backend: "auto", "openai", or "faster".
            faster_compute_type: faster-whisper compute type. If None:
                    CPU -> "int8"; GPU -> "float16".
        """
        self.model_path = model_path if model_path else self.DEFAULT_MODEL
        self.scorer_path = scorer_path
        self.language = language
        self.prefer_gpu = prefer_gpu
        self.requested_device = device
        self.backend_preference = (backend or self.DEFAULT_BACKEND).lower()
        self.faster_compute_type = faster_compute_type

        if self.backend_preference not in {"auto", "openai", "faster"}:
            raise ValueError("backend must be one of: 'auto', 'openai', 'faster'")

        self._model = None
        self._loaded = False
        self._faster_isolated = False
        self._model_type: Optional[str] = None
        self._model_name: Optional[str] = None

        self._gpu_info = self._detect_gpu_info()
        self.backend, self.device = self._select_backend_and_device()
        self.fp16 = fp16 if fp16 is not None else self.device in {"cuda", "mps"}

        if self.backend == "faster" and self.faster_compute_type is None:
            self.faster_compute_type = "float16" if self.device == "cuda" else "int8"

    # ---------------------------------------------------------------------
    # Hardware/backend selection
    # ---------------------------------------------------------------------
    @staticmethod
    def _run_command(command: List[str], timeout: float = 5.0) -> str:
        """Run a short system command and return stdout safely."""
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode != 0:
                return ""
            return completed.stdout.strip()
        except Exception:
            return ""

    @classmethod
    def _get_windows_gpu_names(cls) -> List[str]:
        """Return Windows GPU names without using torch.cuda."""
        if os.name != "nt":
            return []

        # PowerShell CIM is more reliable than wmic on newer Windows, but wmic
        # is still useful on older installations. Try both.
        ps = cls._run_command([
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ])
        names = [line.strip() for line in ps.splitlines() if line.strip()]
        if names:
            return names

        wmic = cls._run_command(["wmic", "path", "win32_VideoController", "get", "name"])
        names = []
        for line in wmic.splitlines():
            line = line.strip()
            if line and line.lower() != "name":
                names.append(line)
        return names

    @staticmethod
    def _looks_like_integrated_gpu(name: str) -> bool:
        """Heuristic for integrated GPUs that should not be selected for Whisper."""
        n = name.lower()

        integrated_markers = [
            "radeon(tm) graphics",
            "amd radeon graphics",
            "vega",
            "intel",
            "iris",
            "uhd graphics",
            "integrated",
            "apu",
        ]

        dedicated_markers = [
            "nvidia",
            "geforce",
            "rtx",
            "gtx",
            "quadro",
            "tesla",
            "radeon rx",
            "radeon pro",
            "rx 6",
            "rx 7",
            "rx 8",
            "rx 9",
            "w7800",
            "w7900",
        ]

        if any(marker in n for marker in dedicated_markers):
            return False
        if any(marker in n for marker in integrated_markers):
            return True

        # Conservative default: unknown GPUs are not treated as dedicated.
        return True

    @classmethod
    def _detect_gpu_info(cls) -> Dict[str, Any]:
        """Detect GPU names and whether any looks dedicated without torch."""
        names: List[str] = []

        if os.name == "nt":
            names = cls._get_windows_gpu_names()
        else:
            # Best-effort Linux/macOS fallback.
            lspci = cls._run_command(["bash", "-lc", "lspci | grep -Ei 'vga|3d|display' || true"])
            names = [line.strip() for line in lspci.splitlines() if line.strip()]

        dedicated = [
            name for name in names
            if not cls._looks_like_integrated_gpu(name)
        ]

        return {
            "gpu_names": names,
            "dedicated_gpu_names": dedicated,
            "has_dedicated_gpu": bool(dedicated),
        }

    @staticmethod
    def _probe_torch_gpu_subprocess(timeout: float = 8.0) -> Dict[str, Any]:
        """Probe torch.cuda in a subprocess so ROCm crashes do not kill the app."""
        code = (
            "import json, torch\n"
            "ok = bool(torch.cuda.is_available())\n"
            "count = int(torch.cuda.device_count()) if ok else 0\n"
            "name = torch.cuda.get_device_name(0) if ok and count > 0 else None\n"
            "hip = getattr(torch.version, 'hip', None)\n"
            "cuda = getattr(torch.version, 'cuda', None)\n"
            "print(json.dumps({'ok': ok, 'count': count, 'name': name, "
            "'hip': hip, 'cuda': cuda, 'torch': torch.__version__}))\n"
        )

        try:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode != 0:
                return {
                    "ok": False,
                    "error": completed.stderr.strip() or f"returncode={completed.returncode}",
                }

            import json
            return json.loads(completed.stdout.strip())
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "torch GPU probe timed out"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _select_backend_and_device(self) -> tuple[str, str]:
        """Select backend and device.

        Critical safety behavior:
            In auto mode, do not call torch.cuda in this process. If no
            dedicated GPU is detected, select faster-whisper CPU immediately.
        """
        if self.backend_preference == "faster":
            device = self.requested_device or "cpu"
            if device == "auto":
                device = "cpu"
            return "faster", device

        if self.backend_preference == "openai":
            device = self.requested_device or ("cuda" if self.prefer_gpu else "cpu")
            return "openai", device

        # Auto mode.
        if not self.prefer_gpu:
            return "faster", "cpu"

        if self.requested_device:
            if self.requested_device == "cpu":
                return "faster", "cpu"
            # If the caller explicitly asks for cuda, validate it in a subprocess.
            probe = self._probe_torch_gpu_subprocess()
            if probe.get("ok"):
                return "openai", self.requested_device
            logger.warning(
                "Requested device='%s', but PyTorch GPU probe failed (%s). "
                "Falling back to faster-whisper CPU.",
                self.requested_device,
                probe.get("error", "GPU unavailable"),
            )
            return "faster", "cpu"

        if not self._gpu_info.get("has_dedicated_gpu"):
            logger.info(
                "No dedicated GPU detected (%s). Using faster-whisper on CPU.",
                self._gpu_info.get("gpu_names") or "no GPU names found",
            )
            return "faster", "cpu"

        probe = self._probe_torch_gpu_subprocess()
        if probe.get("ok"):
            logger.info(
                "Dedicated GPU detected and PyTorch GPU probe succeeded: %s",
                probe.get("name"),
            )
            return "openai", "cuda"

        logger.warning(
            "Dedicated-looking GPU detected (%s), but PyTorch GPU probe failed (%s). "
            "Using faster-whisper on CPU.",
            self._gpu_info.get("dedicated_gpu_names"),
            probe.get("error", "GPU unavailable"),
        )
        return "faster", "cpu"

    # ---------------------------------------------------------------------
    # Model loading
    # ---------------------------------------------------------------------
    def load(self) -> None:
        """Load the selected transcription model.

        Safety rule:
            - torch/openai-whisper is used only when a dedicated GPU is detected
              and PyTorch GPU probing succeeds in a subprocess.
            - CPU fallback uses faster-whisper. There is intentionally no
              openai-whisper CPU fallback, because that would import torch in
              the main process on machines where torch/ROCm may be unstable.
        """
        if self._loaded:
            return

        if self.backend == "faster":
            self._load_faster_whisper()
            return

        if self.backend == "openai" and self.device not in {"cuda", "mps"}:
            raise ModelLoadError(
                "Refusing to load openai-whisper without a GPU. This build is "
                "configured to use torch/openai-whisper only for GPU inference. "
                "Use backend='faster' for CPU."
            )

        self._load_openai_whisper()

    def _load_openai_whisper(self) -> None:
        """Load openai-whisper/PyTorch backend."""
        try:
            import whisper

            start = time.perf_counter()
            self._model = whisper.load_model(self.model_path, device=self.device)
            self._model_name = self.model_path
            self._model_type = "openai-whisper"
            self._loaded = True

            logger.info(
                "Loaded openai-whisper model '%s' on device='%s' in %.2fs",
                self.model_path,
                self.device,
                time.perf_counter() - start,
            )
        except ImportError as exc:
            raise ModelLoadError(
                "Failed to import openai-whisper. Install it with: pip install openai-whisper"
            ) from exc
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load openai-whisper model '{self.model_path}' on device "
                f"'{self.device}': {exc}"
            ) from exc

    def _load_faster_whisper(self) -> None:
        """Load faster-whisper/CTranslate2 backend.

        If the import or native DLL initialization fails in the GUI process, the
        engine falls back to isolated subprocess mode. This is useful on Windows
        machines where a stale/broken torch install causes c10.dll failures in
        the main application process, while a clean Python subprocess can still
        run faster-whisper correctly.
        """
        try:
            from faster_whisper import WhisperModel
        except (ImportError, OSError) as exc:
            logger.warning(
                "Could not import faster-whisper in the main process (%s). "
                "Falling back to isolated faster-whisper subprocess mode.",
                exc,
            )
            self._model = None
            self._faster_isolated = True
            self._model_name = self.model_path
            self._model_type = "faster-whisper-isolated"
            self._loaded = True
            return

        compute_candidates = []
        if self.faster_compute_type:
            compute_candidates.append(self.faster_compute_type)
        if self.device == "cpu":
            compute_candidates.extend(["int8", "int8_float32", "float32"])
        else:
            compute_candidates.extend(["float16", "int8", "float32"])

        compute_candidates = list(dict.fromkeys(compute_candidates))
        errors = []

        for compute_type in compute_candidates:
            try:
                start = time.perf_counter()
                self._model = WhisperModel(
                    self.model_path,
                    device=self.device,
                    compute_type=compute_type,
                    cpu_threads=max(1, min(os.cpu_count() or 1, 8)),
                    num_workers=1,
                )
                self.faster_compute_type = compute_type
                self._model_name = self.model_path
                self._model_type = "faster-whisper"
                self._loaded = True
                self._faster_isolated = False

                logger.info(
                    "Loaded faster-whisper model '%s' on device='%s' compute_type='%s' in %.2fs",
                    self.model_path,
                    self.device,
                    self.faster_compute_type,
                    time.perf_counter() - start,
                )
                return
            except Exception as exc:
                errors.append(f"{compute_type}: {exc}")
                logger.warning(
                    "Failed to load faster-whisper model '%s' on device='%s' compute_type='%s': %s",
                    self.model_path,
                    self.device,
                    compute_type,
                    exc,
                )
                self._model = None
                self._loaded = False

        logger.warning(
            "All in-process faster-whisper load attempts failed. Falling back to "
            "isolated subprocess mode. Errors: %s",
            " | ".join(errors),
        )
        self._model = None
        self._faster_isolated = True
        self._model_name = self.model_path
        self._model_type = "faster-whisper-isolated"
        self._loaded = True

    def _transcribe_faster_isolated(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe with faster-whisper in a fresh Python subprocess."""
        compute_type = self.faster_compute_type or "int8"
        payload = {
            "audio_path": audio_path,
            "model_path": self.model_path,
            "language": self.language,
            "compute_type": compute_type,
            "initial_prompt": initial_prompt,
            "cpu_threads": max(1, min(os.cpu_count() or 1, 8)),
        }

        child_code = """
import json
import os
import sys
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
with open(sys.argv[1], "r", encoding="utf-8") as f:
    payload = json.load(f)
from faster_whisper import WhisperModel
model = WhisperModel(
    payload["model_path"],
    device="cpu",
    compute_type=payload["compute_type"],
    cpu_threads=payload["cpu_threads"],
    num_workers=1,
)
options = {
    "language": payload["language"],
    "beam_size": 1,
    "best_of": 1,
    "condition_on_previous_text": False,
    "vad_filter": False,
}
if payload.get("initial_prompt"):
    options["initial_prompt"] = payload["initial_prompt"]
segments, _info = model.transcribe(payload["audio_path"], **options)
text = "".join(segment.text for segment in segments).strip()
print(json.dumps({"text": text}, ensure_ascii=False))
"""
        fd, json_path = tempfile.mkstemp(prefix="chronicle_fw_", suffix=".json")
        os.close(fd)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

            env = os.environ.copy()
            env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
            env.setdefault("OMP_NUM_THREADS", str(payload["cpu_threads"]))

            completed = subprocess.run(
                [sys.executable, "-c", child_code, json_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=180,
            )
            if completed.returncode != 0:
                raise TranscriptionError(
                    "isolated faster-whisper subprocess failed: "
                    f"{completed.stderr.strip() or completed.stdout.strip()}"
                )

            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if not lines:
                return ""
            result = json.loads(lines[-1])
            return result.get("text", "")
        finally:
            try:
                os.remove(json_path)
            except OSError:
                pass

    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded

    # ---------------------------------------------------------------------
    # Audio loading for openai-whisper/stream support
    # ---------------------------------------------------------------------
    def _load_audio_file(self, audio_path: str) -> np.ndarray:
        """Load and preprocess WAV audio to 16 kHz mono float32."""
        try:
            with wave.open(str(audio_path), "rb") as wf:
                channels = wf.getnchannels()
                if channels not in (1, 2):
                    raise TranscriptionError(f"Unsupported channel count: {channels}")

                frames = wf.readframes(wf.getnframes())
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()

                if sample_width == 2:
                    audio = np.frombuffer(frames, dtype=np.int16)
                    audio = audio.astype(np.float32) / 32768.0
                elif sample_width == 4:
                    audio = np.frombuffer(frames, dtype=np.int32)
                    audio = audio.astype(np.float32) / 2147483648.0
                else:
                    raise TranscriptionError(f"Unsupported sample width: {sample_width}")

                if channels == 2:
                    audio = audio.reshape(-1, 2).mean(axis=1)

                if sample_rate != 16000:
                    audio = self._resample(audio, sample_rate, 16000)

                return audio.astype(np.float32, copy=False)

        except TranscriptionError:
            raise
        except wave.Error as exc:
            raise TranscriptionError(f"Failed to read audio file: {exc}") from exc
        except Exception as exc:
            raise TranscriptionError(f"Audio preprocessing failed: {exc}") from exc

    def _resample(self, audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
        """Simple audio resampling using linear interpolation."""
        if orig_rate == target_rate:
            return audio.astype(np.float32, copy=False)

        new_length = int(len(audio) * target_rate / orig_rate)
        x_orig = np.linspace(0, 1, len(audio))
        x_new = np.linspace(0, 1, new_length)
        return np.interp(x_new, x_orig, audio).astype(np.float32)

    # ---------------------------------------------------------------------
    # Transcription
    # ---------------------------------------------------------------------
    def _openai_transcribe_options(self, initial_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Build low-latency openai-whisper options."""
        options: Dict[str, Any] = {
            "language": self.language,
            "fp16": self.fp16,
            "condition_on_previous_text": False,
            "verbose": False,
        }
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        return options

    def _faster_transcribe_options(self, initial_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Build low-latency faster-whisper options."""
        options: Dict[str, Any] = {
            "language": self.language,
            "beam_size": 1,
            "best_of": 1,
            "condition_on_previous_text": False,
            "vad_filter": False,
        }
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        return options

    def transcribe(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe an audio file to text."""
        if not self._loaded:
            self.load()

        if self.backend == "faster":
            if self._faster_isolated:
                return self._transcribe_faster_isolated(audio_path, initial_prompt=initial_prompt)
            if self._model is None:
                raise ModelLoadError("Model failed to load")
            return self._transcribe_faster(audio_path, initial_prompt=initial_prompt)

        if self._model is None:
            raise ModelLoadError("Model failed to load")
        return self._transcribe_openai(audio_path, initial_prompt=initial_prompt)

    def _transcribe_openai(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe using openai-whisper."""
        try:
            start = time.perf_counter()
            audio = self._load_audio_file(audio_path)
            logger.debug(
                "Audio load/preprocess for %s took %.2fs",
                audio_path,
                time.perf_counter() - start,
            )

            options = self._openai_transcribe_options(initial_prompt=initial_prompt)

            start = time.perf_counter()
            try:
                result = self._model.transcribe(audio, **options)
            except Exception as exc:
                if options.get("fp16"):
                    logger.warning(
                        "openai-whisper fp16 transcription failed on device='%s'. "
                        "Retrying once with fp16=False. Error: %s",
                        self.device,
                        exc,
                    )
                    options["fp16"] = False
                    result = self._model.transcribe(audio, **options)
                else:
                    raise

            logger.info(
                "openai-whisper inference for %s took %.2fs on device='%s' model='%s'",
                audio_path,
                time.perf_counter() - start,
                self.device,
                self.model_path,
            )
            return result.get("text", "")

        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"openai-whisper transcription failed: {exc}") from exc

    def _transcribe_faster(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe using faster-whisper."""
        try:
            options = self._faster_transcribe_options(initial_prompt=initial_prompt)

            start = time.perf_counter()
            segments, _info = self._model.transcribe(audio_path, **options)
            text = "".join(segment.text for segment in segments).strip()

            logger.info(
                "faster-whisper inference for %s took %.2fs on device='%s' model='%s' compute_type='%s'",
                audio_path,
                time.perf_counter() - start,
                self.device,
                self.model_path,
                self.faster_compute_type,
            )
            return text

        except Exception as exc:
            raise TranscriptionError(f"faster-whisper transcription failed: {exc}") from exc

    def transcribe_stream(self, audio_chunk: np.ndarray, initial_prompt: Optional[str] = None) -> str:
        """Transcribe an audio chunk.

        For faster-whisper, the chunk must be a mono float32 numpy array sampled
        at 16 kHz. For openai-whisper, this matches the original behavior.
        """
        if not self._loaded:
            self.load()

        if self.backend == "faster" and self._faster_isolated:
            raise TranscriptionError("transcribe_stream is not supported in isolated faster-whisper mode")

        if self._model is None:
            raise ModelLoadError("Model failed to load")

        try:
            if len(audio_chunk.shape) > 1:
                audio_chunk = audio_chunk.mean(axis=1)
            audio_chunk = audio_chunk.astype(np.float32, copy=False)

            if self.backend == "faster":
                options = self._faster_transcribe_options(initial_prompt=initial_prompt)
                segments, _info = self._model.transcribe(audio_chunk, **options)
                return "".join(segment.text for segment in segments).strip()

            options = self._openai_transcribe_options(initial_prompt=initial_prompt)
            try:
                result = self._model.transcribe(audio_chunk, **options)
            except Exception as exc:
                if options.get("fp16"):
                    logger.warning(
                        "openai-whisper stream fp16 transcription failed on device='%s'. "
                        "Retrying once with fp16=False. Error: %s",
                        self.device,
                        exc,
                    )
                    options["fp16"] = False
                    result = self._model.transcribe(audio_chunk, **options)
                else:
                    raise
            return result.get("text", "")

        except Exception as exc:
            raise TranscriptionError(f"Stream transcription failed: {exc}") from exc

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the selected transcription engine."""
        info: Dict[str, Any] = {
            "loaded": self._loaded,
            "model_path": self.model_path,
            "model_name": self._model_name,
            "scorer_path": self.scorer_path,
            "model_type": self._model_type or self.backend,
            "backend": self.backend,
            "backend_preference": self.backend_preference,
            "language": self.language,
            "device": self.device,
            "requested_device": self.requested_device,
            "prefer_gpu": self.prefer_gpu,
            "fp16": self.fp16,
            "faster_compute_type": self.faster_compute_type,
            "gpu_names": self._gpu_info.get("gpu_names"),
            "dedicated_gpu_names": self._gpu_info.get("dedicated_gpu_names"),
            "has_dedicated_gpu": self._gpu_info.get("has_dedicated_gpu"),
            "sample_rate": 16000,
        }

        # Only probe torch in a subprocess to avoid killing the app on unsupported
        # ROCm devices.
        info["torch_gpu_probe"] = self._probe_torch_gpu_subprocess(timeout=5.0)

        return info

    def unload(self) -> None:
        """Unload the model to free memory."""
        self._model = None
        self._loaded = False
        logger.info("Transcription model unloaded")
