# HITL MCP Server — Stale Session Fix + Image Verification Prompt

> **Scenario:** B (Code Building) — Bug fix + feature verification in existing codebase
> **Agent autonomy:** HIGH — self-test between phases, proceed without asking
> **Scope:** Fix stale session detection causing phantom button accumulation; verify image pipeline end-to-end

---

## Role

```xml
<role>
You are a senior Python developer specializing in MCP (Model Context Protocol) servers, Telegram Bot API, and Windows system programming.
You are fixing bugs in an existing, functioning HITL MCP server. The server works — you are improving its reliability.
You work in autonomous phases — you fix, self-test, and proceed without waiting for user input.
</role>
```

---

## Context

```xml
<context>

<project>
Name: Human-in-the-Loop MCP Server
Location: D:\0000_AI VAULT\Coding\MCP\hitl-mcp-server
Main file: hitl_mcp_server.py (3,850 lines)
Type: FastMCP server providing Telegram-based human input tools for AI agents
Repo: theohero/hitl-mcp-server (GitHub, main branch)
</project>

<stack>
- Python 3.12+
- FastMCP 2.8.1+ (MCP server framework)
- Telegram Bot API (via urllib, no library)
- Zustand-like session coordination (file-based, ~/.hitl-mcp/)
- Optional: RapidOCR, Pillow, pygetwindow, pyautogui, faster-whisper
</stack>

<architecture>
The server provides these Telegram-enabled tools:
- get_multiline_input — Main tool. Sends prompt to Telegram, waits for reply (text, photo, voice, document)
- get_user_input / get_user_choice / show_confirmation_dialog — GUI-only (tkinter), NOT Telegram
- get_window_screenshot — Captures window screenshots via Win32 API
- get_image / list_images — Local filesystem image tools
- health_check — Server status check

Multi-instance coordination via SessionCoordinator class:
- Each VS Code window registers a session in ~/.hitl-mcp/sessions.json
- Sessions have: session_id, number (1-9), workspace name, PID, start time
- Inline keyboard buttons appear when >1 sessions are active (for switching)
- Only one instance polls Telegram at a time (file-lock based)
- Messages are routed by: reply_to_message_id → /r{n} command → active context (button tap) → single-session fallback → disambiguation prompt
</architecture>

</context>
```

---

## Bug Report

```xml
<bugs>

<bug id="1" severity="HIGH" name="Stale sessions cause phantom button accumulation">
  <symptoms>
    User has 1 active VS Code window/session, but Telegram messages show
    inline keyboard buttons for 2-3+ sessions. Each new get_multiline_input
    call adds another message with buttons. Old messages retain their stale buttons.
    Buttons accumulate across the chat history.
  </symptoms>
  <root_cause>
    _is_pid_alive() on Windows checks if ANY process has the given PID, not
    whether it's specifically a Python/HITL process. Windows recycles PIDs
    aggressively — when an MCP process crashes without clean exit (atexit
    doesn't fire), its session stays in sessions.json. If another process
    (Chrome, explorer, etc.) inherits that PID, _is_pid_alive returns True
    and the stale session persists.

    _cleanup_stale() depends on _is_pid_alive() — if it returns True for
    recycled PIDs, stale sessions are never removed.

    build_inline_keyboard() returns buttons whenever sessions > 1, so stale
    sessions cause buttons on every prompt message.
  </root_cause>
  <location>
    hitl_mcp_server.py lines 200-213 (_is_pid_alive)
    hitl_mcp_server.py lines 230-235 (_cleanup_stale)
  </location>
</bug>

<bug id="2" severity="MEDIUM" name="Image pipeline may not work for all MCP hosts">
  <symptoms>
    Some users report that sending photos to the Telegram bot doesn't result
    in the agent receiving the image. Need to verify the entire image pipeline
    works correctly and add better error reporting.
  </symptoms>
  <investigation_needed>
    1. Verify photo handling flow: photo received → download → base64 encode → JSON → routing → return as [TextContent, ImageContent]
    2. Verify document-with-image-MIME handling
    3. Check error handling completeness
    4. Verify the image payload isn't too large for MCP transport
    5. Check if there's a fallback for MCP hosts that don't support ImageContent
  </investigation_needed>
  <location>
    hitl_mcp_server.py lines 1510-1600 (photo handling in polling loop)
    hitl_mcp_server.py lines 569-595 (_telegram_download_photo)
    hitl_mcp_server.py lines 3040-3100 (get_multiline_input image return)
  </location>
</bug>

</bugs>
```

