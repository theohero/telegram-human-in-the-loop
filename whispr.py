"""
Whispr — Optional voice-message transcription for HITL MCP Server.

When enabled, Telegram voice messages are automatically transcribed using
faster-whisper (CTranslate2) and presented to the user for confirmation
before being sent to the agent.

This module is entirely self-contained.  If ``faster-whisper`` is not
installed, :func:`is_available` returns ``False`` and no transcription
happens — the HITL server continues to work normally for text messages.

Configuration is stored in ``~/.hitl-mcp/whispr_config.json``.

Environment variables (override config file):
    HITL_WHISPR_ENABLED   – "1" / "true" / "yes" to enable  (default: off)
    HITL_WHISPR_MODEL     – faster-whisper model name        (default: "base")
    HITL_WHISPR_LANGUAGE  – ISO-639-1 language code or ""    (default: "" = auto)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".hitl-mcp")
CONFIG_FILE = os.path.join(CONFIG_DIR, "whispr_config.json")
MODEL_CACHE = os.path.join(CONFIG_DIR, "whispr_models")

# ── Defaults ───────────────────────────────────────────────────────────────

DEFAULT_MODEL = "base"
DEFAULT_LANGUAGE = ""          # empty → auto-detect
DEFAULT_ENABLED = False

# ── Config ─────────────────────────────────────────────────────────────────


class WhisprConfig:
    """Read/write Whispr configuration."""

    def __init__(self) -> None:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self._data = self._load()

    # — Persistence —

    def _load(self) -> Dict[str, Any]:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    # — Accessors (env vars override file) —

    @property
    def enabled(self) -> bool:
        env = os.getenv("HITL_WHISPR_ENABLED", "").strip().lower()
        if env in ("1", "true", "yes", "on"):
            return True
        if env in ("0", "false", "no", "off"):
            return False
        return self._data.get("enabled", DEFAULT_ENABLED)

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._data["enabled"] = value
        self._save()

    @property
    def model(self) -> str:
        return os.getenv("HITL_WHISPR_MODEL", "").strip() or self._data.get("model", DEFAULT_MODEL)

    @model.setter
    def model(self, value: str) -> None:
        self._data["model"] = value
        self._save()

    @property
    def language(self) -> str:
        env = os.getenv("HITL_WHISPR_LANGUAGE", "").strip()
        if env:
            return env
        return self._data.get("language", DEFAULT_LANGUAGE)

    @language.setter
    def language(self, value: str) -> None:
        self._data["language"] = value
        self._save()


# ── Singleton config ───────────────────────────────────────────────────────

_config: Optional[WhisprConfig] = None


def get_config() -> WhisprConfig:
    """Return the global WhisprConfig (created on first call)."""
    global _config
    if _config is None:
        _config = WhisprConfig()
    return _config


# ── Availability check ─────────────────────────────────────────────────────


def is_available() -> bool:
    """Return True if faster-whisper is importable."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def is_enabled() -> bool:
    """Return True if Whispr is both available and enabled in config."""
    return is_available() and get_config().enabled


# ── Transcriber ────────────────────────────────────────────────────────────


class WhisprTranscriber:
    """Lazy-loading faster-whisper transcriber.

    The model is loaded on the first call to :meth:`transcribe` and stays
    in memory for subsequent calls.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._cfg = get_config()

    def _ensure_model(self) -> None:
        """Load the model if not already loaded."""
        if self._model is not None:
            return

        # DLL fix for Windows (same pattern as Oozr Dictation)
        if sys.platform == "win32":
            for d in _get_cuda_dll_dirs():
                try:
                    os.add_dll_directory(d)
                except (OSError, AttributeError):
                    pass

        from faster_whisper import WhisperModel  # noqa: WPS433

        model_name = self._cfg.model
        os.makedirs(MODEL_CACHE, exist_ok=True)

        # Try CUDA first, fall back to CPU
        for device, ct in [("cuda", "int8_float16"), ("cpu", "int8")]:
            try:
                logger.info("Whispr: loading model '%s' on %s …", model_name, device)
                t0 = time.perf_counter()
                self._model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=ct,
                    download_root=MODEL_CACHE,
                )
                elapsed = (time.perf_counter() - t0) * 1000
                logger.info("Whispr: model loaded in %.0f ms (device=%s)", elapsed, device)
                return
            except Exception as exc:
                logger.warning("Whispr: failed to load on %s: %s", device, exc)
                if device == "cpu":
                    raise

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> str:
        """Transcribe an audio file and return the text.

        Args:
            audio_path: Path to an audio file (ogg, wav, mp3, etc.).
            language: ISO-639-1 code, or None for auto-detect.

        Returns:
            Transcribed text as a single string.
        """
        self._ensure_model()

        lang = language or self._cfg.language or None
        kwargs: Dict[str, Any] = {}
        if lang:
            kwargs["language"] = lang

        segments, info = self._model.transcribe(audio_path, **kwargs)
        text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]

        result = " ".join(text_parts)
        logger.info(
            "Whispr: transcribed %.1f s audio → %d chars (lang=%s)",
            info.duration,
            len(result),
            info.language,
        )
        return result

    def unload(self) -> None:
        """Free model memory."""
        if self._model is not None:
            del self._model
            self._model = None


# ── Telegram voice download ────────────────────────────────────────────────


def download_telegram_voice(file_id: str, bot_token: str) -> str:
    """Download a Telegram voice message and return the local file path.

    The file is saved to a temporary directory and the caller should
    delete it when done.
    """
    # Step 1: getFile → file_path
    url = f"https://api.telegram.org/bot{bot_token}/getFile"
    data = json.dumps({"file_id": file_id}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram getFile failed: {result}")

    file_path = result["result"]["file_path"]

    # Step 2: download the file
    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

    # Determine extension from the file_path
    ext = Path(file_path).suffix or ".ogg"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir=tempfile.gettempdir())
    tmp_path = tmp.name
    tmp.close()

    urllib.request.urlretrieve(download_url, tmp_path)
    logger.info("Whispr: downloaded voice file → %s (%.1f KB)", tmp_path, os.path.getsize(tmp_path) / 1024)
    return tmp_path


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_cuda_dll_dirs() -> list[str]:
    """Collect CUDA DLL directories for os.add_dll_directory on Windows."""
    import importlib.util
    dirs: list[str] = []

    for pkg in ("nvidia.cublas", "nvidia.cuda_runtime", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(pkg)
            if spec and spec.submodule_search_locations:
                bin_dir = os.path.join(list(spec.submodule_search_locations)[0], "bin")
                if os.path.isdir(bin_dir):
                    dirs.append(bin_dir)
        except Exception:
            pass

    torch_lib = os.path.join(sys.prefix, "Lib", "site-packages", "torch", "lib")
    if os.path.isdir(torch_lib):
        dirs.append(torch_lib)

    return dirs


# ── Singleton transcriber ──────────────────────────────────────────────────

_transcriber: Optional[WhisprTranscriber] = None


def get_transcriber() -> WhisprTranscriber:
    """Return the global WhisprTranscriber (created on first call)."""
    global _transcriber
    if _transcriber is None:
        _transcriber = WhisprTranscriber()
    return _transcriber
