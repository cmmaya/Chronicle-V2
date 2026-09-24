"""Which models Chronicle needs, whether they are on disk, and how to get them (BU123).

Both models live in the Hugging Face cache (``HF_HOME``, set to
``paths.models_dir()`` by ``main.py``) at a pinned revision:

- Parakeet ``nemo-parakeet-tdt-0.6b-v3`` via onnx-asr, only the files for the
  configured ``TRANSCRIPTION['quantization']``;
- ``intfloat/multilingual-e5-small`` for RAG routing.

:func:`status` reads the disk only. :func:`download` fetches what is missing
with progress, resume and cancel. Once everything is installed the app turns on
``HF_HUB_OFFLINE`` (:func:`enable_offline_if_installed`), and the loaders use
:func:`local_path` - so a model is never downloaded silently mid-session.

Run ``python -m src.model_manager`` to download from a terminal.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import TRANSCRIPTION

logger = logging.getLogger(__name__)

PARAKEET = "parakeet"
EMBEDDINGS = "embeddings"

# onnx_asr.resolver.model_repos["nemo-parakeet-tdt-0.6b-v3"]
PARAKEET_REPO_ID = "istupakov/parakeet-tdt-0.6b-v3-onnx"
PARAKEET_REVISION = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"

# Kept equal to src/rag/embeddings.py (EMBED_REPO_ID / EMBED_REVISION), which
# stores this id with every vector.
E5_REPO_ID = "intfloat/multilingual-e5-small"
E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"

# File -> size in bytes at the pinned revision. What onnx-asr's resolver loads
# for NemoConformerTdt: config.json, vocab.txt, the encoder and decoder_joint
# for the quantization (plus the encoder's external .onnx.data for full precision).
_PARAKEET_FILES: Dict[Optional[str], Dict[str, int]] = {
    None: {
        "config.json": 97,
        "vocab.txt": 93_939,
        "encoder-model.onnx": 41_770_866,
        "encoder-model.onnx.data": 2_435_420_160,
        "decoder_joint-model.onnx": 72_520_893,
    },
    "int8": {
        "config.json": 97,
        "vocab.txt": 93_939,
        "encoder-model.int8.onnx": 652_183_999,
        "decoder_joint-model.int8.onnx": 18_202_004,
    },
}

_E5_FILES = {
    "onnx/model.onnx": 470_268_510,
    "onnx/tokenizer.json": 17_082_730,
}

# Headroom on top of the download itself (partial files, filesystem slack).
DISK_MARGIN_BYTES = 200 * 1024 * 1024

SETUP_HINT = "Run Chronicle setup to download it (or: python -m src.model_manager)."


# -- errors ------------------------------------------------------------------

class ModelManagerError(Exception):
    """Base error; the message is meant for the user."""


class ModelNotInstalledError(ModelManagerError):
    """A model's files are not on disk."""


class OfflineError(ModelManagerError):
    """Hugging Face could not be reached."""


class DiskFullError(ModelManagerError):
    """Not enough free space for the download."""


class DownloadError(ModelManagerError):
    """Any other download failure."""


class DownloadCancelled(ModelManagerError):
    """The cancel event was set; partial files are kept for resuming."""


# -- the model list ------------------------------------------------------------

@dataclass(frozen=True)
class RequiredModel:
    name: str
    repo_id: str
    revision: str
    files: Dict[str, int]  # filename -> expected size in bytes

    @property
    def total_bytes(self) -> int:
        return sum(self.files.values())


@dataclass(frozen=True)
class ModelStatus:
    name: str
    state: str  # "installed" | "missing" | "incomplete"
    bytes_missing: int
    missing_files: List[str]

    @property
    def installed(self) -> bool:
        return self.state == "installed"


_FROM_CONFIG = object()


def required_models(quantization=_FROM_CONFIG) -> List[RequiredModel]:
    """The models the app needs, with the Parakeet files for ``quantization``.

    By default the quantization is ``config.TRANSCRIPTION['quantization']`` -
    exactly the variant the engine will load.
    """
    if quantization is _FROM_CONFIG:
        quantization = TRANSCRIPTION.get("quantization")
    if quantization not in _PARAKEET_FILES:
        raise ModelManagerError(f"Unsupported Parakeet quantization: {quantization!r}")
    return [
        RequiredModel(PARAKEET, PARAKEET_REPO_ID, PARAKEET_REVISION, dict(_PARAKEET_FILES[quantization])),
        RequiredModel(EMBEDDINGS, E5_REPO_ID, E5_REVISION, dict(_E5_FILES)),
    ]


REQUIRED_MODELS = required_models()


# -- where things are on disk ----------------------------------------------------

def hub_cache() -> Path:
    """The Hugging Face hub cache for the current environment (read at call time)."""
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return Path(explicit)
    home = os.environ.get("HF_HOME")
    if not home:
        xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        home = os.path.join(xdg, "huggingface")
    return Path(home) / "hub"


