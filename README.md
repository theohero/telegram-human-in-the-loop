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

## 🔧 How it Works

```
┌─────────────┐     MCP stdio      ┌──────────────┐    Telegram API    ┌──────────┐
│  AI Agent   │ ◄───────────────►  │  HITL Server │ ◄────────────────► │ You      │
│ (Copilot,   │   tool calls +     │  (Python)    │   send message     │ Telegram │
│  Claude...) │   results          │              │   wait for reply   │          │
└─────────────┘                    └──────────────┘                    └──────────┘
```

1. The AI calls `get_multiline_input` (or another tool)
2. The server sends the prompt to your **Telegram chat** via Bot API
3. You read the message on your phone/desktop and **reply**
4. The server captures your reply and returns it to the AI
5. The AI continues working with your input

**Fallback:** If Telegram is not configured, all tools fall back to native **GUI popups** (tkinter).

---

# 🇬🇧 Installation Guide (English)

This guide assumes you've **never done any of this before**. Follow every step exactly.

---

## Step 1: Install Python

Python is the programming language this server is written in. You need it installed on your computer.

### Windows:
1. Go to **https://www.python.org/downloads/**
2. Click the big yellow **"Download Python 3.x.x"** button
3. Run the downloaded file
4. ⚠️ **IMPORTANT: Check the box that says "Add Python to PATH"** at the bottom of the installer
5. Click **"Install Now"**
6. Wait for it to finish, then close the installer

### macOS:
Open **Terminal** (search for it in Spotlight) and paste:
```bash
brew install python@3.12
```
If you don't have `brew`, first install it by pasting this into Terminal:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Linux (Ubuntu / Debian):
Open a terminal and paste:
```bash
sudo apt update && sudo apt install python3 python3-pip -y
```

### Verify Python works:
Open a terminal/command prompt and type:
```bash
python --version
```
You should see something like `Python 3.12.x`. If you see an error, try `python3 --version` instead.

---

## Step 2: Install uv (package manager)

`uv` is a tool that automatically downloads the right libraries when the server starts. It makes everything easier.

