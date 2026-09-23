"""Runtime store for the tag artifact and the user's custom tag file.

ComfyUI-facing: this module may use folder_paths and the ComfyUI user directory.
Nothing under build/ may import it.

There is no bundled tag data. The artifact is downloaded once into the ComfyUI
user directory and reused offline; `DTA_LOCAL_ARTIFACT` points it at a local build
output instead, which is how the project is developed and verified before a
release exists.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import requests

from .artifact import Artifact, TagIndex, build_custom_overlay, decode

CACHE_DIRNAME = "danbooru-tag-autocomplete"
ARTIFACT_FILENAME = "tags.bin.gz"
METADATA_FILENAME = "metadata.json"
CUSTOM_FILENAMES = ("custom_tags.csv", "custom_tags.json")
DEFAULT_REPO_SLUG = "chynggi/ComfyUI-Danbooru-Tag-Autocomplete"

LATEST_URL_ENV = "DTA_LATEST_URL"
REPO_SLUG_ENV = "DTA_REPO_SLUG"
LOCAL_ARTIFACT_ENV = "DTA_LOCAL_ARTIFACT"

STATE_READY = "ready"
STATE_DOWNLOADING = "downloading"
STATE_MISSING = "missing"
STATE_ERROR = "error"

log = logging.getLogger(__name__)

_lock = threading.Lock()
_state = STATE_MISSING
_error: str | None = None
_download_thread: threading.Thread | None = None
_artifact_cache: tuple[tuple[Path, int, int], Artifact] | None = None
_custom_cache: tuple[tuple[Path, int], tuple[Artifact | None, tuple[str, ...]]] | None = None


@dataclass(frozen=True, slots=True)
class Status:
    state: str
    data_version: str | None
    error: str | None


def cache_dir() -> Path:
    """Directory holding the cached artifact.

    folder_paths is imported here rather than at module scope so this module can be
    imported outside a running ComfyUI, which is what the tests rely on.
    """
    import folder_paths

    return Path(folder_paths.get_user_directory()) / CACHE_DIRNAME


def _local_artifact() -> Path | None:
    value = os.environ.get(LOCAL_ARTIFACT_ENV)
    return Path(value) if value else None


def artifact_path() -> Path:
    local = _local_artifact()
    if local is not None:
        return local
    return cache_dir() / ARTIFACT_FILENAME


def metadata_path() -> Path:
    local = _local_artifact()
    if local is not None:
        return local.with_name(METADATA_FILENAME)
    return cache_dir() / METADATA_FILENAME


def custom_file_path() -> Path | None:
    for name in CUSTOM_FILENAMES:
        candidate = cache_dir() / name
        if candidate.exists():
            return candidate
    return None


def _latest_url() -> str:
    override = os.environ.get(LATEST_URL_ENV)
    if override:
        return override
    slug = os.environ.get(REPO_SLUG_ENV, DEFAULT_REPO_SLUG)
    return f"https://raw.githubusercontent.com/{slug}/main/data/latest.json"


def _data_version() -> str | None:
    path = metadata_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("data_version")
    except (OSError, ValueError):
        return None


def status() -> Status:
    """Report the cache state. Never raises."""
    if artifact_path().exists() and metadata_path().exists():
        with _lock:
            if _state != STATE_DOWNLOADING:
                return Status(STATE_READY, _data_version(), None)
    with _lock:
        return Status(_state, _data_version(), _error)


def ensure_download() -> None:
    """Start a background download when the cache is empty.

    Idempotent and non-blocking. A failed attempt is not retried automatically:
    only a ComfyUI restart starts a new one, which keeps a broken endpoint from
    being hammered by the frontend's polling.
    """
    global _state, _error, _download_thread
    with _lock:
        ready = artifact_path().exists() and metadata_path().exists()
        if ready:
            _state = STATE_READY
            return
        if _download_thread is not None and _download_thread.is_alive():
            return
        if _state in (STATE_DOWNLOADING, STATE_ERROR):
            return
        _state = STATE_DOWNLOADING
        _error = None
        _download_thread = threading.Thread(target=_download, name="dta-download", daemon=True)
        _download_thread.start()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _download() -> None:
    global _state, _error
    try:
        pointer_response = requests.get(_latest_url(), timeout=30)
        pointer_response.raise_for_status()
        pointer = pointer_response.json()

        artifact_response = requests.get(pointer["url"], timeout=300)
        artifact_response.raise_for_status()
        payload = artifact_response.content

        expected = pointer.get("sha256")
        digest = hashlib.sha256(payload).hexdigest()
        if expected and digest != expected:
            raise ValueError(f"artifact sha256 mismatch (expected {expected}, got {digest})")

        metadata_response = requests.get(
            pointer["url"].rsplit("/", 1)[0] + "/" + METADATA_FILENAME, timeout=60
        )
        metadata_response.raise_for_status()
        metadata_response.json()

        _write_atomic(artifact_path(), payload)
        _write_atomic(metadata_path(), metadata_response.content)
    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
        log.warning("danbooru-tag-autocomplete: artifact download failed: %s", exc)
        with _lock:
            _state = STATE_ERROR
            _error = str(exc)
        return
    with _lock:
        _state = STATE_READY
        _error = None


def _read_artifact_bytes(path: Path) -> bytes:
    payload = path.read_bytes()
    if payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload)
    return payload


def load_artifact() -> Artifact:
    """Decode the artifact, cached until the file it came from changes."""
    global _artifact_cache
    path = artifact_path()
    stat = path.stat()
    identity = (path, stat.st_mtime_ns, stat.st_size)
    if _artifact_cache is not None and _artifact_cache[0] == identity:
        return _artifact_cache[1]
    artifact = decode(_read_artifact_bytes(path))
    _artifact_cache = (identity, artifact)
    return artifact


def load_custom() -> tuple[Artifact | None, tuple[str, ...]]:
    """Build the custom overlay for the user's custom file, cached by file identity."""
    global _custom_cache
    path = custom_file_path()
    if path is None:
        return None, ()
    stat = path.stat()
    identity = (path, stat.st_mtime_ns)
    if _custom_cache is not None and _custom_cache[0] == identity:
        return _custom_cache[1]
    try:
        result = build_custom_overlay(load_artifact(), path.read_text(encoding="utf-8"), path.name)
    except (OSError, ValueError) as exc:
        log.warning("danbooru-tag-autocomplete: %s could not be used: %s", path.name, exc)
        result = (None, (f"{path.name}: {exc}",))
    _custom_cache = (identity, result)
    return result


def load_index() -> TagIndex:
    """The index the node searches: the main artifact plus the custom overlay."""
    return TagIndex(load_artifact(), custom=load_custom()[0])


def artifact_content_encoding() -> str | None:
    path = artifact_path()
    if not path.exists():
        return None
    with open(path, "rb") as handle:
        return "gzip" if handle.read(2) == b"\x1f\x8b" else None


def custom_payload() -> dict:
    """JSON-safe custom overlay for the browser."""
    overlay, warnings = load_custom()
    return {
        "available": overlay is not None,
        "warnings": list(warnings),
        "bytes": base64.b64encode(overlay.to_bytes()).decode("ascii") if overlay is not None else None,
    }