def snapshot_dir(model: RequiredModel, cache_dir: Optional[Path] = None) -> Path:
    cache = Path(cache_dir) if cache_dir else hub_cache()
    return cache / f"models--{model.repo_id.replace('/', '--')}" / "snapshots" / model.revision


def _get(name: str, models: Optional[List[RequiredModel]] = None) -> RequiredModel:
    for model in models or required_models():
        if model.name == name:
            return model
    raise KeyError(name)


def _file_ok(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def status(cache_dir: Optional[Path] = None,
           models: Optional[List[RequiredModel]] = None) -> List[ModelStatus]:
    """Installed / missing / incomplete per model. Disk only - no network."""
    result = []
    for model in models or required_models():
        root = snapshot_dir(model, cache_dir)
        missing = [f for f, size in model.files.items() if not _file_ok(root / f, size)]
        bytes_missing = sum(model.files[f] for f in missing)
        if not missing:
            state = "installed"
        elif len(missing) == len(model.files) and not _has_partial(model, cache_dir):
            state = "missing"
        else:
            state = "incomplete"
        result.append(ModelStatus(model.name, state, bytes_missing, missing))
    return result


def _has_partial(model: RequiredModel, cache_dir: Optional[Path]) -> bool:
    blobs = snapshot_dir(model, cache_dir).parent.parent / "blobs"
    return blobs.is_dir() and any(blobs.glob("*.incomplete"))


def is_installed(cache_dir: Optional[Path] = None,
                 models: Optional[List[RequiredModel]] = None) -> bool:
    return all(s.installed for s in status(cache_dir, models))


def bytes_to_download(cache_dir: Optional[Path] = None,
                      models: Optional[List[RequiredModel]] = None) -> int:
    return sum(s.bytes_missing for s in status(cache_dir, models))


def local_path(name: str, filename: Optional[str] = None,
               cache_dir: Optional[Path] = None) -> Path:
    """The installed snapshot folder (or one file in it) for model ``name``.

    Raises:
        ModelNotInstalledError: if any of the model's files is missing.
    """
    model = _get(name)
    [st] = status(cache_dir, [model])
    if not st.installed:
        label = "speech-to-text model (Parakeet)" if name == PARAKEET else "embedding model (e5)"
        raise ModelNotInstalledError(
            f"The {label} is not installed ({len(st.missing_files)} file(s) missing). {SETUP_HINT}"
        )
    root = snapshot_dir(model, cache_dir)
    return root / filename if filename else root


def enable_offline_if_installed(cache_dir: Optional[Path] = None) -> bool:
    """Turn on ``HF_HUB_OFFLINE`` for this run when every model is installed."""
    if not is_installed(cache_dir):
        return False
    os.environ["HF_HUB_OFFLINE"] = "1"
    # huggingface_hub reads the variable once, at import.
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:
        constants.HF_HUB_OFFLINE = True
    return True


# -- downloading -----------------------------------------------------------------

ProgressCallback = Callable[[str, int, int], None]  # (model, bytes_done, bytes_total)


def _existing_ancestor(path: Path) -> Path:
    path = path.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def check_disk_space(needed_bytes: int, cache_dir: Optional[Path] = None) -> None:
    """Raise DiskFullError unless ``needed_bytes`` (plus a margin) fit."""
    if needed_bytes <= 0:
        return
    target = Path(cache_dir) if cache_dir else hub_cache()
    free = shutil.disk_usage(_existing_ancestor(target)).free
    required = needed_bytes + DISK_MARGIN_BYTES
    if free < required:
        raise DiskFullError(
            f"Not enough disk space for the models: {required / 1e9:.1f} GB needed on "
            f"{_existing_ancestor(target).anchor or target}, {free / 1e9:.1f} GB free."
        )


class _Progress:
    """Minimal stand-in for tqdm that hf_hub_download reports bytes to.

    ``initial`` is the size already on disk when a download resumes. Raising
    from ``update`` is how a cancel stops the transfer mid-file.
    """

    def __init__(self, on_bytes: Callable[[int], None], cancel_event: Optional[threading.Event]):
        self._on_bytes = on_bytes
        self._cancel = cancel_event

    def __call__(self, *args, initial: int = 0, **kwargs) -> "_Progress":
        self._set(initial or 0)
        return self

    def __enter__(self) -> "_Progress":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def _set(self, done: int) -> None:
        self.done = done
        self._on_bytes(done)

    def update(self, n: float = 1) -> None:
        self._set(self.done + int(n))
        if self._cancel is not None and self._cancel.is_set():
            raise DownloadCancelled("Download cancelled.")

    # tqdm methods hf_hub_download may call on the bar.
    def close(self) -> None:
        pass

    def reset(self, total=None) -> None:
        self._set(0)

    def refresh(self) -> None:
        pass

    def set_description(self, *args, **kwargs) -> None:
        pass


HF_ENDPOINT = "https://huggingface.co"


def _hub_reachable() -> bool:
    import httpx
    try:
        httpx.head(os.environ.get("HF_ENDPOINT") or HF_ENDPOINT, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def _map_error(exc: Exception) -> ModelManagerError:
    """A typed, readable error for a failed ``hf_hub_download``."""
    if isinstance(exc, ModelManagerError):
        return exc
    import errno
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        return DiskFullError("The disk filled up while downloading the models. Free some space and retry.")
    import httpx
    network_errors = (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
    from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
    network_errors = network_errors + (LocalEntryNotFoundError, OfflineModeIsEnabled)
    # huggingface_hub's retry can also surface a lost connection as e.g.
    # "RuntimeError: client has been closed", so anything else is checked
    # against a direct probe before calling it a download failure.
    if isinstance(exc, network_errors) or not _hub_reachable():
        return OfflineError(
            "Could not reach Hugging Face to download the models. "
            "Check your internet connection and try again - finished files are kept."
        )
    return DownloadError(f"Model download failed: {type(exc).__name__}: {exc}")


def download(progress_cb: Optional[ProgressCallback] = None,
             cancel_event: Optional[threading.Event] = None,
             cache_dir: Optional[Path] = None,
             models: Optional[List[RequiredModel]] = None) -> None:
    """Fetch every missing model file.

    Reports ``(model, bytes_done, bytes_total)`` per model. A partial file left
    by an interrupted run is resumed. On cancel, raises DownloadCancelled with
    the partial file kept.

    Raises:
        DiskFullError, OfflineError, DownloadError, DownloadCancelled
    """
    import huggingface_hub.constants as hf_constants

    models = models or required_models()
    cache = Path(cache_dir) if cache_dir else hub_cache()
    check_disk_space(bytes_to_download(cache, models), cache)

    # Plain HTTP instead of Xet: a Xet transfer ignores the cancel raised from
    # the progress callback until the whole file is done (minutes for the
    # 2.4 GB encoder), while HTTP stops at the next chunk and resumes the
    # .incomplete file with a Range request. Read per call by huggingface_hub.
    xet_was_disabled = hf_constants.HF_HUB_DISABLE_XET
    hf_constants.HF_HUB_DISABLE_XET = True
    try:
        _download(models, cache, progress_cb, cancel_event)
    finally:
        hf_constants.HF_HUB_DISABLE_XET = xet_was_disabled


def _download(models: List[RequiredModel], cache: Path,
              progress_cb: Optional[ProgressCallback],
              cancel_event: Optional[threading.Event]) -> None:
    from huggingface_hub import hf_hub_download

    for model in models:
        root = snapshot_dir(model, cache)
        todo = [f for f, size in model.files.items() if not _file_ok(root / f, size)]
        if not todo:
            continue
        total = model.total_bytes
        finished = total - sum(model.files[f] for f in todo)
        if progress_cb:
            progress_cb(model.name, finished, total)

        for filename in todo:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled("Download cancelled.")
            base = finished

            def report(done: int, _base=base, _name=model.name, _total=total) -> None:
                if progress_cb:
                    progress_cb(_name, min(_base + done, _total), _total)

            # A file already in the snapshot at the wrong size is corrupt:
            # hf_hub_download would return it as-is, so force a fresh copy.
            corrupt = (root / filename).exists()
            try:
                hf_hub_download(
                    model.repo_id,
                    filename,
                    revision=model.revision,
                    cache_dir=str(cache),
                    force_download=corrupt,
                    tqdm_class=_Progress(report, cancel_event),
                )
            except Exception as exc:  # noqa: BLE001 - mapped to a typed error
                raise _map_error(exc) from exc
            if not _file_ok(root / filename, model.files[filename]):
                raise DownloadError(
                    f"{model.repo_id}/{filename} downloaded with an unexpected size."
                )
            finished += model.files[filename]
            if progress_cb:
                progress_cb(model.name, finished, total)


def _main() -> int:
    logging.basicConfig(level=logging.WARNING)
    for st in status():
        print(f"{st.name}: {st.state} ({st.bytes_missing / 1e9:.2f} GB to download)")
    if is_installed():
        print(f"All models installed in {hub_cache()}")
        return 0

    last = {}

    def show(name: str, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        if last.get(name) != pct:
            last[name] = pct
            print(f"\r{name}: {pct:3d}% ({done / 1e9:.2f} / {total / 1e9:.2f} GB)", end="", flush=True)
            if pct == 100:
                print()

    try:
        download(show)
    except KeyboardInterrupt:
        print("\nCancelled - run again to resume.")
        return 1
    except ModelManagerError as exc:
        print(f"\n{exc}")
        return 1
    print(f"All models installed in {hub_cache()}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