---

## Current Code Reference

```xml
<current_code>

<!-- _is_pid_alive — THE BUG (lines 200-213) -->
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

<!-- _cleanup_stale (lines 230-235) -->
def _cleanup_stale(self, data: Dict[str, Any]) -> Dict[str, Any]:
    sessions = data.get("sessions", {})
    data["sessions"] = {
        sid: info for sid, info in sessions.items()
        if self._is_pid_alive(info.get("pid", 0))
    }
    return data

<!-- register (lines 237-260) — saves session with number, workspace, pid, started -->
<!-- deregister (lines 262-280) — removes session, cleans response file, releases poll lock -->

<!-- build_inline_keyboard (lines 310-325) -->
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

<!-- Session file format (~/.hitl-mcp/sessions.json) -->
{
  "sessions": {
    "s1_31760": {
      "number": 1,
      "workspace": "Main Vault",
      "pid": 31760,
      "started": 1772504942.62
    }
  }
}

<!-- Photo handling (lines 1510-1555) — inside the polling while loop -->
# ── Photo message ──
elif msg.get("photo"):
    try:
        photo_sizes = msg["photo"]
        best_photo = photo_sizes[-1]
        file_id = best_photo["file_id"]
        caption = msg.get("caption", "")
        # ... download, base64, OCR, build JSON payload ...
        text = json.dumps({"__image__": True, ...})
        # Fall through to normal routing below
    except Exception as photo_err:
        _telegram_api_call("sendMessage", {"chat_id": chat_id, "text": f"❌ Failed: {photo_err}"})
        continue

<!-- Image return in get_multiline_input (lines 3040-3100) -->
if image_payload:
    return [
        TextContent(type="text", text=json.dumps(metadata)),
        ImageContent(type="image", data=image_payload["image_b64"], mimeType=...),
    ]

</current_code>
```

---

## Task

```xml
<task>
Fix the stale session detection bug and verify the image pipeline.

PHASE 1: Fix _is_pid_alive (stale session root cause)
PHASE 2: Improve session cleanup robustness
PHASE 3: Verify + harden image pipeline
PHASE 4: Test everything end-to-end

Do NOT edit old Telegram messages or remove their keyboards — that's expected Telegram behavior.
Do NOT add session expiry/timeout — sessions should persist as long as VS Code is open.
The ONLY criteria for session liveness: the process is a running Python HITL server.
</task>
```

---

## Phases

