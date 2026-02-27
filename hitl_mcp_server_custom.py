#!/usr/bin/env python3
"""
Human-in-the-Loop MCP Server

This server provides tools for getting human input and choices through GUI dialogs.
It enables LLMs to pause and ask for human feedback, input, or decisions.
Now supports both Windows and macOS platforms.
"""

import asyncio
import json
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import List, Dict, Any, Optional, Literal
import sys
import os
import time
import atexit
import re
import urllib.request
import urllib.error
import base64
from pydantic import Field
from typing import Annotated
# Set required environment variable for FastMCP 2.8.1+
os.environ.setdefault('FASTMCP_LOG_LEVEL', 'INFO')

# ensure stdout uses utf-8 to avoid encoding errors on Windows consoles
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')
except Exception:
    pass

from fastmcp import FastMCP, Context
from fastmcp.utilities.types import Image as FastMCPImage
from mcp.types import ImageContent, TextContent

# Whispr — optional voice-message transcription
try:
    from whispr import (
        is_available as whispr_is_available,
        is_enabled as whispr_is_enabled,
        get_config as whispr_get_config,
        get_transcriber as whispr_get_transcriber,
        download_telegram_voice as whispr_download_voice,
    )
    _WHISPR_IMPORTED = True
except ImportError:
    _WHISPR_IMPORTED = False

    def whispr_is_available():
        return False

    def whispr_is_enabled():
        return False

# Platform detection
CURRENT_PLATFORM = platform.system().lower()
IS_WINDOWS = CURRENT_PLATFORM == 'windows'
IS_MACOS = CURRENT_PLATFORM == 'darwin'
IS_LINUX = CURRENT_PLATFORM == 'linux'

# Initialize the MCP server
mcp = FastMCP("Human-in-the-Loop Server")

# Global variable to ensure GUI is initialized properly
_gui_initialized = False
_gui_lock = threading.Lock()

_telegram_lock = threading.Lock()
_telegram_last_update_id: Optional[int] = None

# Delineator used to separate AI output from user input in multiline dialog
MULTILINE_DELINEATOR = "─" * 50

# ── Session Coordination for Multi-Instance Telegram HITL ──────────────────

class SessionCoordinator:
    """Manages multi-instance session coordination for Telegram HITL.

    When multiple VS Code windows run HITL MCP servers simultaneously,
    this coordinator:
    - Assigns each instance a unique session number (#1-#9)
    - Tags outgoing Telegram messages with session info
    - Routes incoming replies to the correct instance
    - Provides inline keyboard buttons for easy session switching
    - Handles /sessions and /r{n} commands
    """

    BASE_DIR = os.path.join(os.path.expanduser("~"), ".hitl-mcp")
    SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.json")
    OFFSET_FILE = os.path.join(BASE_DIR, "telegram_offset.json")
    RESPONSES_DIR = os.path.join(BASE_DIR, "responses")
    POLL_LOCK_FILE = os.path.join(BASE_DIR, "poll.lock")
    MESSAGE_MAP_FILE = os.path.join(BASE_DIR, "message_map.json")
    ACTIVE_CONTEXT_FILE = os.path.join(BASE_DIR, "active_context.json")

    SESSION_ICONS = ["🔵", "🟢", "🟡", "🟠", "🔴", "🟣", "⚪", "🟤", "⚫"]

    def __init__(self):
        self.session_id: Optional[str] = None
        self.session_number: Optional[int] = None
        self.workspace_name: Optional[str] = None
        self.pid = os.getpid()
        self._lock_fd = None
        self._is_poller = False
        self._registered = False
        os.makedirs(self.BASE_DIR, exist_ok=True)
        os.makedirs(self.RESPONSES_DIR, exist_ok=True)

    # ── Helpers ──

    def _get_workspace_name(self) -> str:
        """Detect a human-readable workspace name for this session.

        Priority:
        1. HITL_SESSION_NAME env var  (set "${workspaceFolderBasename}" in mcp.json)
        2. VSCODE_CWD / VSCODE_WORKSPACE_FOLDER env vars
        3. Script's parent directory name
        4. CWD (skip VS Code install dir)
        5. Fallback to PID-based name
        """
        # 1. Explicit env var — works perfectly with VS Code variable substitution
        name = os.getenv("HITL_SESSION_NAME", "").strip()
        if name:
            return name

        # 2. VS Code env vars (prefer WORKSPACE_FOLDER, then CWD)
        _vscode_skip = {"vs code", "code", "microsoft vs code"}
        for var in ("VSCODE_WORKSPACE_FOLDER", "WORKSPACE_FOLDER", "VSCODE_CWD"):
            val = os.getenv(var, "").strip()
            if val and os.path.isdir(val):
                basename = os.path.basename(val)
                if basename.lower() not in _vscode_skip and "vs code" not in val.lower():
                    return basename

        # 3. Script's parent directory (skip generic dirs + the MCP folder itself)
        if sys.argv:
            script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            dirname = os.path.basename(script_dir)
            skip = {"scripts", "bin", "__pycache__", "site-packages", ".tmp",
                    "hitl-mcp-server", "mcp"}
            if dirname and dirname.lower() not in skip:
                return dirname

        # 4. CWD (skip common VS Code install dirs)
        cwd_name = os.path.basename(os.getcwd())
        if cwd_name and "vs code" not in cwd_name.lower() and cwd_name.lower() not in _vscode_skip:
            return cwd_name

        return f"Session-{os.getpid()}"

    def _json_read(self, filepath: str) -> Any:
        try:
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            pass
        return None

    def _json_write(self, filepath: str, data: Any):
        tmp = filepath + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, filepath)
        except OSError:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

    def _is_pid_alive(self, pid: int) -> bool:
        if IS_WINDOWS:
            try:
                import ctypes
                SYNCHRONIZE = 0x00100000
                handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                return False
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    # ── Session registry ──

    def _read_sessions(self) -> Dict[str, Any]:
        data = self._json_read(self.SESSIONS_FILE)
        return data if isinstance(data, dict) and "sessions" in data else {"sessions": {}}

    def _write_sessions(self, data: Dict[str, Any]):
        self._json_write(self.SESSIONS_FILE, data)

    def _cleanup_stale(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sessions = data.get("sessions", {})
        data["sessions"] = {
            sid: info for sid, info in sessions.items()
            if self._is_pid_alive(info.get("pid", 0))
        }
        return data

    def register(self) -> int:
        """Register this MCP instance. Returns session number (1-9)."""
        self.workspace_name = self._get_workspace_name()
        data = self._read_sessions()
        data = self._cleanup_stale(data)

        used = {info["number"] for info in data["sessions"].values()}
        for n in range(1, 10):
            if n not in used:
                self.session_number = n
                break
        else:
            self.session_number = len(data["sessions"]) + 1

        self.session_id = f"s{self.session_number}_{self.pid}"
        data["sessions"][self.session_id] = {
            "number": self.session_number,
            "workspace": self.workspace_name,
            "pid": self.pid,
            "started": time.time(),
        }
        self._write_sessions(data)
        self._registered = True
        return self.session_number

    def deregister(self):
        """Remove this session on exit."""
        if not self._registered or not self.session_id:
            return
        try:
            data = self._read_sessions()
            data["sessions"].pop(self.session_id, None)
            self._write_sessions(data)
        except Exception:
            pass
        try:
            resp = os.path.join(self.RESPONSES_DIR, f"{self.session_id}.json")
            if os.path.exists(resp):
                os.remove(resp)
        except Exception:
            pass
        self.release_poll_lock()
        self._registered = False

    def get_active_sessions(self) -> List[Dict]:
        data = self._read_sessions()
        data = self._cleanup_stale(data)
        self._write_sessions(data)
        result = []
        for sid, info in data["sessions"].items():
            n = info["number"]
            result.append({
                "session_id": sid,
                "number": n,
                "workspace": info["workspace"],
                "pid": info["pid"],
                "icon": self.SESSION_ICONS[n - 1] if n <= len(self.SESSION_ICONS) else "🔷",
            })
        return sorted(result, key=lambda s: s["number"])

    def session_id_by_number(self, number: int) -> Optional[str]:
        for s in self.get_active_sessions():
            if s["number"] == number:
                return s["session_id"]
        return None

    # ── Formatting ──

    def format_tag(self) -> str:
        n = self.session_number or 0
        icon = self.SESSION_ICONS[n - 1] if 0 < n <= len(self.SESSION_ICONS) else "🔷"
        return f"{icon} #{n} · {self.workspace_name}"

    def build_inline_keyboard(self) -> List[List[Dict]]:
        sessions = self.get_active_sessions()
        if len(sessions) <= 1:
            return []
        buttons: List[List[Dict]] = []
        row: List[Dict] = []
        for s in sessions:
            row.append({
                "text": f"{s['icon']} #{s['number']} {s['workspace']}",
                "callback_data": f"ses:{s['session_id']}",
            })
            if len(row) >= 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        return buttons

    # ── Shared Telegram offset ──

    def get_shared_offset(self) -> int:
        data = self._json_read(self.OFFSET_FILE)
        return data.get("offset", 0) if isinstance(data, dict) else 0

    def set_shared_offset(self, offset: int):
        self._json_write(self.OFFSET_FILE, {"offset": offset})

    def init_shared_offset_if_needed(self):
        """Drain old Telegram updates so they don't interfere."""
        if self.get_shared_offset() > 0:
            return
        try:
            updates = _telegram_api_call("getUpdates", {"timeout": 0, "limit": 100}, timeout=10)
            result = updates.get("result", [])
            if result:
                self.set_shared_offset(max(item.get("update_id", 0) for item in result))
            else:
                self.set_shared_offset(0)
        except Exception:
            self.set_shared_offset(0)

    # ── Message map (message_id → session_id) ──

    def record_message(self, message_id: int):
        data = self._json_read(self.MESSAGE_MAP_FILE) or {}
        data[str(message_id)] = self.session_id
        if len(data) > 200:
            items = sorted(data.items(), key=lambda x: int(x[0]))
            data = dict(items[-200:])
        self._json_write(self.MESSAGE_MAP_FILE, data)

    def lookup_message(self, message_id: int) -> Optional[str]:
        data = self._json_read(self.MESSAGE_MAP_FILE) or {}
        return data.get(str(message_id))

    # ── Response file I/O ──

    def write_response(self, target_session_id: str, text: str):
        path = os.path.join(self.RESPONSES_DIR, f"{target_session_id}.json")
        self._json_write(path, {"text": text, "ts": time.time()})

    def read_response(self) -> Optional[str]:
        if not self.session_id:
            return None
        path = os.path.join(self.RESPONSES_DIR, f"{self.session_id}.json")
        try:
            if os.path.exists(path):
                data = self._json_read(path)
                os.remove(path)
                return data.get("text") if isinstance(data, dict) else None
        except Exception:
            pass
        return None

    # ── Polling lock (only one instance polls Telegram) ──

    def try_acquire_poll_lock(self) -> bool:
        try:
            self._lock_fd = open(self.POLL_LOCK_FILE, "w")
            if IS_WINDOWS:
                import msvcrt
                msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._is_poller = True
            return True
        except Exception:
            if self._lock_fd:
                try:
                    self._lock_fd.close()
                except Exception:
                    pass
                self._lock_fd = None
            self._is_poller = False
            return False

    def release_poll_lock(self):
        if self._lock_fd:
            try:
                if IS_WINDOWS:
                    import msvcrt
                    msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
            self._lock_fd = None
        self._is_poller = False

    # ── Active context (tracks last button-tap session) ──

    def set_active_context(self, session_id: str):
        self._json_write(self.ACTIVE_CONTEXT_FILE, {"session_id": session_id, "ts": time.time()})

    def get_and_clear_active_context(self) -> Optional[str]:
        try:
            if os.path.exists(self.ACTIVE_CONTEXT_FILE):
                data = self._json_read(self.ACTIVE_CONTEXT_FILE)
                os.remove(self.ACTIVE_CONTEXT_FILE)
                if isinstance(data, dict) and time.time() - data.get("ts", 0) < 300:
                    return data.get("session_id")
        except Exception:
            pass
        return None


# Global session coordinator — initialised in main()
_session_coordinator: Optional[SessionCoordinator] = None

def is_telegram_enabled() -> bool:
    """Check whether Telegram transport is configured via environment variables."""
    return bool(os.getenv("HITL_TELEGRAM_BOT_TOKEN") and os.getenv("HITL_TELEGRAM_CHAT_ID"))

def _telegram_chat_id_matches(incoming_chat_id: Any) -> bool:
    configured_chat_id = os.getenv("HITL_TELEGRAM_CHAT_ID", "").strip()
    return str(incoming_chat_id) == configured_chat_id

def _telegram_api_call(method: str, payload: Dict[str, Any], timeout: int = 35) -> Dict[str, Any]:
    token = os.getenv("HITL_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HITL_TELEGRAM_BOT_TOKEN is not set")

    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        parsed = json.loads(body)
    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {parsed}")
    return parsed

def _telegram_init_offset() -> None:
    """Initialize update offset so old messages are ignored on first use."""
    global _telegram_last_update_id
    with _telegram_lock:
        if _telegram_last_update_id is not None:
            return
        try:
            updates = _telegram_api_call("getUpdates", {"timeout": 0, "limit": 100}, timeout=10)
            result = updates.get("result", [])
            if result:
                _telegram_last_update_id = max(item.get("update_id", 0) for item in result)
            else:
                _telegram_last_update_id = 0
        except Exception:
            _telegram_last_update_id = 0


# ── Telegram Photo Download ─────────────────────────────────────────────────

def _telegram_download_photo(file_id: str) -> tuple:
    """Download a photo from Telegram by file_id.

    Returns (image_bytes, mime_type).
    """
    token = os.getenv("HITL_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HITL_TELEGRAM_BOT_TOKEN is not set")
    # Step 1: getFile to obtain the file_path on Telegram's server
    result = _telegram_api_call("getFile", {"file_id": file_id})
    file_path = result["result"]["file_path"]  # e.g. "photos/file_123.jpg"
    # Step 2: Download the actual file bytes
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    # Determine MIME type from file extension
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }
    mime_type = mime_map.get(ext, "image/jpeg")
    return data, mime_type


