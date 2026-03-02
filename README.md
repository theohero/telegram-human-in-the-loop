# 🤖💬 Telegram Human-in-the-Loop MCP Server

> **Let your AI talk to you through Telegram.** This MCP server gives any AI coding assistant the ability to pause, ask you questions, and wait for your reply — right in your Telegram chat. Now with **image support**, **window screenshots**, **OCR**, and **voice message transcription**.

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/MCP-stdio-green" />
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white" />
  <img src="https://img.shields.io/badge/Windows-✅-0078D4?logo=windows" />
  <img src="https://img.shields.io/badge/macOS-✅-000?logo=apple" />
  <img src="https://img.shields.io/badge/Linux-✅-FCC624?logo=linux&logoColor=black" />
</p>

---

## 📌 What is this?

When an AI agent works on your code, it sometimes needs to ask you a question — "What should I name this?", "Which approach do you prefer?", "Is this the right file?"

This server intercepts those questions and **sends them to your Telegram**. You reply on your phone (or desktop Telegram), and the AI continues working with your answer.

**No browser tabs. No terminal switching. Just Telegram.**

---

## ✨ Key Features

### 💬 Bidirectional Text Communication
- AI sends prompts to your Telegram chat
- You reply with text — AI receives it and continues working
- Supports multi-line input, choices, confirmations, and info messages

### 📸 Image Support (Telegram → AI)
- **Send photos** from your phone/desktop directly to the AI
- Images are forwarded as MCP `ImageContent` — the AI can "see" what you send
- Includes **OCR extraction** — text in photos is automatically extracted
- Perfect for: sharing error screenshots, UI mockups, diagrams, handwritten notes

### 🖥️ Window Screenshots (AI → Telegram)
- AI can capture screenshots of any window by title
- **Win32 PrintWindow API** — works even for minimized or occluded windows
- Automatic **OCR text extraction** from captured screenshots
- Results returned as image + metadata (dimensions, OCR text, confidence)

### 🎤 Voice Message Transcription (Whispr)
- Send **voice messages** in Telegram — they are transcribed to text automatically
- Uses a local Whisper model (Whispr module) for privacy
- Transcribed text is sent to the AI as a regular text response
- Edit tracking: shows original transcription plus any edits applied

### 🔄 Fallback System
- If Telegram is not configured, all tools fall back to **native GUI popups** (tkinter)
- Works offline with local dialog boxes
- Graceful degradation — never blocks the AI

---

## 🧩 Compatible Platforms

| Platform | Status | Config location |
|----------|--------|-----------------|
| **VS Code (GitHub Copilot Agent Mode)** | ✅ Fully tested | User `mcp.json` or workspace `.vscode/mcp.json` |
| **VS Code (Copilot Chat)** | ✅ Works | Same as above |
| **Claude Code (CLI)** | ✅ Compatible | `~/.claude/mcp.json` |
| **Claude Desktop** | ✅ Compatible | See config examples below |
| **Cursor** | ✅ Compatible | `.cursor/mcp.json` in workspace |
| **Windsurf (Codeium)** | ✅ Compatible | `~/.codeium/windsurf/mcp_config.json` |
| **Cline** | ✅ Compatible | Via Cline MCP settings UI |
| **Any MCP stdio client** | ✅ Compatible | Pass `python hitl_mcp_server_custom.py` as command |

> The server uses the standard **MCP stdio** transport — it works with **any client** that supports MCP.

---

## 🛠 Available Tools

| Tool | Description |
|------|-------------|
| `get_multiline_input` | Send a prompt to Telegram, wait for the user's reply. Supports **text**, **photos** (returned as ImageContent + OCR), and **voice messages** (Whispr transcription). Main communication tool. |
| `get_user_input` | Simple text/number input dialog |
| `get_user_choice` | Multiple choice selection dialog |
| `get_window_screenshot` | Capture a screenshot of any window by title. Uses Win32 PrintWindow (works minimized). Returns image + OCR metadata. Windows only. |
| `show_confirmation_dialog` | Yes/No confirmation dialog |
| `show_info_message` | Display information to the user |
| `toggle_whispr` | Enable/disable voice message transcription module |
| `health_check` | Check server status, Telegram connectivity, available features |

---

## 🔧 How it Works

```
┌──────────────┐     MCP stdio      ┌───────────────┐    Telegram API    ┌───────────┐
│  AI Agent    │ ◄────────────────► │  HITL Server  │ ◄────────────────► │ You       │
│ (Copilot,    │   tool calls +     │  (Python)     │   send message     │ Telegram  │
│  Claude...)  │   results          │               │   wait for reply   │           │
└──────────────┘                    └───────────────┘                    └───────────┘
```

1. The AI calls `get_multiline_input` (or another tool)
2. The server sends the prompt to your **Telegram chat** via Bot API
3. You read the message on your phone/desktop and **reply** (text, photo, or voice)
4. The server captures your reply and returns it to the AI
5. The AI continues working with your input

### Image Flow
```
You (Telegram)                    HITL Server                     AI Agent
     │                                │                               │
     │── Send photo ──────────────────►│                               │
     │                                │── Download image               │
     │                                │── Run OCR extraction           │
     │                                │── Return ImageContent ────────►│
     │                                │   + OCR text metadata          │
     │                                │                               │── AI "sees" the image
```

### Window Screenshot Flow
```
AI Agent                          HITL Server                     AI Agent
     │                                │                               │
     │── get_window_screenshot ──────►│                               │
     │   (window_title="Settings")   │── Find window by title        │
     │                                │── Win32 PrintWindow capture   │
     │                                │── Run OCR on screenshot       │
     │                                │── Return image + metadata ───►│
     │                                │                               │── AI processes screenshot
```

