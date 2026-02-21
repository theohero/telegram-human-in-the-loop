# 🤖💬 Telegram Human-in-the-Loop MCP Server

> **Let your AI talk to you through Telegram.** This MCP server gives any AI coding assistant the ability to pause, ask you questions, and wait for your reply — right in your Telegram chat.

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/MCP-stdio-green" />
  <img src="https://img.shields.io/badge/Telegram-Bot%20API-26A5E4?logo=telegram&logoColor=white" />
  <img src="https://img.shields.io/badge/Windows-✅-0078D4?logo=windows" />
  <img src="https://img.shields.io/badge/macOS-✅-000?logo=apple" />
  <img src="https://img.shields.io/badge/Linux-✅-FCC624?logo=linux&logoColor=black" />
</p>

---

## 📖 What is this?

When an AI agent works on your code, it sometimes needs to ask you a question — "What should I name this?", "Which approach do you prefer?", "Is this the right file?"

This server intercepts those questions and **sends them to your Telegram**. You reply on your phone (or desktop Telegram), and the AI continues working with your answer.

**No browser tabs. No terminal switching. Just Telegram.**

---

## 🧩 Compatible Platforms / Совместимые платформы

| Platform | Status | Config location |
|----------|--------|-----------------|
| **VS Code (GitHub Copilot Agent Mode)** | ✅ Fully tested | User `mcp.json` or workspace `.vscode/mcp.json` |
| **VS Code (Copilot Chat)** | ✅ Works | Same as above |
| **Claude Code (CLI)** | ✅ Compatible | `~/.claude/mcp.json` |
| **Claude Desktop** | ✅ Compatible | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows) |
| **Cursor** | ✅ Compatible | `.cursor/mcp.json` in workspace |
| **Windsurf (Codeium)** | ✅ Compatible | `~/.codeium/windsurf/mcp_config.json` |
| **Cline** | ✅ Compatible | Via Cline MCP settings UI |
| **Any MCP stdio client** | ✅ Compatible | Pass `python hitl_mcp_server.py` as command |

> The server uses the standard **MCP stdio** transport — it works with **any client** that supports MCP.

---

## 🛠 Available Tools

| Tool | Description |
|------|-------------|
| `get_multiline_input` | Send a message to Telegram, wait for your reply (main tool) |
| `get_user_input` | Simple text/number input dialog (GUI fallback) |
| `get_user_choice` | Multiple choice selection dialog |
| `show_confirmation_dialog` | Yes/No confirmation |
| `show_info_message` | Display information to the user |
| `health_check` | Check server status, Telegram connectivity |

---

## ⚡ Quick Install / Быстрая установка

### Prerequisites / Предварительные требования

<details>
<summary>🇬🇧 English</summary>

You need **Python 3.10+** and **uv** (fast Python package runner).

**1. Install Python** (if not already installed):
```bash
# Windows — download from https://www.python.org/downloads/
# Or use winget:
winget install Python.Python.3.12

# macOS:
brew install python@3.12

# Linux (Ubuntu/Debian):
sudo apt update && sudo apt install python3 python3-pip
```

**2. Install uv** (recommended — handles dependencies automatically):
```bash
# Windows (PowerShell):
irm https://astral.sh/uv/install.ps1 | iex

# macOS / Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installing, **restart your terminal** so `uv` is on your PATH.

</details>

<details>
<summary>🇷🇺 Русский</summary>

Нужен **Python 3.10+** и **uv** (быстрый менеджер пакетов Python).

**1. Установите Python** (если ещё не установлен):
```bash
# Windows — скачайте с https://www.python.org/downloads/
# Или через winget:
winget install Python.Python.3.12

# macOS:
brew install python@3.12

# Linux (Ubuntu/Debian):
sudo apt update && sudo apt install python3 python3-pip
```

**2. Установите uv** (рекомендуется — автоматически управляет зависимостями):
```bash
# Windows (PowerShell):
irm https://astral.sh/uv/install.ps1 | iex

# macOS / Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

После установки **перезапустите терминал**, чтобы `uv` был доступен.

</details>

---