# ── Whispr: voice-message confirmation flow ────────────────────────────────

def _whispr_handle_voice_message(msg: Dict[str, Any], chat_id: str) -> Optional[str]:
    """Download, transcribe, and run confirmation loop for a voice message.

    Returns the final approved text, or None if the user cancels.
    This function blocks the current thread (called from the polling loop).
    """
    if not _WHISPR_IMPORTED or not whispr_is_enabled():
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": "🎙 Voice messages received but Whispr is not enabled.\n"
                    "Enable it with /whispr on",
        }, timeout=10)
        return None

    voice = msg.get("voice") or msg.get("audio")
    if not voice:
        return None

    file_id = voice.get("file_id")
    duration = voice.get("duration", 0)
    if not file_id:
        return None

    bot_token = os.getenv("HITL_TELEGRAM_BOT_TOKEN", "").strip()

    # Notify user we're processing
    _telegram_api_call("sendMessage", {
        "chat_id": chat_id,
        "text": f"🎙 Transcribing voice message ({duration}s)…",
    }, timeout=10)

    # Download and transcribe
    try:
        audio_path = whispr_download_voice(file_id, bot_token)
        try:
            transcriber = whispr_get_transcriber()
            transcribed_text = transcriber.transcribe(audio_path)
        finally:
            # Clean up temp file
            try:
                os.unlink(audio_path)
            except Exception:
                pass
    except Exception as exc:
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": f"❌ Whispr transcription failed: {exc}",
        }, timeout=10)
        return None

    if not transcribed_text.strip():
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": "🎙 Transcription was empty — no speech detected.",
        }, timeout=10)
        return None

    # ── Confirmation loop ──
    current_text = transcribed_text.strip()
    original_text = current_text
    edit_history: list[str] = []

    while True:
        # Send confirmation message with buttons
        confirm_msg = (
            f"🎙 **Transcribed text:**\n\n"
            f"{current_text}\n\n"
            f"─────────────────────\n"
            f"Is this correct?"
        )
        keyboard = [
            [
                {"text": "✅ Yes, proceed", "callback_data": "whispr:approve"},
                {"text": "✏️ Edit", "callback_data": "whispr:edit"},
            ],
            [
                {"text": "❌ Cancel", "callback_data": "whispr:cancel"},
            ],
        ]
        confirm_payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": confirm_msg,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps({"inline_keyboard": keyboard}),
        }
        try:
            _telegram_api_call("sendMessage", confirm_payload, timeout=15)
        except Exception:
            # Markdown might fail if text has special chars, retry without parse_mode
            confirm_payload.pop("parse_mode", None)
            confirm_payload["text"] = (
                f"🎙 Transcribed text:\n\n"
                f"{current_text}\n\n"
                f"─────────────────────\n"
                f"Is this correct?"
            )
            _telegram_api_call("sendMessage", confirm_payload, timeout=15)

        # Wait for user response (button tap or text message)
        response = _whispr_wait_for_response(chat_id)
        if response is None:
            return None  # timeout

        if response == "__WHISPR_APPROVE__":
            # User approved — return the text
            # Send confirmation
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": "✅ Transcription approved. Sending to agent…",
            }, timeout=10)
            return json.dumps({
                "__whispr__": True,
                "text": current_text,
                "original": original_text,
                "edits": edit_history,
            })

        elif response == "__WHISPR_CANCEL__":
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": "❌ Voice message cancelled.",
            }, timeout=10)
            return None

        elif response == "__WHISPR_EDIT__":
            # Ask for corrections
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "✏️ Send your corrections:\n\n"
                    "• Type the full corrected text, OR\n"
                    "• Describe what to change (e.g. \"change 'debug thing' to 'debugging step'\")"
                ),
            }, timeout=10)
            # Wait for the correction text
            correction = _whispr_wait_for_response(chat_id)
            if correction is None:
                return None
            if correction.startswith("__WHISPR_"):
                continue  # they tapped a button instead of typing, re-show

            edit_history.append(correction)

            # If the correction is long enough to be a full replacement, use it directly
            if len(correction) > len(current_text) * 0.5:
                current_text = correction.strip()
            else:
                # Short correction — treat as natural language edit instruction
                # We can't run LLM here, so append the correction as a note
                # and the agent will interpret it
                current_text = f"{current_text}\n\n[User correction: {correction}]"

            # Loop back to confirmation
            continue

        else:
            # User sent raw text without pressing a button — treat as correction
            edit_history.append(response)
            current_text = response.strip()
            # Loop back to confirmation
            continue


def _whispr_wait_for_response(chat_id: str, timeout_seconds: int = 300) -> Optional[str]:
    """Wait for a user response (callback or text) in the Whispr flow.

    Returns:
        - "__WHISPR_APPROVE__" if user tapped ✅
        - "__WHISPR_EDIT__" if user tapped ✏️
        - "__WHISPR_CANCEL__" if user tapped ❌
        - The text content if user sent a text message
        - None on timeout
    """
    global _telegram_last_update_id
    start = time.time()

    while time.time() - start < timeout_seconds:
        try:
            with _telegram_lock:
                offset = (_telegram_last_update_id or 0) + 1
            updates = _telegram_api_call(
                "getUpdates",
                {"offset": offset, "timeout": 15, "allowed_updates": ["message", "callback_query"]},
                timeout=25,
            )
        except Exception:
            time.sleep(1)
            continue

        for item in updates.get("result", []):
            update_id = item.get("update_id", 0)
            with _telegram_lock:
                _telegram_last_update_id = max(_telegram_last_update_id or 0, update_id)

            # Check callback queries (button taps)
            cb = item.get("callback_query")
            if cb:
                cb_data = cb.get("data", "")
                cb_id = cb.get("id")
                if cb_data == "whispr:approve":
                    try:
                        _telegram_api_call("answerCallbackQuery", {
                            "callback_query_id": cb_id, "text": "✅ Approved!", "show_alert": False,
                        }, timeout=10)
                    except Exception:
                        pass
                    return "__WHISPR_APPROVE__"
                elif cb_data == "whispr:edit":
                    try:
                        _telegram_api_call("answerCallbackQuery", {
                            "callback_query_id": cb_id, "text": "✏️ Send your corrections", "show_alert": False,
                        }, timeout=10)
                    except Exception:
                        pass
                    return "__WHISPR_EDIT__"
                elif cb_data == "whispr:cancel":
                    try:
                        _telegram_api_call("answerCallbackQuery", {
                            "callback_query_id": cb_id, "text": "❌ Cancelled", "show_alert": False,
                        }, timeout=10)
                    except Exception:
                        pass
                    return "__WHISPR_CANCEL__"
                # Other callback (session switching etc.) — ignore in whispr flow
                continue

            # Check text messages
            msg = item.get("message")
            if msg and _telegram_chat_id_matches(msg.get("chat", {}).get("id")):
                text = msg.get("text")
                if text:
                    return text

    return None  # timeout


# ── Whispr command handlers ────────────────────────────────────────────────

def _handle_whispr_command(text: str, chat_id: str) -> None:
    """Handle /whispr commands (on, off, status, model)."""
    parts = text.split(maxsplit=1)
    subcmd = parts[1].strip().lower() if len(parts) > 1 else "status"

    if not _WHISPR_IMPORTED:
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": (
                "🎙 **Whispr module not found.**\n\n"
                "Install faster-whisper to enable voice transcription:\n"
                "`pip install faster-whisper`"
            ),
            "parse_mode": "Markdown",
        }, timeout=10)
        return

    cfg = whispr_get_config()

    if subcmd in ("on", "enable"):
        if not whispr_is_available():
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "⚠️ faster-whisper is not installed.\n"
                    "Run: `pip install faster-whisper`\n"
                    "Then restart the MCP server."
                ),
                "parse_mode": "Markdown",
            }, timeout=10)
            return
        cfg.enabled = True
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"✅ Whispr enabled!\n\n"
                f"Model: {cfg.model}\n"
                f"Language: {cfg.language or 'auto-detect'}\n\n"
                f"Send a voice message to try it out."
            ),
        }, timeout=10)

    elif subcmd in ("off", "disable"):
        cfg.enabled = False
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": "🔇 Whispr disabled. Voice messages will be ignored.",
        }, timeout=10)

    elif subcmd.startswith("model"):
        model_parts = subcmd.split(maxsplit=1)
        if len(model_parts) > 1:
            new_model = model_parts[1].strip()
            cfg.model = new_model
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": f"✅ Whispr model set to: {new_model}\n(Will load on next transcription)",
            }, timeout=10)
        else:
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    f"Current model: {cfg.model}\n\n"
                    "Available: tiny, base, small, medium, large-v3\n"
                    "Usage: /whispr model small"
                ),
            }, timeout=10)

    elif subcmd.startswith("lang"):
        lang_parts = subcmd.split(maxsplit=1)
        if len(lang_parts) > 1:
            new_lang = lang_parts[1].strip()
            if new_lang == "auto":
                new_lang = ""
            cfg.language = new_lang
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": f"✅ Whispr language set to: {new_lang or 'auto-detect'}",
            }, timeout=10)
        else:
            _telegram_api_call("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    f"Current language: {cfg.language or 'auto-detect'}\n\n"
                    "Usage: /whispr lang en  (or ru, de, fr, auto)"
                ),
            }, timeout=10)

    else:
        # Status
        available = whispr_is_available()
        enabled = cfg.enabled
        _telegram_api_call("sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"🎙 **Whispr Status**\n\n"
                f"Available: {'✅ Yes' if available else '❌ No (install faster-whisper)'}\n"
                f"Enabled: {'✅ On' if enabled else '🔇 Off'}\n"
                f"Model: {cfg.model}\n"
                f"Language: {cfg.language or 'auto-detect'}\n\n"
                f"Commands:\n"
                f"/whispr on — Enable transcription\n"
                f"/whispr off — Disable transcription\n"
                f"/whispr model <name> — Change model\n"
                f"/whispr lang <code> — Set language"
            ),
            "parse_mode": "Markdown",
        }, timeout=10)