**Fallback:** If Telegram is not configured, all tools fall back to native **GUI popups** (tkinter).

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- A Telegram Bot token (create via [@BotFather](https://t.me/BotFather))
- Your Telegram Chat ID

### Installation

```bash
git clone https://github.com/theohero/telegram-human-in-the-loop.git
cd telegram-human-in-the-loop
git checkout main
pip install fastmcp pydantic
```

**Optional dependencies** (for full feature set):
```bash
pip install pygetwindow pyautogui Pillow numpy rapidocr-onnxruntime
```

| Package | Required for |
|---------|-------------|
| `pygetwindow` | Window screenshot capture |
| `pyautogui` | Fallback screenshot method |
| `Pillow` | Image processing |
| `numpy` | OCR support |
| `rapidocr-onnxruntime` | OCR text extraction from images |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Your Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | Yes | Your personal Telegram Chat ID |
| `HITL_TELEGRAM_TIMEOUT_SECONDS` | No | Timeout for waiting for reply (default: 3600) |

### VS Code Configuration (GitHub Copilot)

Add to your `.vscode/mcp.json` or user `mcp.json`:

```json
{
  "servers": {
    "hitl-mcp-server": {
      "type": "stdio",
      "command": "python",
      "args": ["path/to/hitl_mcp_server_custom.py"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "your-bot-token-here",
        "TELEGRAM_CHAT_ID": "your-chat-id-here"
      }
    }
  }
}
```

### Claude Desktop Configuration

Add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "hitl-mcp-server": {
      "command": "python",
      "args": ["path/to/hitl_mcp_server_custom.py"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "your-bot-token-here",
        "TELEGRAM_CHAT_ID": "your-chat-id-here"
      }
    }
  }
}
```

### Claude Code (CLI)

```json
{
  "mcpServers": {
    "hitl-mcp-server": {
      "command": "python",
      "args": ["path/to/hitl_mcp_server_custom.py"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "your-bot-token-here",
        "TELEGRAM_CHAT_ID": "your-chat-id-here"
      }
    }
  }
}
```

---

## 🔍 Finding Your Chat ID

1. Open Telegram and search for **@userinfobot**
2. Send it any message — it replies with your Chat ID
3. Copy the number (e.g., `123456789`)

**Alternative:** Open [https://web.telegram.org](https://web.telegram.org), go to any chat, and look at the URL — the number after `#` is your Chat ID.

---

## 📸 Image Capabilities in Detail

### Receiving Images from Telegram

When you send a photo in Telegram as a reply to the AI's prompt:

1. The server downloads the highest-resolution version of the photo
2. Runs **OCR** (Optical Character Recognition) to extract any text in the image
3. Returns **mixed MCP content**: `TextContent` (metadata + OCR text) + `ImageContent` (the actual image)
4. The AI receives both the image and any extracted text

**Response metadata includes:**
```json
{
  "success": true,
  "user_input": "caption or OCR text",
  "has_image": true,
  "image_width": 1920,
  "image_height": 1080,
  "image_file_size": 245000,
  "image_mime_type": "image/jpeg",
  "ocr_enabled": true,
  "ocr_text": "extracted text from the image",
  "ocr_lines": ["line 1", "line 2"],
  "ocr_avg_confidence": 0.95
}
```

### Window Screenshots

The `get_window_screenshot` tool captures any window by title:

```python
# AI calls this tool with:
get_window_screenshot(window_title_contains="Settings", max_size=1400)
```

**Capture strategy:**
1. **Primary: Win32 PrintWindow API** — works even for minimized or occluded windows (Windows only)
2. **Fallback: pyautogui region capture** — requires the window to be visible

The screenshot is returned as:
- `TextContent` with metadata (title, dimensions, OCR text)
- `ImageContent` with the actual screenshot image

---

## 🎤 Voice Message Support (Whispr)

The server includes an optional **Whispr** module for local voice transcription:

1. You send a voice message in Telegram
2. Whispr downloads and transcribes it locally using a Whisper model
3. The transcribed text is sent to the AI as a regular text response
4. Metadata includes original transcription and any LLM-powered edits

Toggle Whispr with the `toggle_whispr` tool. Requires a local Whisper model to be configured.

---

## ❓ FAQ

**Q: Do I need all the optional dependencies?**
A: No. The core functionality (text chat via Telegram) only needs `fastmcp` and `pydantic`. Install optional packages only for the features you want.

**Q: Does it work without Telegram?**
A: Yes! All tools fall back to native GUI popup dialogs (tkinter) when Telegram is not configured.

**Q: Can the AI see my photos?**
A: Only photos you explicitly send as replies to the AI's prompts. The server does not access your Telegram in any other way.

**Q: Is OCR always enabled?**
A: OCR runs automatically if `rapidocr-onnxruntime`, `numpy`, and `Pillow` are installed. If not installed, images are still forwarded but without OCR text extraction.

**Q: Does it work on macOS/Linux?**
A: Text communication and image support work everywhere. Window screenshots (`get_window_screenshot`) currently use Win32 APIs and only work on Windows.

---

## 🌐 Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | — | Your Telegram Chat ID |
| `HITL_TELEGRAM_TIMEOUT_SECONDS` | `3600` | How long to wait for a reply (seconds) |
| `FASTMCP_LOG_LEVEL` | `INFO` | Logging level |

---

## 📝 License

MIT License. See [LICENSE](LICENSE) for details.

---

## 🙏 Credits

Built with [FastMCP](https://github.com/jlowin/fastmcp) and the [Telegram Bot API](https://core.telegram.org/bots/api).