```xml
<phases>

<phase id="1" name="Fix _is_pid_alive" autonomous="true">
  <scope>
    Fix the root cause: _is_pid_alive must verify the process is Python, not just that it exists.
    1 method modified.
  </scope>
  <tasks>
    Replace _is_pid_alive with a version that:

    On Windows:
    1. Opens the process with PROCESS_QUERY_LIMITED_INFORMATION (0x1000)
    2. Uses QueryFullProcessImageNameW to get the executable path
    3. Checks if the basename is python.exe, python3.exe, or pythonw.exe
    4. If the process doesn't exist → return False
    5. If the process exists but isn't Python → return False (recycled PID)
    6. If QueryFullProcessImageNameW fails (access denied) → fall back to checking if the process
       has been alive since the session's "started" time using GetProcessTimes, OR check
       using wmic/tasklist as a fallback, OR if all fails, assume alive (same as current behavior)

    On macOS/Linux:
    1. Keep the existing os.kill(pid, 0) check
    2. ADDITIONALLY read /proc/{pid}/comm (Linux) or use ps (macOS) to verify it's python
    3. If can't determine → assume alive (same fallback)

    IMPORTANT: The fix must be robust. If we can't determine the process name (e.g., permission
    denied), we should fall back to the current behavior (assume alive) rather than falsely
    killing active sessions.
  </tasks>
  <self_test>
    Run: python -c "from hitl_mcp_server import SessionCoordinator; c = SessionCoordinator(); print(c._is_pid_alive(os.getpid()))"
    Must return True (our own process IS Python).
    Test with a non-Python PID (e.g., explorer.exe PID):
    Run: python -c "import subprocess; p = subprocess.Popen(['cmd', '/c', 'timeout', '5']); print(c._is_pid_alive(p.pid))"
    Should return False (cmd.exe is not Python).
    Proceed.
  </self_test>
</phase>

<phase id="2" name="Improve Session Cleanup Robustness" autonomous="true">
  <scope>
    Make _cleanup_stale more robust. Add a heartbeat mechanism so sessions prove they're
    still active, without using a time-based expiry.
    2-3 methods modified.
  </scope>
  <tasks>
    1. Add a "last_seen" field to session registration:
       When register() creates the session entry, include "last_seen": time.time()

    2. Add a touch_session() method:
       Updates "last_seen" to the current time. Called by the poller on each poll cycle.
       This proves the session's MCP server is alive and actively polling.

    3. Update _cleanup_stale to use BOTH checks:
       - PID must be alive AND be a Python process (from Phase 1)
       - If the PID check is inconclusive (can't determine process name), use last_seen:
         if last_seen is more than 10 minutes old AND the PID's process name can't be
         confirmed as Python, THEN remove the stale session.
       - This gives a generous 10-minute window for edge cases, but catches long-dead sessions.

    4. Call touch_session() in the polling loop:
       Inside the poller path of _send_and_wait_telegram_multiline_input, after processing
       each batch of updates, call coord.touch_session() to update last_seen.

    NOTE: This is NOT a timeout-based expiry. Active sessions update last_seen continuously.
    Only sessions whose PID is suspicious AND haven't been seen in 10 minutes are removed.
    A session that's open in VS Code and waiting for input will continuously update via polling.
  </tasks>
  <self_test>
    Run: python -c "from hitl_mcp_server import SessionCoordinator; c = SessionCoordinator(); c.register(); print(c._read_sessions())"
    Verify: session entry includes "last_seen" field.
    Proceed.
  </self_test>
</phase>

<phase id="3" name="Verify + Harden Image Pipeline" autonomous="true">
  <scope>
    Read through the entire image pipeline and fix any issues.
    0-3 modifications depending on findings.
  </scope>
  <tasks>
    Trace the FULL image flow and verify each step:

    1. Photo received in polling loop (line ~1510):
       - msg.get("photo") detected
       - Last PhotoSize selected (largest)
       - file_id extracted
       - Verify: chat_id check happens BEFORE photo handling (line ~1477)

    2. Photo download via _telegram_download_photo:
       - getFile API call → file_path
       - File bytes downloaded via HTTPS
       - MIME type determined from extension
       - Verify: timeout is sufficient (currently 60s)
       - Verify: error messages are descriptive

    3. OCR extraction (optional):
       - _extract_ocr_from_image_bytes called
       - Returns dict with ocr_text, ocr_lines, etc.
       - Verify: OCR failure doesn't block image return

    4. JSON payload construction:
       - __image__: True flag set
       - image_b64 is base64-encoded
       - Dimensions from PhotoSize
       - Verify: JSON is well-formed
       - POTENTIAL ISSUE: Very large images → huge base64 string → huge JSON
         Check: Telegram bot API has 20MB file size limit. Base64 expands by ~33%.
         A 20MB image → ~27MB base64 → ~27MB JSON string.
         This could cause issues with MCP transport.
         ADD: A size check. If image > 10MB, resize before encoding.
         Use Pillow if available, otherwise skip resize and warn.

    5. Routing (falls through to normal text routing):
       - Reply-to-message → message map lookup
       - Active context → button tap session
       - Single session → direct return
       - Multi session → disambiguation
       - Verify: image JSON payload is NOT parsed by /whispr, /help, /sessions
         commands (it won't match those — JSON starts with "{", not "/")

    6. Return in get_multiline_input:
       - Parsed as JSON with __image__ flag
       - Returns [TextContent(metadata), ImageContent(base64)]
       - Verify: metadata is complete (has_image, dimensions, OCR fields, etc.)
       - ADD: Fallback for MCP hosts that don't support ImageContent:
         If return of mixed content fails, fall back to TextContent-only
         with the image_b64 included in the metadata JSON (so the host can
         still access it even if it can't render inline).
         ACTUALLY — we can't detect this at the MCP server level. The MCP
         transport will either pass through the ImageContent or drop it.
         But we CAN add a note in the metadata about what's happening.

    7. ADD better logging:
       - Log the image file size and dimensions when received
       - Log if OCR is enabled/available
       - Log the total payload size being returned

    8. CHECK: Document-with-image-MIME handling (line ~1560):
       - Same flow as photo, but for file attachments with image MIME type
       - Verify: no differences in error handling
       - NOTE: width/height are None for documents (Telegram doesn't provide them)
       - This is fine — but add a log note
  </tasks>
  <self_test>
    Review all changes.
    Run: python -c "import hitl_mcp_server" — must import without errors.
    Check: no syntax errors, all functions defined.
    Proceed.
  </self_test>
</phase>

<phase id="4" name="Test & Push" autonomous="true">
  <scope>
    Full verification. Push to GitHub.
  </scope>
  <tasks>
    1. Run: python -c "import hitl_mcp_server; print('Import OK')"
       Must succeed.

    2. Run: python -c "
       from hitl_mcp_server import SessionCoordinator
       import os
       c = SessionCoordinator()
       # Test 1: own PID is Python
       assert c._is_pid_alive(os.getpid()) == True, 'Own PID should be alive'
       # Test 2: PID 0 is not alive
       assert c._is_pid_alive(0) == False, 'PID 0 should not be alive'
       # Test 3: PID 99999 is probably not alive
       assert c._is_pid_alive(99999) == False, 'PID 99999 should not be alive'
       print('All PID tests passed')
       "

    3. Run: python -c "
       from hitl_mcp_server import SessionCoordinator
       c = SessionCoordinator()
       num = c.register()
       sessions = c.get_active_sessions()
       print(f'Registered as #{num}, active sessions: {len(sessions)}')
       c.deregister()
       print('Deregistered OK')
       "

    4. Review the diff: git diff
       Verify no unintended changes.

    5. Commit: git add -A && git commit -m "fix: robust stale session cleanup + image pipeline hardening"

    6. Push: git push origin main

    7. Report: list all changes with line numbers, what each change does.
  </tasks>
  <self_test>
    Full self-audit:
    - List every function modified
    - Python import test — pass/fail
    - PID validation tests — pass/fail
    - Session lifecycle test — pass/fail
    - Git push status
    - Summary of image pipeline findings
  </self_test>
</phase>

</phases>
```