def _handle_help_command(chat_id: str) -> None:
    """Handle /help command with full command list including Whispr."""
    whispr_status = ""
    if _WHISPR_IMPORTED:
        cfg = whispr_get_config()
        status = "✅ ON" if cfg.enabled else "🔇 OFF"
        whispr_status = f"\n\n🎙 **Whispr** (voice transcription): {status}"

    _telegram_api_call("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🤖 **HITL MCP Server — Commands**\n\n"
            "📋 /sessions — List active agent sessions\n"
            "📝 /r{n} — Reply to session #n\n"
            f"🎙 /whispr — Voice transcription settings\n"
            f"  /whispr on — Enable Whispr\n"
            f"  /whispr off — Disable Whispr\n"
            f"  /whispr model <name> — Set model (tiny/base/small/medium/large-v3)\n"
            f"  /whispr lang <code> — Set language (en/ru/auto)\n"
            "📷 **Images** — Send a photo or image file and it will be forwarded to the AI model\n"
            "❓ /help — Show this help"
            f"{whispr_status}"
        ),
        "parse_mode": "Markdown",
    }, timeout=10)


def _send_and_wait_telegram_multiline_input(
    title: str,
    prompt: str,
    default_value: str = "",
    timeout_seconds: int = 1800,
) -> Optional[str]:
    """Send prompt to Telegram with session tagging and wait for a reply.

    Supports multi-instance coordination:
    - Only ONE instance polls Telegram at a time (file-lock).
    - Other instances wait for a response file written by the poller.
    - Incoming messages are routed by:
        1. reply_to_message_id → message-map lookup
        2. /r{n} command → session number
        3. Active context (button tap) → last-tapped session
        4. Single-session fallback
        5. Ambiguous → disambiguation prompt
    """
    global _telegram_last_update_id, _session_coordinator
    coord = _session_coordinator

    chat_id = os.getenv("HITL_TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("HITL_TELEGRAM_CHAT_ID is not set")

    _telegram_init_offset()

    # ── Build tagged message ──
    tag = coord.format_tag() if coord and coord.session_id else ""
    header = f"🧠 {tag}" if tag else f"🧠 {title}"
    message_lines = [
        header, "",
        prompt, "",
        MULTILINE_DELINEATOR,
        "Reply to this message or tap a button below.",
    ]
    if default_value:
        message_lines.extend(["", "Default value:", default_value])

    # ── Send with inline keyboard ──
    keyboard = coord.build_inline_keyboard() if coord else []
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": "\n".join(message_lines)}
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})

    sent = _telegram_api_call("sendMessage", payload, timeout=20)
    sent_message_id = sent.get("result", {}).get("message_id")

    if coord and sent_message_id:
        coord.record_message(sent_message_id)

    # ── Init shared offset for poller ──
    if coord:
        coord.init_shared_offset_if_needed()

    start_time = time.time()
    is_poller = coord.try_acquire_poll_lock() if coord else True

    try:
        while True:
            if time.time() - start_time > timeout_seconds:
                return None

            # ────── POLLER PATH ──────
            if is_poller or not coord:
                if coord:
                    offset = coord.get_shared_offset() + 1
                else:
                    with _telegram_lock:
                        offset = (_telegram_last_update_id or 0) + 1

                try:
                    updates = _telegram_api_call(
                        "getUpdates",
                        {
                            "offset": offset,
                            "timeout": 25,
                            "allowed_updates": ["message", "edited_message", "callback_query"],
                        },
                        timeout=35,
                    )
                except Exception:
                    time.sleep(2)
                    continue

                for item in updates.get("result", []):
                    update_id = item.get("update_id", 0)
                    if coord:
                        coord.set_shared_offset(update_id)
                    else:
                        with _telegram_lock:
                            _telegram_last_update_id = max(_telegram_last_update_id or 0, update_id)

                    # ── Callback query (inline-button tap) ──
                    cb = item.get("callback_query")
                    if cb:
                        cb_data = cb.get("data", "")
                        cb_id = cb.get("id")
                        if cb_data.startswith("ses:") and coord:
                            target_sid = cb_data[4:]
                            coord.set_active_context(target_sid)
                            sessions = coord.get_active_sessions()
                            tgt = next((s for s in sessions if s["session_id"] == target_sid), None)
                            ans = (
                                f"📝 Now replying to #{tgt['number']} · {tgt['workspace']}. Send your message:"
                                if tgt else "Send your message:"
                            )
                            try:
                                _telegram_api_call("answerCallbackQuery", {
                                    "callback_query_id": cb_id, "text": ans, "show_alert": False,
                                }, timeout=10)
                                _telegram_api_call("sendMessage", {"chat_id": chat_id, "text": ans}, timeout=10)
                            except Exception:
                                pass
                        continue

                    # ── Text message ──
                    msg = item.get("message") or item.get("edited_message")
                    if not msg:
                        continue
                    if not _telegram_chat_id_matches(msg.get("chat", {}).get("id")):
                        continue

                    # ── Voice message (Whispr) ──
                    if msg.get("voice") or msg.get("audio"):
                        if whispr_is_enabled():
                            whispr_result = _whispr_handle_voice_message(msg, chat_id)
                            if whispr_result is not None:
                                # Route the transcribed text like a normal message
                                text = whispr_result
                                # Fall through to normal routing below
                            else:
                                continue  # cancelled or failed
                        else:
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": "🎙 Voice message received. Enable Whispr to auto-transcribe: /whispr on",
                            }, timeout=10)
                            continue

                    # ── Photo message ──
                    elif msg.get("photo"):
                        try:
                            # Telegram sends array of PhotoSize; last = largest
                            photo_sizes = msg["photo"]
                            best_photo = photo_sizes[-1]
                            file_id = best_photo["file_id"]
                            caption = msg.get("caption", "")
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": "📷 Photo received, downloading...",
                            }, timeout=10)
                            image_bytes, mime_type = _telegram_download_photo(file_id)
                            image_b64 = base64.b64encode(image_bytes).decode("ascii")
                            # Encode as JSON payload so get_multiline_input can detect it
                            text = json.dumps({
                                "__image__": True,
                                "caption": caption,
                                "mime_type": mime_type,
                                "image_b64": image_b64,
                                "file_size": len(image_bytes),
                                "width": best_photo.get("width"),
                                "height": best_photo.get("height"),
                            })
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"✅ Photo downloaded ({len(image_bytes)} bytes, {best_photo.get('width')}x{best_photo.get('height')}). Forwarding to model...",
                            }, timeout=10)
                            # Fall through to normal routing below
                        except Exception as photo_err:
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"❌ Failed to download photo: {photo_err}",
                            }, timeout=10)
                            continue

                    # ── Document (file) with image MIME ──
                    elif msg.get("document") and (msg["document"].get("mime_type", "")).startswith("image/"):
                        try:
                            doc = msg["document"]
                            file_id = doc["file_id"]
                            caption = msg.get("caption", "")
                            mime_type = doc.get("mime_type", "image/jpeg")
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": "📷 Image file received, downloading...",
                            }, timeout=10)
                            image_bytes, _ = _telegram_download_photo(file_id)
                            image_b64 = base64.b64encode(image_bytes).decode("ascii")
                            text = json.dumps({
                                "__image__": True,
                                "caption": caption,
                                "mime_type": mime_type,
                                "image_b64": image_b64,
                                "file_size": len(image_bytes),
                                "width": None,
                                "height": None,
                            })
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"✅ Image downloaded ({len(image_bytes)} bytes). Forwarding to model...",
                            }, timeout=10)
                        except Exception as doc_err:
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"❌ Failed to download image: {doc_err}",
                            }, timeout=10)
                            continue

                    else:
                        text = msg.get("text")
                        if text is None:
                            continue

                    # /whispr command
                    if text.strip().lower().startswith("/whispr"):
                        _handle_whispr_command(text.strip(), chat_id)
                        continue

                    # /help command (updated with Whispr info)
                    if text.strip().lower() == "/help":
                        _handle_help_command(chat_id)
                        continue

                    # /sessions command
                    if text.strip().lower() == "/sessions":
                        sessions = coord.get_active_sessions() if coord else []
                        if sessions:
                            lines = ["📋 Active sessions:", ""]
                            for s in sessions:
                                lines.append(f"{s['icon']} #{s['number']} · {s['workspace']} (PID {s['pid']})")
                            _telegram_api_call("sendMessage", {"chat_id": chat_id, "text": "\n".join(lines)}, timeout=10)
                        else:
                            _telegram_api_call("sendMessage", {"chat_id": chat_id, "text": "No active sessions."}, timeout=10)
                        continue

                    # /r{n} command
                    rn_match = re.match(r"^/r(\d+)\s*(.*)", text, re.DOTALL)
                    if rn_match and coord:
                        target_num = int(rn_match.group(1))
                        reply_body = rn_match.group(2).strip()
                        target_sid = coord.session_id_by_number(target_num)
                        if target_sid and reply_body:
                            if target_sid == coord.session_id:
                                return reply_body
                            coord.write_response(target_sid, reply_body)
                            continue
                        elif target_sid and not reply_body:
                            coord.set_active_context(target_sid)
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"📝 Now replying to #{target_num}. Send your message:",
                            }, timeout=10)
                            continue
                        else:
                            _telegram_api_call("sendMessage", {
                                "chat_id": chat_id,
                                "text": f"⚠️ Session #{target_num} not found.",
                            }, timeout=10)
                            continue

                    # Route by reply_to_message_id
                    reply_to_id = msg.get("reply_to_message", {}).get("message_id")
                    if reply_to_id and coord:
                        target_sid = coord.lookup_message(reply_to_id)
                        if target_sid:
                            if target_sid == coord.session_id:
                                return text
                            coord.write_response(target_sid, text)
                            continue

                    # Route by active context (button tap)
                    if coord:
                        ctx_sid = coord.get_and_clear_active_context()
                        if ctx_sid:
                            if ctx_sid == coord.session_id:
                                return text
                            coord.write_response(ctx_sid, text)
                            continue

                    # Exact reply to THIS sent message
                    if sent_message_id is not None and reply_to_id == sent_message_id:
                        return text

                    # Single session → accept directly
                    sessions = coord.get_active_sessions() if coord else []
                    if len(sessions) <= 1:
                        return text

                    # Multiple sessions, ambiguous → ask
                    lines = ["Which session should I route this to?", ""]
                    for s in sessions:
                        lines.append(f"  /r{s['number']} — {s['icon']} {s['workspace']}")
                    lines.append("\nOr tap a button below:")
                    kb = coord.build_inline_keyboard()
                    ask_payload: Dict[str, Any] = {"chat_id": chat_id, "text": "\n".join(lines)}
                    if kb:
                        ask_payload["reply_markup"] = json.dumps({"inline_keyboard": kb})
                    _telegram_api_call("sendMessage", ask_payload, timeout=10)
                    continue

            # ────── NON-POLLER PATH ──────
            else:
                resp = coord.read_response() if coord else None
                if resp is not None:
                    return resp
                # Try to become poller (in case the original poller exited)
                if coord and coord.try_acquire_poll_lock():
                    is_poller = True
                    continue
                time.sleep(0.4)
    finally:
        if coord and is_poller:
            coord.release_poll_lock()

