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
import urllib.request
import urllib.error
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

def _send_and_wait_telegram_multiline_input(
    title: str,
    prompt: str,
    default_value: str = "",
    timeout_seconds: int = 1800
) -> Optional[str]:
    """Send prompt to Telegram and wait for the next message from configured chat."""
    global _telegram_last_update_id

    chat_id = os.getenv("HITL_TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("HITL_TELEGRAM_CHAT_ID is not set")

    _telegram_init_offset()

    message_lines = [
        f"🧠 {title}",
        "",
        prompt,
        "",
        f"{MULTILINE_DELINEATOR}",
        "Reply to this message to continue."
    ]
    if default_value:
        message_lines.extend(["", "Default value:", default_value])

    sent = _telegram_api_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "\n".join(message_lines)
        },
        timeout=20
    )

    sent_message_id = sent.get("result", {}).get("message_id")
    start_time = time.time()

    while True:
        if time.time() - start_time > timeout_seconds:
            return None

        with _telegram_lock:
            offset = (_telegram_last_update_id or 0) + 1

        updates = _telegram_api_call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message", "edited_message"]
            },
            timeout=35
        )

        result = updates.get("result", [])
        if not result:
            continue

        for item in result:
            update_id = item.get("update_id", 0)
            with _telegram_lock:
                _telegram_last_update_id = max(_telegram_last_update_id or 0, update_id)

            msg = item.get("message") or item.get("edited_message")
            if not msg:
                continue

            chat = msg.get("chat", {})
            if not _telegram_chat_id_matches(chat.get("id")):
                continue

            text = msg.get("text")
            if text is None:
                continue

            reply_to = msg.get("reply_to_message", {})
            reply_to_id = reply_to.get("message_id")

            if sent_message_id is not None and reply_to_id == sent_message_id:
                return text

            # Fallback: accept any next text message in the configured chat
            # in case user did not use Telegram's reply feature.
            return text

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
) -> Dict[str, Any]:
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
                    return {
                        "success": True,
                        "user_input": result,
                        "character_count": len(result),
                        "line_count": len(result.split('\n')),
                        "cancelled": False,
                        "platform": CURRENT_PLATFORM,
                        "transport": "telegram"
                    }

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
   - Confirm successful completion of user-guided actions"""
    }
    
    return guidance

# Add a health check tool
@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check if the Human-in-the-Loop server is running and GUI is available."""
    try:
        gui_available = ensure_gui_initialized()
        telegram_enabled = is_telegram_enabled()
        
        return {
            "status": "healthy" if gui_available else "degraded",
            "gui_available": gui_available,
            "telegram_enabled": telegram_enabled,
            "telegram_config": {
                "has_bot_token": bool(os.getenv("HITL_TELEGRAM_BOT_TOKEN")),
                "has_chat_id": bool(os.getenv("HITL_TELEGRAM_CHAT_ID")),
                "chat_id": os.getenv("HITL_TELEGRAM_CHAT_ID", "")
            },
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
                "get_human_loop_prompt"
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
    if is_telegram_enabled():
        print("Telegram transport: ENABLED (get_multiline_input will send+await via Telegram)")
    else:
        print("Telegram transport: DISABLED (set HITL_TELEGRAM_BOT_TOKEN and HITL_TELEGRAM_CHAT_ID)")
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