### Windows:
Open **PowerShell** (search for "PowerShell" in the Start menu) and paste:
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```
**Close and re-open PowerShell** after this.

### macOS / Linux:
Open Terminal and paste:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
**Close and re-open Terminal** after this.

### Verify uv works:
```bash
uv --version
```
You should see a version number. If you get an error, you may need to add `uv` to your PATH — on Windows it's usually `%USERPROFILE%\.local\bin`.

---

## Step 3: Install Git

Git is a tool for downloading code from GitHub. We'll use it to download this server.

### Windows:
1. Go to **https://git-scm.com/download/win**
2. The download should start automatically — run the installer
3. Click **"Next"** through all the screens — the default settings are fine
4. On the "Adjusting your PATH" screen, make sure **"Git from the command line and also from 3rd-party software"** is selected (it should be by default)
5. Click **"Install"**, then **"Finish"**
6. **Close and re-open PowerShell**

### macOS:
Open **Terminal** and type:
```bash
git --version
```
If git is not installed, macOS will prompt you to install the Command Line Tools — click **"Install"** and wait.

Alternatively:
```bash
brew install git
```

### Linux (Ubuntu / Debian):
```bash
sudo apt update && sudo apt install git -y
```

### Verify git works:
```bash
git --version
```
You should see something like `git version 2.x.x`.

---

## Step 4: Download this server

### Option A: Using git (recommended)

Open PowerShell (Windows) or Terminal (macOS/Linux) and run these commands one by one:

```bash
cd ~
git clone https://github.com/theohero/telegram-human-in-the-loop.git
```

This creates a folder called `telegram-human-in-the-loop` in your **home directory** (user folder):
- **Windows**: `C:\Users\YourName\telegram-human-in-the-loop\`
- **macOS**: `/Users/YourName/telegram-human-in-the-loop/`
- **Linux**: `/home/YourName/telegram-human-in-the-loop/`

The path to the server file will be:
- **Windows**: `C:\Users\YourName\telegram-human-in-the-loop\hitl_mcp_server.py`
- **macOS/Linux**: `~/telegram-human-in-the-loop/hitl_mcp_server.py`

> 💡 You'll need this path in Step 6.

### Option B: Download as ZIP (if you don't want to use git)
1. Go to **https://github.com/theohero/telegram-human-in-the-loop**
2. Click the green **"< > Code"** button
3. Click **"Download ZIP"**
4. Extract the ZIP to your **home folder** (e.g. `C:\Users\YourName\`)
5. The extracted folder will be called `telegram-human-in-the-loop-master` — you can rename it to `telegram-human-in-the-loop`

---

## Step 5: Create your Telegram Bot

1. Open **Telegram** on your phone or computer
2. Search for **@BotFather** (it has a blue checkmark ✅)
3. Tap **Start**, then send the message: `/newbot`
4. BotFather will ask for a **name** — type anything (e.g. `My AI Assistant`)
5. BotFather will ask for a **username** — it must end in `bot` (e.g. `my_ai_helper_bot`)
6. BotFather will reply with your **API token** — it looks like this:
   ```
   123456789:ABCdefGHIjklMNOpqrSTUvwx
   ```
   **Copy this token and save it somewhere** (a notepad file is fine)

7. Now open a chat with your new bot — search for it by the username you just created
8. **Send any message** to it (like "hello") — this is necessary to activate the chat

### 🔑 Finding your Chat ID

There are two ways to find your personal Telegram Chat ID:

**Method 1: Telegram Web (easiest)**
1. Open **https://web.telegram.org** in your browser
2. Log in to your Telegram account
3. Click on **Saved Messages** (or open any chat with yourself)
4. Look at the **URL in your browser** — it will look like:
   ```
   https://web.telegram.org/k/#777000000
   ```
5. The **number at the end** is your Chat ID (e.g. `777000000`)
6. **Copy this number and save it**

**Method 2: Bot API**
1. Open this URL in your browser (replace `YOUR_TOKEN` with your actual bot token from step 6):
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
2. Look for `"chat":{"id":` followed by a number — that number is your **Chat ID**
3. Example: `"chat":{"id":777000000` → your Chat ID is `777000000`
4. **Copy this number and save it**

> ⚠️ If Method 2 shows empty results, make sure you've sent a message to your bot first (step 8 above), then refresh the page.

---

## Step 6: Configure your code editor

You need to tell your code editor where the server is and give it your Telegram credentials. The MCP configuration file lives in your **user folder**, not in the project.

### VS Code (GitHub Copilot) — Most common setup

1. Open VS Code
2. Press `Ctrl + Shift + P` (Windows/Linux) or `Cmd + Shift + P` (macOS)
3. Type **"MCP: Open User Configuration"** and press Enter
4. A JSON file will open. Paste this content (replace the 3 values marked with ⬅️):

**Windows:**
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
        "C:\\Users\\YourName\\telegram-human-in-the-loop\\hitl_mcp_server.py"
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

**macOS / Linux:**
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
        "/Users/YourName/telegram-human-in-the-loop/hitl_mcp_server.py"
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

⬅️ Replace these three things:
- **`YourName`** — your actual computer username (the folder name under `C:\Users\`)
- **`YOUR_BOT_TOKEN_HERE`** — the bot token from Step 5
- **`YOUR_CHAT_ID_HERE`** — the Chat ID from Step 5

> ⚠️ On **Windows**, use **double backslashes** `\\` in the file path
> On **macOS/Linux**, use forward slashes `/`

5. Save the file (`Ctrl + S`)

### Claude Desktop

File location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

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
        "/path/to/telegram-human-in-the-loop/hitl_mcp_server.py"
      ],
      "env": {
        "HITL_TELEGRAM_BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
        "HITL_TELEGRAM_CHAT_ID": "YOUR_CHAT_ID_HERE"
      }
    }
  }
}
```

### Claude Code (CLI)

```bash
claude mcp add hitl-mcp-server \
  -e HITL_TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE \
  -e HITL_TELEGRAM_CHAT_ID=YOUR_CHAT_ID_HERE \
  -- uv run --with "fastmcp>=2.8.1" --with "pydantic>=2.0.0" python ~/telegram-human-in-the-loop/hitl_mcp_server.py
```

### Cursor / Windsurf / Cline

Same JSON structure as VS Code — just place it in the config file your editor expects (see compatibility table above).

---

## Step 7: Test it!

1. Open **VS Code**
2. Open **Copilot Chat** (click the chat icon or press `Ctrl + Alt + I`)
3. Switch to **Agent Mode** (dropdown at the top of the chat)
4. Click the **Tools** icon (wrench/gear) next to the text box → check `hitl-mcp-server`
5. Type this message:
   > Use the get_multiline_input tool to ask me what my favorite color is
6. **Check your Telegram** — you should get a message from your bot!
7. **Reply in Telegram** with your answer
8. The AI in VS Code will continue using your reply ✅

---

## ❓ FAQ

**Can I use this without Telegram?**
Yes! Without the Telegram env vars, all tools show native GUI popup dialogs instead.

**Does it work on headless servers (no screen)?**
With Telegram — yes! Without Telegram on a headless server, GUI popups will fail.

**Can multiple people use the same bot?**
Each person should create their own bot. The server only responds to the configured Chat ID.

**Is my bot token safe?**
The token stays in your local config file. It never gets uploaded anywhere. Never share your token publicly!

**Where is the MCP config file stored?**
The MCP configuration lives in your **user profile folder**, not in the project. For VS Code on Windows, it's `%APPDATA%\Code\User\mcp.json`. The server script (`.py` file) can be anywhere on your computer — you just point to it in the config.

---

---

# 🇷🇺 Инструкция по установке (Русский)

Эта инструкция написана для тех, кто **никогда раньше ничего подобного не делал**. Следуйте каждому шагу точно.

---

## Что это такое?

Когда ИИ-ассистент (например, GitHub Copilot или Claude) работает с вашим кодом, ему иногда нужно задать вам вопрос — «Как назвать эту переменную?», «Какой подход выбрать?»

Этот сервер перехватывает эти вопросы и **отправляет их вам в Telegram**. Вы отвечаете с телефона или компьютера, и ИИ продолжает работу с вашим ответом.

**Никаких вкладок в браузере. Никакого переключения окон. Просто Telegram.**

---

## Шаг 1: Установите Python

Python — это язык программирования, на котором написан этот сервер. Его нужно установить на ваш компьютер.

### Windows:
1. Перейдите на **https://www.python.org/downloads/**
2. Нажмите большую жёлтую кнопку **"Download Python 3.x.x"**
3. Запустите скачанный файл
4. ⚠️ **ВАЖНО: Поставьте галочку "Add Python to PATH"** в нижней части установщика
5. Нажмите **"Install Now"**
6. Дождитесь окончания установки

### macOS:
Откройте **Терминал** (найдите через Spotlight) и вставьте:
```bash
brew install python@3.12
```
Если у вас нет `brew`, сначала установите его:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Linux (Ubuntu / Debian):
Откройте терминал и вставьте:
```bash
sudo apt update && sudo apt install python3 python3-pip -y
```

### Проверка:
Откройте терминал/командную строку и введите:
```bash
python --version
```
Должно появиться `Python 3.12.x`. Если ошибка — попробуйте `python3 --version`.

---

## Шаг 2: Установите uv (менеджер пакетов)

`uv` — это программа, которая автоматически скачивает нужные библиотеки при запуске сервера.

### Windows:
Откройте **PowerShell** (найдите "PowerShell" в меню Пуск) и вставьте:
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```
**Закройте и откройте PowerShell заново** после установки.

### macOS / Linux:
Откройте Терминал и вставьте:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
**Закройте и откройте Терминал заново** после установки.

### Проверка:
```bash
uv --version
```
Должен показать номер версии.

---

## Шаг 3: Установите Git

Git — это программа для скачивания кода с GitHub. Мы используем её, чтобы скачать сервер.

### Windows:
1. Перейдите на **https://git-scm.com/download/win**
2. Загрузка начнётся автоматически — запустите установщик
3. Нажимайте **"Next"** на всех экранах — настройки по умолчанию подходят
4. На экране "Adjusting your PATH" убедитесь, что выбрано **"Git from the command line and also from 3rd-party software"** (обычно уже выбрано)
5. Нажмите **"Install"**, затем **"Finish"**
6. **Закройте и откройте PowerShell заново**

### macOS:
Откройте **Терминал** и введите:
```bash
git --version
```
Если git не установлен, macOS предложит установить Command Line Tools — нажмите **"Install"** и подождите.

Или через brew:
```bash
brew install git
```

### Linux (Ubuntu / Debian):
```bash
sudo apt update && sudo apt install git -y
```

### Проверка:
```bash
git --version
```
Должно появиться `git version 2.x.x`.

---

## Шаг 4: Скачайте этот сервер

### Вариант А: Через git (рекомендуется)

Откройте PowerShell (Windows) или Терминал (macOS/Linux) и выполните эти команды по одной:

```bash
cd ~
git clone https://github.com/theohero/telegram-human-in-the-loop.git
```

Это создаст папку `telegram-human-in-the-loop` в вашей **домашней директории** (папке пользователя):
- **Windows**: `C:\Users\ВашеИмя\telegram-human-in-the-loop\`
- **macOS**: `/Users/ВашеИмя/telegram-human-in-the-loop/`
- **Linux**: `/home/ВашеИмя/telegram-human-in-the-loop/`

Путь к файлу сервера:
- **Windows**: `C:\Users\ВашеИмя\telegram-human-in-the-loop\hitl_mcp_server.py`
- **macOS/Linux**: `~/telegram-human-in-the-loop/hitl_mcp_server.py`

> 💡 Этот путь понадобится на Шаге 6.

### Вариант Б: Скачать как ZIP (если не хотите использовать git)
1. Перейдите на **https://github.com/theohero/telegram-human-in-the-loop**
2. Нажмите зелёную кнопку **"< > Code"**
3. Нажмите **"Download ZIP"**
4. Распакуйте ZIP в вашу **домашнюю папку** (например, `C:\Users\ВашеИмя\`)
5. Папка будет называться `telegram-human-in-the-loop-master` — можете переименовать в `telegram-human-in-the-loop`

---

## Шаг 5: Создайте Telegram-бота

1. Откройте **Telegram** на телефоне или компьютере
2. Найдите **@BotFather** (у него синяя галочка ✅)
3. Нажмите **Start**, затем отправьте сообщение: `/newbot`
4. BotFather попросит **имя** — напишите что угодно (например, `Мой ИИ Помощник`)
5. BotFather попросит **username** — должен заканчиваться на `bot` (например, `my_ai_helper_bot`)
6. BotFather ответит вашим **API-токеном** — он выглядит так:
   ```
   123456789:ABCdefGHIjklMNOpqrSTUvwx
   ```
   **Скопируйте этот токен и сохраните** (можно в блокнот)

7. Теперь откройте чат с вашим новым ботом — найдите его по username
8. **Отправьте ему любое сообщение** (например, "привет") — это нужно для активации чата

### 🔑 Как узнать свой Chat ID

Есть два способа узнать ваш персональный Chat ID в Telegram:

**Способ 1: Через Telegram Web (самый простой)**
1. Откройте **https://web.telegram.org** в браузере
2. Войдите в свой аккаунт Telegram
3. Нажмите на **Избранное (Saved Messages)** или откройте чат с самим собой
4. Посмотрите на **URL в адресной строке браузера** — он будет выглядеть так:
   ```
   https://web.telegram.org/k/#777000000
   ```
5. **Число в конце** — это ваш Chat ID (например, `777000000`)
6. **Скопируйте это число и сохраните**

**Способ 2: Через Bot API**
1. Откройте эту ссылку в браузере (замените `ВАШ_ТОКЕН` на настоящий токен бота из шага 6):
   ```
   https://api.telegram.org/botВАШ_ТОКЕН/getUpdates
   ```
2. Найдите `"chat":{"id":` и число после него — это ваш **Chat ID**
3. Пример: `"chat":{"id":777000000` → ваш Chat ID = `777000000`
4. **Скопируйте это число и сохраните**

> ⚠️ Если Способ 2 показывает пустой результат, убедитесь, что вы отправили сообщение боту (шаг 8 выше), затем обновите страницу.

---

## Шаг 6: Настройте ваш редактор кода

Нужно сказать редактору, где находится сервер и дать ему ваши данные Telegram. Файл конфигурации MCP хранится в **папке вашего пользователя**, а не в проекте.

### VS Code (GitHub Copilot) — Самый популярный вариант

1. Откройте VS Code
2. Нажмите `Ctrl + Shift + P` (Windows/Linux) или `Cmd + Shift + P` (macOS)
3. Введите **"MCP: Open User Configuration"** и нажмите Enter
4. Откроется файл конфигурации. Вставьте это содержимое (замените 3 значения, отмеченные ⬅️):

**Windows:**
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
        "C:\\Users\\YourName\\telegram-human-in-the-loop\\hitl_mcp_server.py"
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

**macOS / Linux:**
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
        "/Users/YourName/telegram-human-in-the-loop/hitl_mcp_server.py"
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

⬅️ Замените три вещи:
- **`YourName`** — ваше имя пользователя на компьютере (имя папки в `C:\Users\`)
- **`YOUR_BOT_TOKEN_HERE`** — токен бота из Шага 5
- **`YOUR_CHAT_ID_HERE`** — Chat ID из Шага 5

> ⚠️ В **Windows** используйте **двойные обратные слеши** `\\` в пути
> В **macOS/Linux** используйте обычные слеши `/`

5. Сохраните файл (`Ctrl + S`)

### Claude Desktop

Расположение файла конфигурации:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

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
        "/path/to/telegram-human-in-the-loop/hitl_mcp_server.py"
      ],
      "env": {
        "HITL_TELEGRAM_BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
        "HITL_TELEGRAM_CHAT_ID": "YOUR_CHAT_ID_HERE"
      }
    }
  }
}
```

### Claude Code (CLI)

```bash
claude mcp add hitl-mcp-server \
  -e HITL_TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE \
  -e HITL_TELEGRAM_CHAT_ID=YOUR_CHAT_ID_HERE \
  -- uv run --with "fastmcp>=2.8.1" --with "pydantic>=2.0.0" python ~/telegram-human-in-the-loop/hitl_mcp_server.py
```

### Cursor / Windsurf / Cline

Тот же формат JSON, что и для VS Code — просто поместите его в файл конфигурации вашего редактора (см. таблицу совместимости выше).

---

## Шаг 7: Проверьте, что всё работает!

1. Откройте **VS Code**
2. Откройте **Copilot Chat** (иконка чата или `Ctrl + Alt + I`)
3. Переключитесь в **Agent Mode** (выпадающий список вверху чата)
4. Нажмите иконку **инструментов** (гаечный ключ) рядом с полем ввода → включите `hitl-mcp-server`
5. Напишите в чат:
   > Use the get_multiline_input tool to ask me what my favorite color is
6. **Проверьте Telegram** — должно прийти сообщение от вашего бота!
7. **Ответьте в Telegram** любым текстом
8. ИИ в VS Code продолжит работу с вашим ответом ✅

---

## ❓ Частые вопросы

**Можно ли использовать без Telegram?**
Да! Без переменных окружения Telegram все инструменты показывают обычные всплывающие окна на компьютере.

**Работает ли на сервере без экрана?**
С Telegram — да! Telegram не требует дисплея. Без Telegram на сервере без экрана — нет.

**Могут ли несколько человек использовать одного бота?**
Каждый человек должен создать своего бота. Сервер отвечает только на настроенный Chat ID.

**Мой токен бота в безопасности?**
Токен хранится только в вашем локальном файле конфигурации. Он никуда не отправляется. Никогда не делитесь токеном публично!

**Где хранится конфигурация MCP?**
Конфигурация MCP хранится в **папке вашего профиля пользователя**, а не в проекте. Для VS Code на Windows это `%APPDATA%\Code\User\mcp.json`. Файл сервера (`.py`) может лежать где угодно — вы просто указываете путь к нему в конфигурации.

---

## 🌐 Переменные окружения

| Переменная | Обязательна | Описание |
|----------|----------|-------------|
| `HITL_TELEGRAM_BOT_TOKEN` | Да (для Telegram) | Токен вашего бота от @BotFather |
| `HITL_TELEGRAM_CHAT_ID` | Да (для Telegram) | Ваш Chat ID в Telegram |
| `HITL_TELEGRAM_TIMEOUT_SECONDS` | Нет (по умолч.: 3600) | Сколько секунд ждать ваш ответ |

---

## 📝 License / Лицензия

MIT — используйте как хотите / use it however you want.

---

## 🙏 Credits

Built with [FastMCP](https://github.com/jlowin/fastmcp) and the [Telegram Bot API](https://core.telegram.org/bots/api).

---

<p align="center">
  <b>⭐ Star this repo if you find it useful! / Поставьте звезду, если пригодилось!</b>
</p>