def get_system_font():
    """Get appropriate system font for the current platform"""
    if IS_MACOS:
        return ("SF Pro Display", 13)  # macOS system font
    elif IS_WINDOWS:
        return ("Segoe UI", 10)  # Windows system font
    else:
        return ("Ubuntu", 10)  # Linux/other systems

def get_title_font():
    """Get title font for dialogs"""
    if IS_MACOS:
        return ("SF Pro Display", 16, "bold")
    elif IS_WINDOWS:
        return ("Segoe UI", 14, "bold")
    else:
        return ("Ubuntu", 14, "bold")

def get_text_font():
    """Get text font for text widgets"""
    if IS_MACOS:
        return ("Monaco", 12)  # macOS monospace font
    elif IS_WINDOWS:
        return ("Consolas", 11)  # Windows monospace font
    else:
        return ("Ubuntu Mono", 10)  # Linux monospace font

def get_theme_colors():
    """Get modern theme colors based on platform"""
    if IS_WINDOWS:
        return {
            "bg_primary": "#FFFFFF",           # Pure white background
            "bg_secondary": "#F8F9FA",         # Light gray background
            "bg_accent": "#F1F3F4",            # Accent background
            "fg_primary": "#202124",           # Dark text
            "fg_secondary": "#5F6368",         # Secondary text
            "accent_color": "#0078D4",         # Windows blue
            "accent_hover": "#106EBE",         # Darker blue for hover
            "border_color": "#E8EAED",         # Light border
            "success_color": "#137333",        # Green for success
            "error_color": "#D93025",          # Red for errors
            "selection_bg": "#E3F2FD",         # Light blue selection
            "selection_fg": "#1565C0"          # Dark blue selection text
        }
    elif IS_MACOS:
        return {
            "bg_primary": "#FFFFFF",
            "bg_secondary": "#F5F5F7",
            "bg_accent": "#F2F2F7",
            "fg_primary": "#1D1D1F",
            "fg_secondary": "#86868B",
            "accent_color": "#007AFF",
            "accent_hover": "#0056CC",
            "border_color": "#D2D2D7",
            "success_color": "#30D158",
            "error_color": "#FF3B30",
            "selection_bg": "#E3F2FD",
            "selection_fg": "#1565C0"
        }
    else:  # Linux
        return {
            "bg_primary": "#FFFFFF",
            "bg_secondary": "#F8F9FA",
            "bg_accent": "#F1F3F4",
            "fg_primary": "#202124",
            "fg_secondary": "#5F6368",
            "accent_color": "#1976D2",
            "accent_hover": "#1565C0",
            "border_color": "#E8EAED",
            "success_color": "#388E3C",
            "error_color": "#D32F2F",
            "selection_bg": "#E3F2FD",
            "selection_fg": "#1565C0"
        }

def apply_modern_style(widget, widget_type="default", theme_colors=None):
    """Apply modern styling to tkinter widgets"""
    if theme_colors is None:
        theme_colors = get_theme_colors()
    
    try:
        if widget_type == "frame":
            widget.configure(
                bg=theme_colors["bg_primary"],
                relief="flat",
                borderwidth=0
            )
        elif widget_type == "label":
            widget.configure(
                bg=theme_colors["bg_primary"],
                fg=theme_colors["fg_primary"],
                font=get_system_font(),
                anchor="w"
            )
        elif widget_type == "title_label":
            widget.configure(
                bg=theme_colors["bg_primary"],
                fg=theme_colors["fg_primary"],
                font=get_title_font(),
                anchor="w"
            )
        elif widget_type == "listbox":
            widget.configure(
                bg=theme_colors["bg_primary"],
                fg=theme_colors["fg_primary"],
                selectbackground=theme_colors["selection_bg"],
                selectforeground=theme_colors["selection_fg"],
                relief="solid",
                borderwidth=1,
                highlightthickness=1,
                highlightcolor=theme_colors["accent_color"],
                highlightbackground=theme_colors["border_color"],
                font=get_system_font(),
                activestyle="none"
            )
        elif widget_type == "text":
            widget.configure(
                bg=theme_colors["bg_primary"],
                fg=theme_colors["fg_primary"],
                selectbackground=theme_colors["selection_bg"],
                selectforeground=theme_colors["selection_fg"],
                relief="solid",
                borderwidth=1,
                highlightthickness=1,
                highlightcolor=theme_colors["accent_color"],
                highlightbackground=theme_colors["border_color"],
                font=get_text_font(),
                wrap="word",
                padx=12,
                pady=8
            )
        elif widget_type == "scrollbar":
            widget.configure(
                bg=theme_colors["bg_secondary"],
                troughcolor=theme_colors["bg_accent"],
                activebackground=theme_colors["accent_hover"],
                relief="flat",
                borderwidth=0,
                highlightthickness=0
            )
    except Exception:
        pass  # Ignore styling errors on different platforms

def create_modern_button(parent, text, command, button_type="primary", theme_colors=None):
    """Create a modern styled button"""
    if theme_colors is None:
        theme_colors = get_theme_colors()
    
    if button_type == "primary":
        bg_color = theme_colors["accent_color"]
        fg_color = "#FFFFFF"
        hover_color = theme_colors["accent_hover"]
    else:  # secondary
        bg_color = theme_colors["bg_secondary"]
        fg_color = theme_colors["fg_primary"]
        hover_color = theme_colors["bg_accent"]
    
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg_color,
        fg=fg_color,
        font=get_system_font(),
        relief="flat",
        borderwidth=0,
        padx=20,
        pady=8,
        cursor="hand2" if IS_WINDOWS else "pointinghand"
    )
    
    # Add hover effects
    def on_enter(e):
        button.configure(bg=hover_color)
    
    def on_leave(e):
        button.configure(bg=bg_color)
    
    button.bind("<Enter>", on_enter)
    button.bind("<Leave>", on_leave)
    
    return button

def configure_modern_window(window):
    """Apply modern window styling"""
    theme_colors = get_theme_colors()
    
    try:
        window.configure(bg=theme_colors["bg_primary"])
        
        if IS_WINDOWS:
            # Windows-specific modern styling
            try:
                # Try to remove window decorations for modern look (Windows 10/11)
                window.overrideredirect(False)  # Keep decorations for better UX
                window.attributes('-alpha', 0.98)  # Slight transparency
            except:
                pass
        
        # Apply platform-specific configurations
        configure_window_for_platform(window)
        
    except Exception:
        pass  # Fallback to basic styling

def configure_macos_app():
    """Configure macOS-specific application settings"""
    if IS_MACOS:
        try:
            # Try to bring Python to front on macOS
            subprocess.run([
                'osascript', '-e', 
                'tell application "System Events" to set frontmost of first process whose unix id is {} to true'.format(os.getpid())
            ], check=False, capture_output=True)
        except Exception:
            pass  # Ignore if osascript is not available

def ensure_gui_initialized():
    """Ensure GUI subsystem is properly initialized"""
    global _gui_initialized
    with _gui_lock:
        if not _gui_initialized:
            try:
                test_root = tk.Tk()
                test_root.withdraw()
                
                # Platform-specific initialization
                if IS_MACOS:
                    # macOS-specific configuration
                    test_root.call('wm', 'attributes', '.', '-topmost', '1')
                    configure_macos_app()
                elif IS_WINDOWS:
                    # Windows-specific configuration (existing behavior)
                    test_root.attributes('-topmost', True)
                
                test_root.destroy()
                _gui_initialized = True
            except Exception as e:
                print(f"Warning: GUI initialization failed: {e}")
                _gui_initialized = False
        return _gui_initialized

def configure_window_for_platform(window):
    """Apply platform-specific window configurations"""
    try:
        if IS_MACOS:
            # macOS-specific window configuration
            window.call('wm', 'attributes', '.', '-topmost', '1')
            window.lift()
            window.focus_force()
            # Try to activate the app on macOS
            configure_macos_app()
        elif IS_WINDOWS:
            # Windows-specific configuration (existing behavior)
            window.attributes('-topmost', True)
            window.lift()
            window.focus_force()
    except Exception as e:
        print(f"Warning: Platform-specific window configuration failed: {e}")

def create_input_dialog(title: str, prompt: str, default_value: str = "", input_type: str = "text"):
    """Create a modern input dialog window"""
    try:
        root = tk.Tk()
        root.withdraw()
        dialog = ModernInputDialog(root, title, prompt, default_value, input_type)
        result = dialog.result
        root.destroy()
        return result
    except Exception as e:
        print(f"Error in input dialog: {e}")
        return None

def show_confirmation(title: str, message: str):
    """Show modern confirmation dialog"""
    try:
        root = tk.Tk()
        root.withdraw()
        dialog = ModernConfirmationDialog(root, title, message)
        result = dialog.result
        root.destroy()
        return result
    except Exception as e:
        print(f"Error in confirmation dialog: {e}")
        return False

def show_info(title: str, message: str):
    """Show modern info dialog"""
    try:
        root = tk.Tk()
        root.withdraw()
        dialog = ModernInfoDialog(root, title, message)
        result = dialog.result
        root.destroy()
        return result
    except Exception as e:
        print(f"Error in info dialog: {e}")
        return False