### 🤖 Step 1: Create your Telegram Bot / Создайте Telegram-бота

<details>
<summary>🇬🇧 English</summary>

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. "My HITL Bot")
4. Choose a username (e.g. `my_hitl_bot`)
5. **Copy the API token** — you'll need it (looks like `123456789:ABCdefGHI...`)
6. Open a chat with your new bot, send any message (e.g. "hello") — this activates the chat
7. Get your **Chat ID**: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser, find `"chat":{"id":123456789}` — that number is your Chat ID

</details>

<details>
<summary>🇷🇺 Русский</summary>

1. Откройте Telegram и найдите **@BotFather**
2. Отправьте `/newbot`
3. Выберите имя (например, "My HITL Bot")
4. Выберите username (например, `my_hitl_bot`)
5. **Скопируйте API-токен** — он понадобится (выглядит как `123456789:ABCdefGHI...`)
6. Откройте чат с вашим новым ботом, отправьте любое сообщение (например, "привет") — это активирует чат
7. Узнайте свой **Chat ID**: откройте в браузере `https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates`, найдите `"chat":{"id":123456789}` — это число и есть ваш Chat ID

</details>

---

### 📋 Step 2: Configure your MCP client / Настройте MCP-клиент

#### VS Code (GitHub Copilot) — Recommended / Рекомендуется

<details>
<summary>🇬🇧 English</summary>

Open your VS Code User Settings JSON and add the MCP server config.

**Option A: User-level** (works across all projects)

File: `%APPDATA%\Code\User\mcp.json` (Windows) or `~/.config/Code/User/mcp.json` (Linux/macOS)

```json
{
  "servers": {
    "hitl-mcp-server": {
      "command": "uv",
      "args": [
        "run",
        "--with", "fastmcp>=2.8.1",
        "--with", "pydantic>=2.0.0",
        "python",
        "C:\\path\\to\\hitl_mcp_server.py"
      ],
      "env": {
        "HITL_TELEGRAM_BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
        "HITL_TELEGRAM_CHAT_ID": "YOUR_CHAT_ID_HERE",
        "HITL_TELEGRAM_TIMEOUT_SECONDS": "86400"
      },
      "type": "stdio"
    }
  }
}
```

> ⚠️ Replace the path and tokens with your own values. Use `\\` for Windows paths in JSON.

**Option B: Workspace-level** (per-project)

Create `.vscode/mcp.json` in your project root with the same content.

</details>

<details>
<summary>🇷🇺 Русский</summary>

Откройте настройки VS Code User Settings JSON и добавьте конфигурацию MCP-сервера.

**Вариант A: На уровне пользователя** (работает во всех проектах)

Файл: `%APPDATA%\Code\User\mcp.json` (Windows) или `~/.config/Code/User/mcp.json` (Linux/macOS)

```json
{
  "servers": {
    "hitl-mcp-server": {
      "command": "uv",
      "args": [
        "run",
        "--with", "fastmcp>=2.8.1",
        "--with", "pydantic>=2.0.0",
        "python",
        "C:\\path\\to\\hitl_mcp_server.py"
      ],
      "env": {
        "HITL_TELEGRAM_BOT_TOKEN": "ВАШ_ТОКЕН_БОТА",
        "HITL_TELEGRAM_CHAT_ID": "ВАШ_CHAT_ID",
        "HITL_TELEGRAM_TIMEOUT_SECONDS": "86400"
      },
      "type": "stdio"
    }
  }
}
```

> ⚠️ Замените путь и токены на свои значения. Используйте `\\` для Windows-путей в JSON.

**Вариант B: На уровне проекта**

Создайте `.vscode/mcp.json` в корне проекта с тем же содержимым.

</details>

#### Claude Desktop

```json
{
  "mcpServers": {
    "hitl-mcp-server": {
      "command": "uv",
      "args": [
        "run",
        "--with", "fastmcp>=2.8.1",
        "--with", "pydantic>=2.0.0",
        "python",
        "/path/to/hitl_mcp_server.py"
      ],
      "env": {
        "HITL_TELEGRAM_BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
        "HITL_TELEGRAM_CHAT_ID": "YOUR_CHAT_ID_HERE"
      }
    }
  }
}
```