---

## Constraints

```xml
<constraints>
- Modify ONLY hitl_mcp_server.py — no other files
- Do NOT change the session file format in a breaking way (add fields, don't remove)
- Do NOT add session timeout/expiry — sessions live as long as VS Code is open
- Do NOT edit old Telegram messages or remove their keyboards
- Do NOT change the public MCP tool API (function signatures, return types)
- Do NOT add new dependencies — use only stdlib + what's already imported
- Do NOT break the multi-instance coordination — multiple sessions MUST still work
- The PID check fallback must be SAFE: if you can't determine the process name,
  assume alive (don't kill active sessions)
- Keep backward compatibility with existing sessions.json (old entries without "last_seen"
  should still work — treat missing last_seen as "very old")
- Test on Windows (primary platform) but don't break macOS/Linux
</constraints>
```

---

## Anti-Goals

```xml
<anti_goals>
- Do NOT rewrite the SessionCoordinator class — fix the specific methods
- Do NOT change the Telegram message format or layout
- Do NOT add a session heartbeat daemon/thread — use the existing polling loop
- Do NOT change how buttons are built (build_inline_keyboard is correct — the
  input to it (active sessions) is wrong)
- Do NOT modify get_user_input, get_user_choice, or show_confirmation_dialog
- Do NOT add external dependencies (psutil, etc.) — use ctypes/subprocess
</anti_goals>
```

---

## Self-Audit Template

```xml
<self_audit_at_end>
After Phase 4, report:

1. FILES MODIFIED — hitl_mcp_server.py (list methods changed)
2. BUG #1 FIX — _is_pid_alive now checks process name. How?
3. BUG #2 STATUS — Image pipeline findings. Any fixes applied?
4. TEST RESULTS — Import test, PID tests, session lifecycle test
5. BACKWARD COMPAT — Can old sessions.json (without last_seen) be read? (Y/N)
6. PLATFORM COMPAT — Windows fix verified? macOS/Linux code preserved? (Y/N)
7. GIT — Commit message, push status
8. REMAINING CONCERNS — Anything still suspicious in the image pipeline?
</self_audit_at_end>
```

---

*Prompt built following [PROMPT_BUILDING_GUIDE.md](../../PROMPT_BUILDING_GUIDE.md) — Scenario B (Code Building) with bug-fix focus.*