class ModernInputDialog:
    def __init__(self, parent, title, prompt, default_value="", input_type="text"):
        self.result = None
        self.input_type = input_type
        
        # Get theme colors
        self.theme_colors = get_theme_colors()
        
        # Create the dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        
        # Apply modern window styling
        configure_modern_window(self.dialog)
        
        # Set size based on platform
        if IS_WINDOWS:
            self.dialog.geometry("420x280")
        else:
            self.dialog.geometry("400x260")
        
        self.center_window()
        
        # Create the main frame
        main_frame = tk.Frame(self.dialog, bg=self.theme_colors["bg_primary"])
        main_frame.pack(fill="both", expand=True, padx=24, pady=20)
        
        # Title label
        title_label = tk.Label(
            main_frame,
            text=title,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_primary"],
            font=get_title_font(),
            anchor="w"
        )
        title_label.pack(fill="x", pady=(0, 8))
        
        # Prompt label
        prompt_label = tk.Label(
            main_frame,
            text=prompt,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_secondary"],
            font=get_system_font(),
            wraplength=350,
            justify="left",
            anchor="w"
        )
        prompt_label.pack(fill="x", pady=(0, 20))
        
        # Input field
        input_frame = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        input_frame.pack(fill="x", pady=(0, 24))
        
        self.entry = tk.Entry(
            input_frame,
            font=get_system_font(),
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_primary"],
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightcolor=self.theme_colors["accent_color"],
            highlightbackground=self.theme_colors["border_color"],
            insertbackground=self.theme_colors["accent_color"]
        )
        self.entry.pack(fill="x", ipady=8, ipadx=12)
        
        if default_value:
            self.entry.insert(0, default_value)
            self.entry.select_range(0, tk.END)
        
        # Button frame
        button_frame = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        button_frame.pack(fill="x")
        
        # Create modern buttons
        self.ok_button = create_modern_button(
            button_frame, "OK", self.ok_clicked, "primary", self.theme_colors
        )
        self.ok_button.pack(side=tk.RIGHT, padx=(8, 0))
        
        self.cancel_button = create_modern_button(
            button_frame, "Cancel", self.cancel_clicked, "secondary", self.theme_colors
        )
        self.cancel_button.pack(side=tk.RIGHT)
        
        # Handle window close and keyboard shortcuts
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel_clicked)
        self.dialog.bind('<Return>', lambda e: self.ok_clicked())
        self.dialog.bind('<Escape>', lambda e: self.cancel_clicked())
        
        # Focus on entry
        self.entry.focus_set()
        
        # Wait for dialog completion
        self.dialog.wait_window()
    
    def center_window(self):
        """Center the dialog window on screen"""
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        
        if IS_MACOS:
            y = max(50, y - 50)
        elif IS_WINDOWS:
            y = max(30, y - 30)
            
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def ok_clicked(self):
        value = self.entry.get()
        if self.input_type == "integer":
            try:
                self.result = int(value) if value else None
            except ValueError:
                self.result = None
        elif self.input_type == "float":
            try:
                self.result = float(value) if value else None
            except ValueError:
                self.result = None
        else:
            self.result = value if value else None
        self.dialog.destroy()
    
    def cancel_clicked(self):
        self.result = None
        self.dialog.destroy()

class ModernConfirmationDialog:
    def __init__(self, parent, title, message):
        self.result = False
        
        # Get theme colors
        self.theme_colors = get_theme_colors()
        
        # Create the dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        
        # Apply modern window styling
        configure_modern_window(self.dialog)
        
        # Set size based on content
        if IS_WINDOWS:
            self.dialog.geometry("440x220")
        else:
            self.dialog.geometry("420x200")
        
        self.center_window()
        
        # Create the main frame
        main_frame = tk.Frame(self.dialog, bg=self.theme_colors["bg_primary"])
        main_frame.pack(fill="both", expand=True, padx=24, pady=20)
        
        # Title label
        title_label = tk.Label(
            main_frame,
            text=title,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_primary"],
            font=get_title_font(),
            anchor="w"
        )
        title_label.pack(fill="x", pady=(0, 12))
        
        # Message label
        message_label = tk.Label(
            main_frame,
            text=message,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_secondary"],
            font=get_system_font(),
            wraplength=370,
            justify="left",
            anchor="w"
        )
        message_label.pack(fill="x", pady=(0, 24))
        
        # Button frame
        button_frame = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        button_frame.pack(fill="x")
        
        # Create modern buttons
        self.yes_button = create_modern_button(
            button_frame, "Yes", self.yes_clicked, "primary", self.theme_colors
        )
        self.yes_button.pack(side=tk.RIGHT, padx=(8, 0))
        
        self.no_button = create_modern_button(
            button_frame, "No", self.no_clicked, "secondary", self.theme_colors
        )
        self.no_button.pack(side=tk.RIGHT)
        
        # Handle window close and keyboard shortcuts
        self.dialog.protocol("WM_DELETE_WINDOW", self.no_clicked)
        self.dialog.bind('<Return>', lambda e: self.yes_clicked())
        self.dialog.bind('<Escape>', lambda e: self.no_clicked())
        
        # Focus on No button by default (safer)
        self.no_button.focus_set()
        
        # Wait for dialog completion
        self.dialog.wait_window()
    
    def center_window(self):
        """Center the dialog window on screen"""
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        
        if IS_MACOS:
            y = max(50, y - 50)
        elif IS_WINDOWS:
            y = max(30, y - 30)
            
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def yes_clicked(self):
        self.result = True
        self.dialog.destroy()
    
    def no_clicked(self):
        self.result = False
        self.dialog.destroy()

class ModernInfoDialog:
    def __init__(self, parent, title, message):
        self.result = True
        
        # Get theme colors
        self.theme_colors = get_theme_colors()
        
        # Create the dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        
        # Apply modern window styling
        configure_modern_window(self.dialog)
        
        # Set size based on content
        if IS_WINDOWS:
            self.dialog.geometry("420x200")
        else:
            self.dialog.geometry("400x180")
        
        self.center_window()
        
        # Create the main frame
        main_frame = tk.Frame(self.dialog, bg=self.theme_colors["bg_primary"])
        main_frame.pack(fill="both", expand=True, padx=24, pady=20)
        
        # Title label
        title_label = tk.Label(
            main_frame,
            text=title,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_primary"],
            font=get_title_font(),
            anchor="w"
        )
        title_label.pack(fill="x", pady=(0, 12))
        
        # Message label
        message_label = tk.Label(
            main_frame,
            text=message,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_secondary"],
            font=get_system_font(),
            wraplength=350,
            justify="left",
            anchor="w"
        )
        message_label.pack(fill="x", pady=(0, 24))
        
        # Button frame
        button_frame = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        button_frame.pack(fill="x")
        
        # Create modern OK button
        self.ok_button = create_modern_button(
            button_frame, "OK", self.ok_clicked, "primary", self.theme_colors
        )
        self.ok_button.pack(side=tk.RIGHT)
        
        # Handle window close and keyboard shortcuts
        self.dialog.protocol("WM_DELETE_WINDOW", self.ok_clicked)
        self.dialog.bind('<Return>', lambda e: self.ok_clicked())
        self.dialog.bind('<Escape>', lambda e: self.ok_clicked())
        
        # Focus on OK button
        self.ok_button.focus_set()
        
        # Wait for dialog completion
        self.dialog.wait_window()
    
    def center_window(self):
        """Center the dialog window on screen"""
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        
        if IS_MACOS:
            y = max(50, y - 50)
        elif IS_WINDOWS:
            y = max(30, y - 30)
            
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def ok_clicked(self):
        self.result = True
        self.dialog.destroy()

def create_choice_dialog(title: str, prompt: str, choices: List[str], allow_multiple: bool = False):
    """Create a choice dialog window"""
    try:
        root = tk.Tk()
        root.withdraw()
        dialog = ChoiceDialog(root, title, prompt, choices, allow_multiple)
        result = dialog.result
        root.destroy()
        return result
    except Exception as e:
        print(f"Error in choice dialog: {e}")
        return None

def create_multiline_input_dialog(title: str, prompt: str, default_value: str = ""):
    """Create a multi-line text input dialog"""
    try:
        root = tk.Tk()
        root.withdraw()
        dialog = MultilineInputDialog(root, title, prompt, default_value)
        result = dialog.result
        root.destroy()
        return result
    except Exception as e:
        print(f"Error in multiline dialog: {e}")
        return None

def show_confirmation(title: str, message: str):
    """Show confirmation dialog"""
    try:
        root = tk.Tk()
        root.withdraw()
        configure_window_for_platform(root)
        result = messagebox.askyesno(title, message, parent=root)
        root.destroy()
        return result
    except Exception as e:
        print(f"Error in confirmation dialog: {e}")
        return False

def show_info(title: str, message: str):
    """Show info dialog"""
    try:
        root = tk.Tk()
        root.withdraw()
        configure_window_for_platform(root)
        messagebox.showinfo(title, message, parent=root)
        root.destroy()
        return True
    except Exception as e:
        print(f"Error in info dialog: {e}")
        return False

class ChoiceDialog:
    def __init__(self, parent, title, prompt, choices, allow_multiple=False):
        self.result = None
        
        # Get theme colors
        self.theme_colors = get_theme_colors()
        
        # Create the dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.grab_set()
        self.dialog.resizable(True, True)
        
        # Apply modern window styling
        configure_modern_window(self.dialog)
        
        # Set size based on platform
        if IS_MACOS:
            self.dialog.geometry("480x400")
        elif IS_WINDOWS:
            self.dialog.geometry("500x420")
        else:
            self.dialog.geometry("450x350")
        
        self.center_window()
        
        # Create the main frame with modern styling
        main_frame = tk.Frame(self.dialog, bg=self.theme_colors["bg_primary"])
        main_frame.pack(fill="both", expand=True, padx=24, pady=20)
        
        # Configure grid weights
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        # Add modern title label
        title_label = tk.Label(
            main_frame, 
            text=title,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_primary"],
            font=get_title_font(),
            anchor="w"
        )
        title_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        
        # Add prompt label with modern styling
        prompt_label = tk.Label(
            main_frame,
            text=prompt,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_secondary"],
            font=get_system_font(),
            wraplength=450,
            justify="left",
            anchor="w"
        )
        prompt_label.grid(row=1, column=0, sticky="ew", pady=(0, 20))
        
        # Create choice selection widget with modern container
        list_container = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        list_container.grid(row=2, column=0, sticky="nsew", pady=(0, 24))
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)
        
        # Modern listbox with styling
        if allow_multiple:
            self.listbox = tk.Listbox(list_container, selectmode=tk.MULTIPLE, height=8)
        else:
            self.listbox = tk.Listbox(list_container, selectmode=tk.SINGLE, height=8)
        
        apply_modern_style(self.listbox, "listbox", self.theme_colors)
        
        for choice in choices:
            self.listbox.insert(tk.END, choice)
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        
        # Modern scrollbar
        scrollbar = tk.Scrollbar(list_container, orient="vertical", command=self.listbox.yview)
        apply_modern_style(scrollbar, "scrollbar", self.theme_colors)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        
        # Modern button frame
        button_frame = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        button_frame.grid(row=3, column=0, sticky="ew")
        
        # Create modern buttons
        self.ok_button = create_modern_button(
            button_frame, "OK", self.ok_clicked, "primary", self.theme_colors
        )
        self.ok_button.pack(side=tk.RIGHT, padx=(8, 0))
        
        self.cancel_button = create_modern_button(
            button_frame, "Cancel", self.cancel_clicked, "secondary", self.theme_colors
        )
        self.cancel_button.pack(side=tk.RIGHT)
        
        # Handle window close
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel_clicked)
        
        # Focus on listbox
        self.listbox.focus_set()
        if choices:
            self.listbox.selection_set(0)  # Select first item by default
        
        # Platform-specific final setup
        if IS_MACOS:
            self.dialog.after(100, lambda: self.listbox.focus_set())
        
        # Add keyboard shortcuts
        self.dialog.bind('<Return>', lambda e: self.ok_clicked())
        self.dialog.bind('<Escape>', lambda e: self.cancel_clicked())
        
        # Wait for the dialog to complete
        self.dialog.wait_window()
    
    def center_window(self):
        """Center the dialog window on screen"""
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        
        # Get screen dimensions
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        
        # Calculate center position
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        
        # Platform-specific adjustments
        if IS_MACOS:
            y = max(50, y - 50)
        elif IS_WINDOWS:
            y = max(30, y - 30)
        
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def ok_clicked(self):
        selection = self.listbox.curselection()
        if selection:
            selected_items = [self.listbox.get(i) for i in selection]
            self.result = selected_items if len(selected_items) > 1 else selected_items[0]
        self.dialog.destroy()
    
    def cancel_clicked(self):
        self.result = None
        self.dialog.destroy()