#### Claude Code (CLI)

```bash
claude mcp add hitl-mcp-server \
  -e HITL_TELEGRAM_BOT_TOKEN=YOUR_TOKEN \
  -e HITL_TELEGRAM_CHAT_ID=YOUR_CHAT_ID \
  -- uv run --with "fastmcp>=2.8.1" --with "pydantic>=2.0.0" python /path/to/hitl_mcp_server.py
```

#### Cursor / Windsurf / Cline

Same JSON structure as VS Code — just place it in the config file your editor expects (see compatibility table above).

---

### 🧪 Step 3: Test it! / Проверьте!

<details>
<summary>🇬🇧 English</summary>

1. Open VS Code → Copilot Chat → **Agent Mode**
2. Click the **Tools** icon (wrench) → enable `hitl-mcp-server`
3. Type: *"Use the get_multiline_input tool to ask me what my favorite color is"*
4. Check your Telegram — you should see the message from your bot!
5. Reply in Telegram → the AI continues with your answer

</details>

<details>
<summary>🇷🇺 Русский</summary>

1. Откройте VS Code → Copilot Chat → **Agent Mode**
2. Нажмите на иконку **инструментов** (гаечный ключ) → включите `hitl-mcp-server`
3. Введите: *"Use the get_multiline_input tool to ask me what my favorite color is"*
4. Проверьте Telegram — вы должны увидеть сообщение от бота!
5. Ответьте в Telegram → ИИ продолжит работу с вашим ответом

</details>

---

## 🔧 How it Works / Как это работает

```
┌─────────────┐     MCP stdio      ┌──────────────┐    Telegram API    ┌──────────┐
│  AI Agent   │ ◄───────────────► │  HITL Server  │ ◄────────────────► │ You 📱   │
│ (Copilot,   │   tool calls +     │  (Python)     │   send message     │ Telegram │
│  Claude...) │   results          │               │   wait for reply   │          │
└─────────────┘                    └──────────────┘                    └──────────┘
```

1. The AI calls `get_multiline_input` (or another tool)
2. The server sends the prompt to your **Telegram chat** via Bot API
3. You read the message on your phone/desktop and **reply**
4. The server captures your reply and returns it to the AI
5. The AI continues working with your input

**Fallback:** If Telegram is not configured (no env vars), all tools fall back to native **GUI popups** (tkinter) — works on Windows, macOS, and Linux.

---

## 🌐 Environment Variables / Переменные окружения

| Variable | Required | Description |
|----------|----------|-------------|
| `HITL_TELEGRAM_BOT_TOKEN` | Yes (for Telegram) | Your Telegram bot token from @BotFather |
| `HITL_TELEGRAM_CHAT_ID` | Yes (for Telegram) | Your Telegram chat ID |
| `HITL_TELEGRAM_TIMEOUT_SECONDS` | No (default: 3600) | How long to wait for your reply (seconds) |

---

## ❓ FAQ

<details>
<summary>Can I use this without Telegram?</summary>

Yes! Without Telegram env vars, all tools show native GUI popup dialogs instead. This works on Windows, macOS, and Linux.
</details>

<details>
<summary>Does it work on headless servers?</summary>

With Telegram — yes! The Telegram transport doesn't need a display. Without Telegram on a headless server, GUI popups will fail (no display).
</details>

<details>
<summary>Can multiple people use the same bot?</summary>

The server only responds to messages from the configured `HITL_TELEGRAM_CHAT_ID`. Each person should create their own bot and use their own chat ID.
</details>

<details>
<summary>Is my bot token safe?</summary>

The token is stored in your local MCP config (mcp.json) and passed as an environment variable. It never leaves your machine. Never commit tokens to git!
</details>

---

## 📝 License

MIT — use it however you want.

---

## 🙏 Credits

Built with [FastMCP](https://github.com/jlowin/fastmcp) and the [Telegram Bot API](https://core.telegram.org/bots/api).

---

<p align="center">
  <b>⭐ Star this repo if you find it useful!</b>
</p>