class MultilineInputDialog:
    def __init__(self, parent, title, prompt, default_value=""):
        self.result = None
        self._user_input_mark = "user_input_start"  # Tkinter mark name

        # Get theme colors
        self.theme_colors = get_theme_colors()

        # Create the dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        # NOTE: grab_set() is intentionally omitted — on Windows it fights with
        # VS Code for the window grab and causes the dialog to self-cancel when
        # VS Code briefly reclaims focus. wait_window() below is sufficient.
        self.dialog.resizable(True, True)

        # Apply modern window styling
        configure_modern_window(self.dialog)

        # Set size based on platform (taller to accommodate AI output + input)
        if IS_MACOS:
            self.dialog.geometry("620x640")
        elif IS_WINDOWS:
            self.dialog.geometry("650x660")
        else:
            self.dialog.geometry("580x600")

        self.center_window()

        # Create the main frame with modern styling
        main_frame = tk.Frame(self.dialog, bg=self.theme_colors["bg_primary"])
        main_frame.pack(fill="both", expand=True, padx=24, pady=20)

        # Configure grid weights — row 1 holds the text widget and expands
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # Add title label (row 0)
        title_label = tk.Label(
            main_frame,
            text=title,
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_primary"],
            font=get_title_font(),
            anchor="w"
        )
        title_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        # Create text widget container (row 1) — no separate prompt label,
        # the AI output is shown directly inside the text widget.
        text_container = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        text_container.grid(row=1, column=0, sticky="nsew", pady=(0, 16))
        text_container.columnconfigure(0, weight=1)
        text_container.rowconfigure(0, weight=1)

        # Modern text widget
        self.text_widget = tk.Text(text_container, height=20)
        apply_modern_style(self.text_widget, "text", self.theme_colors)
        # Set widget-level selection colors — these control the highlight when
        # the widget does NOT have keyboard focus (inactiveselectbackground).
        # Without this Windows shows a near-invisible grey for inactive selection.
        self.text_widget.configure(
            selectbackground=self.theme_colors["accent_color"],
            selectforeground="#FFFFFF",
            inactiveselectbackground=self.theme_colors["accent_color"],
        )
        self.text_widget.grid(row=0, column=0, sticky="nsew", padx=(0, 2))

        # Modern scrollbar
        text_scrollbar = tk.Scrollbar(text_container, orient="vertical", command=self.text_widget.yview)
        apply_modern_style(text_scrollbar, "scrollbar", self.theme_colors)
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        self.text_widget.configure(yscrollcommand=text_scrollbar.set)

        # ── Tag for the AI output region (visually distinct, read-only feel) ──
        self.text_widget.tag_configure(
            "ai_output",
            foreground=self.theme_colors["fg_primary"],
            background=self.theme_colors["bg_accent"],
            font=get_text_font(),
            lmargin1=8,
            lmargin2=8,
        )
        # ── Tag for the delineator line ──
        self.text_widget.tag_configure(
            "separator",
            foreground=self.theme_colors["accent_color"],
            font=get_system_font(),
            justify="center",
        )
        # ── Tag for the user input region ──
        self.text_widget.tag_configure(
            "user_input",
            foreground=self.theme_colors["fg_primary"],
            background=self.theme_colors["bg_primary"],
            font=get_text_font(),
            lmargin1=8,
            lmargin2=8,
        )
        # ── Make selection always visible, even over custom tag backgrounds ──
        # Configure the built-in 'sel' tag explicitly so it is never washed out.
        self.text_widget.tag_configure(
            "sel",
            background=self.theme_colors["accent_color"],
            foreground="#FFFFFF",
        )
        # tag_raise must come AFTER all tag_configure calls.
        self.text_widget.tag_raise("sel")

        # Insert AI output (prompt) into the textbox
        if prompt:
            self.text_widget.insert("1.0", prompt)
            ai_end = self.text_widget.index(tk.END)
            self.text_widget.tag_add("ai_output", "1.0", ai_end)

        # Insert delineator on its own line
        sep_text = "\n" + MULTILINE_DELINEATOR + "\n"
        sep_start = self.text_widget.index(tk.END)
        self.text_widget.insert(tk.END, sep_text)
        sep_end = self.text_widget.index(tk.END)
        self.text_widget.tag_add("separator", sep_start, sep_end)

        # Place the 'user_input_start' mark right after the delineator.
        # LEFT gravity: when the user types AT the mark position the mark stays
        # put and text is inserted to its right, so get(mark, END) always
        # captures exactly what the user typed and nothing more.
        self.text_widget.mark_set(self._user_input_mark, tk.END)
        self.text_widget.mark_gravity(self._user_input_mark, "left")

        # Insert any pre-fill for the user area (e.g., default_value)
        if default_value:
            self.text_widget.insert(tk.END, default_value)
            user_end = self.text_widget.index(tk.END)
            self.text_widget.tag_add("user_input", self._user_input_mark, user_end)

        # Move cursor to end of user input area and scroll there
        self.text_widget.mark_set(tk.INSERT, tk.END)
        self.text_widget.see(tk.END)

        # ── Make the built-in 'sel' tag render ON TOP of our custom tags ──
        # Without this our ai_output / user_input tag backgrounds swallow the
        # blue selection highlight and make it invisible.
        self.text_widget.tag_raise("sel")

        def _guard_printable(event):
            """
            For printable keys only: if cursor is in the protected zone, silently
            move it to the user zone so text is inserted in the right place.
            Navigation, Ctrl/Alt combos, BackSpace, Delete are never intercepted
            so tkinter's native Text handling works fully unimpeded.
            """
            if event.state & (0x4 | 0x8):   # Ctrl or Alt — always pass through
                return
            if not event.char or event.keysym in (
                'BackSpace', 'Delete',
                'Up', 'Down', 'Left', 'Right',
                'Home', 'End', 'Prior', 'Next',
                'Shift_L', 'Shift_R', 'Control_L', 'Control_R',
                'Alt_L', 'Alt_R', 'Tab', 'ISO_Left_Tab',
                'Escape', 'Return',
                'F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12',
            ):
                return
            try:
                user_start = self.text_widget.index(self._user_input_mark)
                cursor     = self.text_widget.index(tk.INSERT)
                if self.text_widget.compare(cursor, "<", user_start):
                    self.text_widget.mark_set(tk.INSERT, user_start)
            except Exception:
                pass

        def _do_backspace(event):
            """Only block backspace when it would delete into the protected AI zone.
            Otherwise, let tkinter's native Text class binding handle it."""
            import sys
            user_start = self.text_widget.index(self._user_input_mark)
            cursor = self.text_widget.index(tk.INSERT)
            print(f"[DEBUG BS] cursor={cursor} user_start={user_start}", file=sys.stderr)
            if self.text_widget.compare(cursor, "<=", user_start):
                print(f"[DEBUG BS] BLOCKED", file=sys.stderr)
                return "break"  # Block: would delete into protected zone
            # Allow tkinter's native backspace — do NOT return "break"
            print(f"[DEBUG BS] ALLOWED (returning None)", file=sys.stderr)
            return None

        def _do_delete(event):
            """Only block delete when cursor is in the protected AI zone."""
            import sys
            user_start = self.text_widget.index(self._user_input_mark)
            cursor = self.text_widget.index(tk.INSERT)
            print(f"[DEBUG DEL] cursor={cursor} user_start={user_start}", file=sys.stderr)
            if self.text_widget.compare(cursor, "<", user_start):
                return "break"
            return None

        # Guard printable keys from entering the protected zone
        self.text_widget.bind('<Key>', _guard_printable, add=True)
        # Bind as add=True so tkinter's class-level Text bindings still fire
        self.text_widget.bind('<BackSpace>', _do_backspace, add=True)
        self.text_widget.bind('<Delete>',    _do_delete, add=True)

        # ── Right-click context menu (copy / select-all / paste) ──
        ctx_menu = tk.Menu(self.text_widget, tearoff=0)
        ctx_menu.add_command(
            label="Copy",
            command=lambda: self.text_widget.event_generate("<<Copy>>")
        )
        ctx_menu.add_command(
            label="Select All",
            command=lambda: (
                self.text_widget.tag_add(tk.SEL, "1.0", tk.END),
                self.text_widget.mark_set(tk.INSERT, tk.END),
            )
        )
        ctx_menu.add_separator()
        ctx_menu.add_command(
            label="Paste",
            command=lambda: self.text_widget.event_generate("<<Paste>>")
        )

        def _show_context_menu(event):
            # Move cursor to click position before showing menu
            self.text_widget.mark_set(tk.INSERT, f"@{event.x},{event.y}")
            try:
                ctx_menu.tk_popup(event.x_root, event.y_root)
            finally:
                ctx_menu.grab_release()

        self.text_widget.bind('<Button-3>', _show_context_menu)

        # Hint label below the textbox
        hint_label = tk.Label(
            main_frame,
            text="AI output is shown above the line  ·  Type your response below it  ·  Ctrl+Enter to submit",
            bg=self.theme_colors["bg_primary"],
            fg=self.theme_colors["fg_secondary"],
            font=(get_system_font()[0], get_system_font()[1] - 1),
            anchor="center",
        )
        hint_label.grid(row=2, column=0, sticky="ew", pady=(0, 12))

        # Modern button frame (row 3)
        button_frame = tk.Frame(main_frame, bg=self.theme_colors["bg_primary"])
        button_frame.grid(row=3, column=0, sticky="ew")

        # Create modern buttons
        self.ok_button = create_modern_button(
            button_frame, "Submit", self.ok_clicked, "primary", self.theme_colors
        )
        self.ok_button.pack(side=tk.RIGHT, padx=(8, 0))

        self.cancel_button = create_modern_button(
            button_frame, "Cancel", self.cancel_clicked, "secondary", self.theme_colors
        )
        self.cancel_button.pack(side=tk.RIGHT)

        # Handle window close
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel_clicked)

        # Focus on text widget
        self.text_widget.focus_set()

        # Platform-specific final setup
        if IS_MACOS:
            self.dialog.after(100, lambda: self.text_widget.focus_set())

        # Keyboard shortcuts
        self.dialog.bind('<Control-Return>', lambda e: self.ok_clicked())
        self.dialog.bind('<Escape>', lambda e: self.cancel_clicked())

        # Wait for the dialog to complete
        self.dialog.wait_window()
    
    def center_window(self):
        """Center the dialog window on screen"""
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        
        # Get screen dimensions
        screen_width = self.dialog.winfo_screenwidth()
        screen_height = self.dialog.winfo_screenheight()
        
        # Calculate center position
        x = (screen_width // 2) - (width // 2)
        y = (screen_height // 2) - (height // 2)
        
        # Platform-specific adjustments
        if IS_MACOS:
            y = max(50, y - 50)
        elif IS_WINDOWS:
            y = max(30, y - 30)
        
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def ok_clicked(self):
        """Return only the text the user typed (after the delineator)."""
        full = self.text_widget.get("1.0", tk.END)
        if MULTILINE_DELINEATOR in full:
            self.result = full[full.index(MULTILINE_DELINEATOR) + len(MULTILINE_DELINEATOR):].strip()
        else:
            self.result = full.strip()
        self.dialog.destroy()

    def cancel_clicked(self):
        self.result = None
        self.dialog.destroy()

# MCP Tools

@mcp.tool()
async def get_user_input(
    title: Annotated[str, Field(description="Title of the input dialog window")],
    prompt: Annotated[str, Field(description="The prompt/question to show to the user")],
    default_value: Annotated[str, Field(description="Default value to pre-fill in the input field")] = "",
    input_type: Annotated[Literal["text", "integer", "float"], Field(description="Type of input expected")] = "text",
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Create an input dialog window for the user to enter text, numbers, or other data.
    
    This tool opens a GUI dialog box where the user can input information that the LLM needs.
    Perfect for getting specific details, clarifications, or data from the user.
    """
    try:
        if ctx:
            await ctx.info(f"Requesting user input: {prompt}")
        
        # Ensure GUI is initialized
        if not ensure_gui_initialized():
            return {
                "success": False,
                "error": "GUI system not available",
                "cancelled": False,
                "platform": CURRENT_PLATFORM
            }
        
        # Create the dialog in a separate thread to avoid blocking
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(create_input_dialog, title, prompt, default_value, input_type)
            result = future.result(timeout=300)  # 5 minute timeout
        
        if result is not None:
            if ctx:
                await ctx.info(f"User provided input: {result}")
            return {
                "success": True,
                "user_input": result,
                "input_type": input_type,
                "cancelled": False,
                "platform": CURRENT_PLATFORM
            }
        else:
            if ctx:
                await ctx.warning("User cancelled the input dialog")
            return {
                "success": False,
                "user_input": None,
                "input_type": input_type,
                "cancelled": True,
                "platform": CURRENT_PLATFORM
            }
    
    except Exception as e:
        if ctx:
            await ctx.error(f"Error creating input dialog: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "cancelled": False,
            "platform": CURRENT_PLATFORM
        }

@mcp.tool()
async def get_user_choice(
    title: Annotated[str, Field(description="Title of the choice dialog window")],
    prompt: Annotated[str, Field(description="The prompt/question to show to the user")],
    choices: Annotated[List[str], Field(description="List of choices to present to the user")],
    allow_multiple: Annotated[bool, Field(description="Whether user can select multiple choices")] = False,
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Create a choice dialog window for the user to select from multiple options.
    
    This tool opens a GUI dialog box with a list of choices where the user can select
    one or multiple options. Perfect for getting decisions, preferences, or selections from the user.
    """
    try:
        if ctx:
            await ctx.info(f"Requesting user choice: {prompt}")
            await ctx.debug(f"Available choices: {choices}")
        
        # Ensure GUI is initialized
        if not ensure_gui_initialized():
            return {
                "success": False,
                "error": "GUI system not available",
                "cancelled": False,
                "platform": CURRENT_PLATFORM
            }
        
        # Create the dialog in a separate thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(create_choice_dialog, title, prompt, choices, allow_multiple)
            result = future.result(timeout=300)  # 5 minute timeout
        
        if result is not None:
            if ctx:
                await ctx.info(f"User selected: {result}")
            return {
                "success": True,
                "selected_choice": result,
                "selected_choices": result if isinstance(result, list) else [result],
                "allow_multiple": allow_multiple,
                "cancelled": False,
                "platform": CURRENT_PLATFORM
            }
        else:
            if ctx:
                await ctx.warning("User cancelled the choice dialog")
            return {
                "success": False,
                "selected_choice": None,
                "selected_choices": [],
                "allow_multiple": allow_multiple,
                "cancelled": True,
                "platform": CURRENT_PLATFORM
            }
    
    except Exception as e:
        if ctx:
            await ctx.error(f"Error creating choice dialog: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "cancelled": False,
            "platform": CURRENT_PLATFORM
        }

@mcp.tool()
async def get_multiline_input(
    title: Annotated[str, Field(description="Title of the input dialog window")],
    prompt: Annotated[str, Field(description="The prompt/question to show to the user")],
    default_value: Annotated[str, Field(description="Default text to pre-fill in the text area")] = "",
    ctx: Context = None
) -> Any:
    """
    Create a multi-line text input dialog for the user to enter longer text content.
    
    This tool opens a GUI dialog box with a large text area where the user can input
    multiple lines of text. Perfect for getting detailed descriptions, code, or long-form content.
    """
    try:
        if ctx:
            await ctx.info(f"Requesting multiline user input: {prompt}")

        if is_telegram_enabled():
            try:
                if ctx:
                    await ctx.info("Telegram HITL mode enabled. Sending prompt and awaiting Telegram reply.")

                timeout_seconds_raw = os.getenv("HITL_TELEGRAM_TIMEOUT_SECONDS", "3600")
                try:
                    timeout_seconds = max(30, int(timeout_seconds_raw))
                except ValueError:
                    timeout_seconds = 3600

                result = await asyncio.to_thread(
                    _send_and_wait_telegram_multiline_input,
                    title,
                    prompt,
                    default_value,
                    timeout_seconds
                )

                if result is not None:
                    if ctx:
                        await ctx.info(f"Received multiline input from Telegram ({len(result)} characters)")

                    # ── Detect special message types ──
                    whispr_meta = None
                    image_payload = None
                    user_text = result
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict):
                            # ── Whispr voice message ──
                            if parsed.get("__whispr__"):
                                user_text = parsed.get("text", result)
                                whispr_meta = {
                                    "whispr": True,
                                    "original_transcription": parsed.get("original", ""),
                                    "edits": parsed.get("edits", []),
                                }
                                if ctx:
                                    edits = len(parsed.get("edits", []))
                                    await ctx.info(
                                        f"Voice message transcribed via Whispr"
                                        + (f" ({edits} edit(s) applied)" if edits else "")
                                    )
                            # ── Image message ──
                            elif parsed.get("__image__"):
                                image_payload = parsed
                                caption = parsed.get("caption", "")
                                user_text = caption or "[User sent an image]"
                                if ctx:
                                    w = parsed.get("width", "?")
                                    h = parsed.get("height", "?")
                                    sz = parsed.get("file_size", 0)
                                    await ctx.info(
                                        f"Image received from Telegram ({w}x{h}, {sz} bytes)"
                                    )
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # ── Return image as mixed content (text + image) ──
                    if image_payload:
                        metadata = {
                            "success": True,
                            "user_input": user_text,
                            "character_count": len(user_text),
                            "line_count": len(user_text.split('\n')),
                            "cancelled": False,
                            "platform": CURRENT_PLATFORM,
                            "transport": "telegram",
                            "has_image": True,
                            "image_width": image_payload.get("width"),
                            "image_height": image_payload.get("height"),
                            "image_file_size": image_payload.get("file_size"),
                            "image_mime_type": image_payload.get("mime_type", "image/jpeg"),
                        }
                        return [
                            TextContent(type="text", text=json.dumps(metadata)),
                            ImageContent(
                                type="image",
                                data=image_payload["image_b64"],
                                mimeType=image_payload.get("mime_type", "image/jpeg"),
                            ),
                        ]

                    response = {
                        "success": True,
                        "user_input": user_text,
                        "character_count": len(user_text),
                        "line_count": len(user_text.split('\n')),
                        "cancelled": False,
                        "platform": CURRENT_PLATFORM,
                        "transport": "telegram",
                    }
                    if whispr_meta:
                        response.update(whispr_meta)
                    return response

                if ctx:
                    await ctx.warning("Telegram input timed out or was not received")
                return {
                    "success": False,
                    "user_input": None,
                    "cancelled": True,
                    "platform": CURRENT_PLATFORM,
                    "transport": "telegram",
                    "error": "Timed out waiting for Telegram response"
                }
            except Exception as telegram_error:
                if ctx:
                    await ctx.warning(f"Telegram transport unavailable ({telegram_error}). Falling back to popup dialog.")
        
        # Ensure GUI is initialized
        if not ensure_gui_initialized():
            return {
                "success": False,
                "error": "GUI system not available",
                "cancelled": False,
                "platform": CURRENT_PLATFORM
            }
        
        # Create the dialog in a separate thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(create_multiline_input_dialog, title, prompt, default_value)
            result = future.result(timeout=300)  # 5 minute timeout
        
        if result is not None:
            if ctx:
                await ctx.info(f"User provided multiline input ({len(result)} characters)")
            return {
                "success": True,
                "user_input": result,
                "character_count": len(result),
                "line_count": len(result.split('\n')),
                "cancelled": False,
                "platform": CURRENT_PLATFORM,
                "transport": "popup"
            }
        else:
            if ctx:
                await ctx.warning("User cancelled the multiline input dialog")
            return {
                "success": False,
                "user_input": None,
                "cancelled": True,
                "platform": CURRENT_PLATFORM,
                "transport": "popup"
            }
    
    except Exception as e:
        if ctx:
            await ctx.error(f"Error creating multiline input dialog: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "cancelled": False,
            "platform": CURRENT_PLATFORM
        }

@mcp.tool()
async def show_confirmation_dialog(
    title: Annotated[str, Field(description="Title of the confirmation dialog")],
    message: Annotated[str, Field(description="The message to show to the user")],
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Show a confirmation dialog with Yes/No buttons.
    
    This tool displays a message to the user and asks for confirmation.
    Perfect for getting approval before proceeding with an action.
    """
    try:
        if ctx:
            await ctx.info(f"Requesting user confirmation: {message}")
        
        # Ensure GUI is initialized
        if not ensure_gui_initialized():
            return {
                "success": False,
                "error": "GUI system not available",
                "confirmed": False,
                "platform": CURRENT_PLATFORM
            }
        
        # Create the dialog in a separate thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(show_confirmation, title, message)
            result = future.result(timeout=300)  # 5 minute timeout
        
        if ctx:
            await ctx.info(f"User confirmation result: {'Yes' if result else 'No'}")
        
        return {
            "success": True,
            "confirmed": result,
            "response": "yes" if result else "no",
            "platform": CURRENT_PLATFORM
        }
    
    except Exception as e:
        if ctx:
            await ctx.error(f"Error showing confirmation dialog: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "confirmed": False,
            "platform": CURRENT_PLATFORM
        }

@mcp.tool()
async def show_info_message(
    title: Annotated[str, Field(description="Title of the information dialog")],
    message: Annotated[str, Field(description="The information message to show to the user")],
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Show an information message to the user.
    
    This tool displays an informational message dialog to notify the user about something.
    The user just needs to click OK to acknowledge the message.
    """
    try:
        if ctx:
            await ctx.info(f"Showing info message to user: {message}")
        
        # Ensure GUI is initialized
        if not ensure_gui_initialized():
            return {
                "success": False,
                "error": "GUI system not available",
                "platform": CURRENT_PLATFORM
            }
        
        # Create the dialog in a separate thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(show_info, title, message)
            result = future.result(timeout=300)  # 5 minute timeout
        
        if ctx:
            await ctx.info("Info message acknowledged by user")
        
        return {
            "success": True,
            "acknowledged": result,
            "platform": CURRENT_PLATFORM
        }
    
    except Exception as e:
        if ctx:
            await ctx.error(f"Error showing info message: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "platform": CURRENT_PLATFORM
        }

# Add a prompt to get prompting guidance for LLMs
@mcp.prompt()
async def get_human_loop_prompt() -> Dict[str, str]:
    """
    Get prompting guidance for LLMs on when and how to use human-in-the-loop tools.
    
    This tool returns comprehensive guidance that helps LLMs understand when to pause
    and ask for human input, decisions, or feedback during task execution.
    """
    guidance = {
        "main_prompt": """
You have access to Human-in-the-Loop tools that allow you to interact directly with users through GUI dialogs. Use these tools strategically to enhance task completion and user experience.

**WHEN TO USE HUMAN-IN-THE-LOOP TOOLS:**

1. **Ambiguous Requirements** - When user instructions are unclear or could have multiple interpretations
2. **Decision Points** - When you need user preference between valid alternatives
3. **Creative Input** - For subjective choices like design, content style, or personal preferences
4. **Sensitive Operations** - Before executing potentially destructive or irreversible actions
5. **Missing Information** - When you need specific details not provided in the original request
6. **Quality Feedback** - To get user validation on intermediate results before proceeding
7. **Error Handling** - When encountering issues that require user guidance to resolve

**AVAILABLE TOOLS:**
- `get_user_input` - Single-line text/number input (names, values, paths, etc.)
- `get_user_choice` - Multiple choice selection (pick from options)
- `get_multiline_input` - Long-form text (descriptions, code, documents)
- `show_confirmation_dialog` - Yes/No decisions (confirmations, approvals)
- `show_info_message` - Status updates and notifications

**BEST PRACTICES:**
- Ask specific, clear questions with context
- Provide helpful default values when possible
- Use confirmation dialogs before destructive actions
- Give status updates for long-running processes
- Offer meaningful choices rather than overwhelming options
- Be concise but informative in dialog prompts""",
        
        "usage_examples": """
**EXAMPLE SCENARIOS:**

1. **File Operations:**
   - "I'm about to delete 15 files. Should I proceed?" (confirmation)
   - "Enter the target directory path:" (input)
   - "Choose backup format: Full, Incremental, Differential" (choice)

2. **Content Creation:**
   - "What tone should I use: Professional, Casual, Friendly?" (choice)
   - "Please provide any specific requirements:" (multiline input)
   - "Content generated successfully!" (info message)

3. **Code Development:**
   - "Enter the API endpoint URL:" (input)
   - "Select framework: React, Vue, Angular, Vanilla JS" (choice)
   - "Review the generated code and provide feedback:" (multiline input)

4. **Data Processing:**
   - "Found 3 data formats. Which should I use?" (choice)
   - "Enter the date range (YYYY-MM-DD to YYYY-MM-DD):" (input)
   - "Processing complete. 1,250 records updated." (info message)""",
        
        "decision_framework": """
**DECISION FRAMEWORK FOR HUMAN-IN-THE-LOOP:**

ASK YOURSELF:
1. Is this decision subjective or preference-based? → USE CHOICE DIALOG
2. Do I need specific information not provided? → USE INPUT DIALOG  
3. Could this action cause problems if wrong? → USE CONFIRMATION DIALOG
4. Is this a long process the user should know about? → USE INFO MESSAGE
5. Do I need detailed explanation or content? → USE MULTILINE INPUT

AVOID OVERUSE:
- Don't ask for information already provided
- Don't seek confirmation for obviously safe operations
- Don't interrupt flow for trivial decisions
- Don't ask multiple questions when one comprehensive dialog would suffice

OPTIMIZE FOR USER EXPERIENCE:
- Batch related questions together when possible
- Provide context for why you need the information
- Offer sensible defaults and suggestions
- Make dialogs self-explanatory and actionable""",
        
        "integration_tips": """
**INTEGRATION TIPS:**

1. **Workflow Integration:**
   ```
   Step 1: Analyze user request
   Step 2: Identify decision points and missing info
   Step 3: Use appropriate human-in-the-loop tools
   Step 4: Process user responses
   Step 5: Continue with enhanced information
   ```

2. **Error Recovery:**
   - If user cancels, gracefully explain and offer alternatives
   - Handle timeouts by providing default behavior
   - Always validate user input before proceeding

3. **Progressive Enhancement:**
   - Start with automated solutions
   - Add human input only where it adds clear value
   - Learn from user patterns to improve future automation

4. **Communication:**
   - Explain why you need user input
   - Show progress and intermediate results
   - Confirm successful completion of user-guided actions""",

        "whispr_voice_input": """
**WHISPR — VOICE MESSAGE TRANSCRIPTION:**

When Whispr is enabled and the user sends a voice or audio message in
Telegram instead of typing, the server automatically:

1. Downloads the voice note.
2. Transcribes it with faster-whisper (local, private, CUDA-accelerated).
3. Sends the user a confirmation message with buttons:
   ✅ Yes, proceed  |  ✏️ Edit  |  ❌ Cancel
4. If the user chooses Edit, they can type corrections and re-confirm.
5. Once approved, the final text is returned to you as `user_input`.

**How to detect a Whispr response:**
The return dict will contain `"whispr": True` plus:
- `original_transcription` — the raw model output
- `edits` — list of correction messages the user typed (may be empty)

If `edits` is non-empty, the user reworded something after the initial
transcription.  Treat `user_input` as the authoritative final text.

**MCP tools for Whispr:**
- `toggle_whispr(enabled, model?, language?)` — turn it on/off, set model or language.
- `health_check()` → includes `whispr.available`, `whispr.enabled`, `whispr.model`.

**Telegram commands the user can type:**
`/whispr on|off|status|model <size>|lang <code>`
"""
    }
    
    return guidance

# ── Whispr: MCP tool to toggle voice transcription ──
@mcp.tool()
async def toggle_whispr(
    enabled: bool,
    model: str = "",
    language: str = "",
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Enable or disable Whispr voice-message transcription in Telegram.

    When enabled, voice/audio messages from the user are automatically
    transcribed and sent through a confirmation flow before returning to
    the agent.

    Args:
        enabled:  True to enable, False to disable Whispr.
        model:    (optional) Whisper model size – tiny, base, small, medium, large-v3.
        language: (optional) ISO-639-1 language code (e.g. 'en', 'ru'). Empty = auto-detect.
    """
    if not _WHISPR_IMPORTED:
        return {
            "success": False,
            "error": "Whispr is not available – faster-whisper is not installed. "
                     "Install it with: pip install faster-whisper",
            "whispr_available": False,
        }

    cfg = whispr_get_config()
    cfg.enabled = enabled
    if model:
        cfg.model = model
    if language:
        cfg.language = language
    cfg.save()

    status_msg = f"Whispr {'enabled' if enabled else 'disabled'}"
    if model:
        status_msg += f" (model: {model})"
    if language:
        status_msg += f" (language: {language})"

    if ctx:
        await ctx.info(status_msg)

    return {
        "success": True,
        "whispr_available": True,
        "whispr_enabled": cfg.enabled,
        "whispr_model": cfg.model,
        "whispr_language": cfg.language or "auto-detect",
        "message": status_msg,
    }


# Add a health check tool
@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check if the Human-in-the-Loop server is running and GUI is available."""
    try:
        gui_available = ensure_gui_initialized()
        telegram_enabled = is_telegram_enabled()

        # ── Whispr status ──
        whispr_info = {"available": False, "enabled": False}
        if _WHISPR_IMPORTED:
            whispr_info["available"] = whispr_is_available()
            whispr_info["enabled"] = whispr_is_enabled()
            if whispr_info["available"]:
                cfg = whispr_get_config()
                whispr_info["model"] = cfg.model
                whispr_info["language"] = cfg.language or "auto-detect"
        
        return {
            "status": "healthy" if gui_available else "degraded",
            "gui_available": gui_available,
            "telegram_enabled": telegram_enabled,
            "telegram_config": {
                "has_bot_token": bool(os.getenv("HITL_TELEGRAM_BOT_TOKEN")),
                "has_chat_id": bool(os.getenv("HITL_TELEGRAM_CHAT_ID")),
                "chat_id": os.getenv("HITL_TELEGRAM_CHAT_ID", "")
            },
            "whispr": whispr_info,
            "server_name": "Human-in-the-Loop Server",
            "platform": CURRENT_PLATFORM,
            "platform_details": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor()
            },
            "python_version": sys.version.split()[0],
            "is_windows": IS_WINDOWS,
            "is_macos": IS_MACOS,
            "is_linux": IS_LINUX,
            "tools_available": [
                "get_user_input",
                "get_user_choice", 
                "get_multiline_input",
                "show_confirmation_dialog",
                "show_info_message",
                "get_human_loop_prompt",
                "toggle_whispr"
            ]
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "gui_available": False,
            "error": str(e),
            "platform": CURRENT_PLATFORM
        }

# Main execution

def main():
    global _session_coordinator

    print("Starting Human-in-the-Loop MCP Server...")
    print("This server provides tools for LLMs to interact with humans through GUI dialogs.")
    print(f"Platform: {CURRENT_PLATFORM} ({platform.system()} {platform.release()})")
    print("")
    print("Available tools:")
    print("get_user_input - Get text/number input from user")
    print("get_user_choice - Let user choose from options")
    print("get_multiline_input - Get multi-line text from user")
    print("show_confirmation_dialog - Ask user for yes/no confirmation")
    print("show_info_message - Display information to user")
    print("get_human_loop_prompt - Get guidance on when to use human-in-the-loop tools")
    print("health_check - Check server status")
    print("toggle_whispr - Enable/disable voice transcription (Whispr)")
    if is_telegram_enabled():
        print("Telegram transport: ENABLED (get_multiline_input will send+await via Telegram)")
        # ── Session coordination ──
        _session_coordinator = SessionCoordinator()
        num = _session_coordinator.register()
        atexit.register(_session_coordinator.deregister)
        tag = _session_coordinator.format_tag()
        print(f"Session registered: {tag} (PID {os.getpid()})")
        sessions = _session_coordinator.get_active_sessions()
        if len(sessions) > 1:
            print(f"Active sessions: {len(sessions)}")
            for s in sessions:
                print(f"  {s['icon']} #{s['number']} · {s['workspace']} (PID {s['pid']})")
    else:
        print("Telegram transport: DISABLED (set HITL_TELEGRAM_BOT_TOKEN and HITL_TELEGRAM_CHAT_ID)")

    # ── Whispr status ──
    if _WHISPR_IMPORTED and whispr_is_available():
        cfg = whispr_get_config()
        state = "ENABLED" if cfg.enabled else "DISABLED"
        lang  = cfg.language or "auto"
        print(f"Whispr voice transcription: {state} (model: {cfg.model}, lang: {lang})")
        print("  Toggle in Telegram: /whispr on | /whispr off")
    else:
        print("Whispr voice transcription: NOT AVAILABLE (install faster-whisper to enable)")

    print("")
    
    # Platform-specific startup messages
    if IS_MACOS:
        print("macOS detected - Using native system fonts and window management")
        print("Note: You may need to allow Python to control your computer in System Preferences > Security & Privacy > Accessibility")
    elif IS_WINDOWS:
        print("Windows detected - Using modern Windows 11-style GUI with enhanced styling")
        print("Features: Modern colors, improved fonts, hover effects, and sleek design")
    elif IS_LINUX:
        print("Linux detected - Using Linux-compatible GUI settings with modern styling")
    
    # Test GUI availability
    if ensure_gui_initialized():
        print(" GUI system initialized successfully")
        if IS_MACOS:
            print(" macOS GUI optimizations applied")
    else:
        print(" Warning: GUI system may not be available")
    
    print("")
    print("Starting MCP server...")
    
    # Run the server
    mcp.run()

if __name__ == "__main__":
    main()