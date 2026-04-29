#!/usr/bin/env python3
"""
StickS3 Claude Codex Helper (Windows tray app)

Runs in the Windows system tray. Responsibilities:

  1. HTTP server on configurable port (default 8765):
     - POST /type         — paste text into Claude target window
     - POST /codex/type   — paste text into Codex target window
     - POST /status       — receive Claude Code progress from hooks
     - POST /codex/status — receive Codex progress from hooks
     - GET  /status       — return Claude buffer for StickS3 to poll
     - GET  /codex/status — return Codex buffer for StickS3 to poll
     - POST /log     — append a line to stick_log.txt (persistent board log)
     - GET  /ping    — liveness probe

  2. UDP discovery responder on configurable port (default 8766):
     - StickS3 broadcasts "STICK?" to locate a helper on the LAN.
     - This helper replies "STICK! <ip>:<http_port>" so the board can cache
       the address without any manual configuration.

  3. System tray icon (pystray):
     - Tooltip shows LAN IP + port + connection status.
     - Right-click menu: "打开配置" / "打开日志文件夹" / "退出".

  4. Config stored in helper/config.json (auto-created with defaults).
     Tkinter config dialog lets the user tweak: port, corrections,
     discovery toggle, open-on-login shortcut.

Install once:
    pip install pystray Pillow

Run (double-click or CLI):
    python type_server.py
"""

import atexit
import ctypes
import datetime as _dt
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from ctypes import wintypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn

from status_logic import clean_text, codex_phase_for_text
from update_check import (
    DEFAULT_RELEASE_API_URL,
    download_helper_update,
    fetch_latest_release,
    format_helper_update_summary,
    helper_update_info,
)
from version_info import HELPER_VERSION, RELEASE_PAGE_URL

# Avoid duplicate tray helpers. A second instance cannot bind the ports anyway,
# and two tray icons make it hard to know which config/log is active.
_SINGLE_INSTANCE_MUTEX = None
if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    _SINGLE_INSTANCE_MUTEX = _kernel32.CreateMutexW(
        None, False, "Local\\StickS3ClaudeCodexHelper.SingleInstance"
    )
    if _SINGLE_INSTANCE_MUTEX and ctypes.get_last_error() == 183:
        # Do not exit here. A stale helper/dev Python process can hold this
        # mutex even after the HTTP server is gone. The real single-instance
        # guard is the port bind in main().
        print("[warn] StickS3ClaudeCodexHelper mutex already exists; checking HTTP port.")

# ==========================================================================
# Config
# ==========================================================================

# Base directory for persistent files (config + log). Under a PyInstaller
# frozen exe, __file__ points INTO the _MEIxxx temp extraction dir — that
# directory gets wiped when the exe exits, so writing config/log there
# makes both disappear on restart. Use the exe's containing folder instead.
if getattr(sys, "frozen", False):
    _THIS_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_THIS_DIR, "config.json")
LOG_FILE    = os.path.join(_THIS_DIR, "stick_log.txt")
CONFIG_SCHEMA_VERSION = 2

DEFAULT_CONFIG = {
    "config_version": CONFIG_SCHEMA_VERSION,
    "http_port": 8765,
    "udp_port": 8766,
    "discovery_enabled": True,
    "autostart": False,
    "codex_session_watch_enabled": False,
    # When non-null, /type will focus this window before pasting. Captured
    # via tray menu "绑定当前窗口为输入目标". Shape: {"title": "...", "class": "..."}.
    "target_window": None,
    "codex_target_window": None,
    "target_click": None,
    "codex_target_click": None,
    "corrections": [
        ["克拉克", "Claude"],
        ["克劳德", "Claude"],
        ["卡劳德", "Claude"],
        ["cloud\\s*code", "Claude Code"],
        ["claude\\s*code", "Claude Code"],
        ["GitHub", "GitHub"],
        ["社会上", "设备上"],
    ],
}


def _fresh_default_config():
    return json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))


def load_config():
    if not os.path.exists(CONFIG_PATH):
        cfg = _fresh_default_config()
        _save(cfg)
        return cfg
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        try:
            backup = CONFIG_PATH + f".bad-{int(time.time())}"
            os.replace(CONFIG_PATH, backup)
            print(f"[warn] config unreadable, backed up to {backup}: {e}")
        except Exception:
            print(f"[warn] config unreadable and backup failed: {e}")
        cfg = _fresh_default_config()
        _save(cfg)
        return cfg
    changed = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    # v0.1.x exposed the release API URL as an editable field. Keep updates
    # pinned to the official project so the UI cannot be misconfigured.
    for old_key in ("helper_release_api_url", "firmware_manifest_url"):
        if old_key in cfg:
            cfg.pop(old_key, None)
            changed = True
    if cfg.get("config_version") != CONFIG_SCHEMA_VERSION:
        cfg["config_version"] = CONFIG_SCHEMA_VERSION
        changed = True
    if changed:
        _save(cfg)
    return cfg


def _save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def save_config(cfg):
    global CONFIG, _compiled_corrections
    _save(cfg)
    CONFIG = cfg
    _compiled_corrections = _compile_corrections(cfg["corrections"])


def _compile_corrections(pairs):
    compiled = []
    for row in pairs:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            print(f"[warn] skipping malformed correction row: {row!r}")
            continue
        pat, repl = row
        try:
            compiled.append((re.compile(pat, re.IGNORECASE), repl))
        except re.error as e:
            print(f"[warn] skipping invalid regex {pat!r}: {e}")
    return compiled


CONFIG = load_config()
_compiled_corrections = _compile_corrections(CONFIG["corrections"])


def fix_text(text: str) -> str:
    for pat, repl in _compiled_corrections:
        text = pat.sub(repl, text)
    return text


# ==========================================================================
# Win32 clipboard + SendInput (IME-safe paste path)
# ==========================================================================
if sys.platform != "win32":
    print("[error] Windows only."); sys.exit(1)

user32   = ctypes.WinDLL("user32",   use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE  = 0x0002

INPUT_KEYBOARD    = 1
KEYEVENTF_KEYUP   = 0x0002
VK_CONTROL = 0x11
VK_V       = 0x56
VK_RETURN  = 0x0D

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR))

class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR))

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD))

class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))
    _anonymous_ = ("i",)
    _fields_ = (("type", wintypes.DWORD), ("i", _I))

LPINPUT = ctypes.POINTER(INPUT)

user32.SendInput.argtypes = (wintypes.UINT, LPINPUT, ctypes.c_int)
user32.SendInput.restype  = wintypes.UINT

kernel32.GlobalAlloc.argtypes  = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype   = wintypes.HANDLE
kernel32.GlobalLock.argtypes   = (wintypes.HANDLE,)
kernel32.GlobalLock.restype    = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalUnlock.restype  = wintypes.BOOL
kernel32.GlobalFree.argtypes   = (wintypes.HANDLE,)
kernel32.GlobalFree.restype    = wintypes.HANDLE

user32.OpenClipboard.argtypes    = (wintypes.HWND,)
user32.OpenClipboard.restype     = wintypes.BOOL
user32.EmptyClipboard.argtypes   = ()
user32.EmptyClipboard.restype    = wintypes.BOOL
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype  = wintypes.HANDLE
user32.CloseClipboard.argtypes   = ()
user32.CloseClipboard.restype    = wintypes.BOOL

# Window-focus APIs (for "always type into the bound terminal" feature).
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype  = wintypes.HWND
user32.GetWindowTextW.argtypes      = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype       = ctypes.c_int
user32.GetClassNameW.argtypes       = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype        = ctypes.c_int
user32.IsWindow.argtypes            = (wintypes.HWND,)
user32.IsWindow.restype             = wintypes.BOOL
user32.IsWindowVisible.argtypes     = (wintypes.HWND,)
user32.IsWindowVisible.restype      = wintypes.BOOL
user32.SwitchToThisWindow.argtypes  = (wintypes.HWND, wintypes.BOOL)
user32.SwitchToThisWindow.restype   = None
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype  = wintypes.BOOL
user32.BringWindowToTop.argtypes    = (wintypes.HWND,)
user32.BringWindowToTop.restype     = wintypes.BOOL
user32.ShowWindow.argtypes          = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype           = wintypes.BOOL
user32.GetWindowRect.argtypes       = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetWindowRect.restype        = wintypes.BOOL
user32.GetCursorPos.argtypes        = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype         = wintypes.BOOL
user32.SetCursorPos.argtypes        = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype         = wintypes.BOOL
user32.mouse_event.argtypes         = (wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ULONG_PTR)
user32.mouse_event.restype          = None
_ENUM_WNDPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes         = (_ENUM_WNDPROC, wintypes.LPARAM)
user32.EnumWindows.restype          = wintypes.BOOL

SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004


def _window_info(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    title = buf.value
    buf2 = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf2, 256)
    cls = buf2.value
    return title, cls


def capture_current_window():
    """Return {title, class} of the current foreground window, or None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    title, cls = _window_info(hwnd)
    if not cls:
        return None
    info = {"title": title, "class": cls}
    parts = [p.strip() for p in title.split(" - ") if p.strip()]
    if len(parts) >= 2:
        # VS Code/Windows Terminal titles often include a volatile document or
        # command prefix. Keep stable suffix fragments for future matching.
        info["title_tail"] = " - ".join(parts[-2:])
        info["project"] = parts[-2]
    return info


def find_target_window(config_key="target_window"):
    """Find a visible window matching CONFIG[config_key] criteria.
    Returns HWND or None. Match: class exact + title contains saved title
    (so terminal titles that change with cwd still match)."""
    tgt = CONFIG.get(config_key) or {}
    if not tgt or not tgt.get("class"):
        return None
    want_cls = tgt["class"]
    candidates = []
    for key in ("title", "title_tail", "project"):
        val = (tgt.get(key) or "").strip()
        if val and val not in candidates:
            candidates.append(val)
    found = []

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        title, cls = _window_info(hwnd)
        if cls != want_cls:
            return True
        if candidates and not any(c in title for c in candidates):
            return True
        score = 0
        if title == (tgt.get("title") or ""):
            score += 100
        if (tgt.get("title_tail") or "") and (tgt.get("title_tail") in title):
            score += 50
        if (tgt.get("project") or "") and (tgt.get("project") in title):
            score += 20
        if hwnd == user32.GetForegroundWindow():
            score += 5
        found.append((score, hwnd, title))
        return True

    user32.EnumWindows(_ENUM_WNDPROC(cb), 0)
    if not found:
        print(f"[bind:{config_key}] target not found class={want_cls!r} candidates={candidates!r}")
        return None
    found.sort(key=lambda row: row[0], reverse=True)
    return found[0][1]


def focus_window(hwnd):
    """Best-effort bring-to-foreground for an arbitrary HWND. Uses
    SwitchToThisWindow which sidesteps the usual SetForegroundWindow
    restrictions on modern Windows."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    for _ in range(4):
        user32.ShowWindow(hwnd, SW_RESTORE)     # unminimize if needed
        user32.SwitchToThisWindow(hwnd, True)   # documented to work w/o focus-steal block
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return user32.GetForegroundWindow() == hwnd


def _window_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def capture_click_position(config_key="target_window"):
    hwnd = find_target_window(config_key)
    if not hwnd:
        hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = _window_rect(hwnd)
    if not rect:
        return None
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    w = max(1, int(rect.right - rect.left))
    h = max(1, int(rect.bottom - rect.top))
    x = int(pt.x - rect.left)
    y = int(pt.y - rect.top)
    return {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "rx": x / w,
        "ry": y / h,
        "right": w - x,
        "bottom": h - y,
    }


def click_bound_position(hwnd, click_info):
    if not hwnd or not isinstance(click_info, dict):
        return False
    rect = _window_rect(hwnd)
    if not rect:
        return False
    w = max(1, int(rect.right - rect.left))
    h = max(1, int(rect.bottom - rect.top))
    old_w = int(click_info.get("w") or 0)
    old_h = int(click_info.get("h") or 0)
    local_x = int(click_info.get("x", 0))
    local_y = int(click_info.get("y", 0))
    if old_w > 0 and abs(w - old_w) > 8:
        local_x = int(float(click_info.get("rx", local_x / max(1, old_w))) * w)
    if old_h > 0 and abs(h - old_h) > 8:
        # Composer boxes usually sit near the bottom of IDE sidebars. If the
        # captured point was in the lower half, keep its bottom distance stable
        # across window resizes; otherwise scale normally.
        if int(click_info.get("y", 0)) > old_h * 0.55 and "bottom" in click_info:
            local_y = h - int(click_info.get("bottom", 0))
        else:
            local_y = int(float(click_info.get("ry", local_y / max(1, old_h))) * h)
    local_x = max(1, min(w - 2, local_x))
    local_y = max(1, min(h - 2, local_y))
    x = rect.left + local_x
    y = rect.top + local_y
    old_pt = wintypes.POINT()
    have_old_pt = bool(user32.GetCursorPos(ctypes.byref(old_pt)))
    user32.SetCursorPos(x, y)
    time.sleep(0.04)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    if have_old_pt:
        time.sleep(0.02)
        user32.SetCursorPos(int(old_pt.x), int(old_pt.y))
    return True


def set_clipboard_text(text: str) -> bool:
    data = text.encode("utf-16le") + b"\x00\x00"
    size = len(data)
    for _ in range(10):
        if user32.OpenClipboard(0): break
        time.sleep(0.05)
    else:
        return False
    hmem = None
    try:
        user32.EmptyClipboard()
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not hmem: return False
        ptr = kernel32.GlobalLock(hmem)
        if not ptr: return False
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(hmem)
        if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
            return False
        # On success the system owns hmem — clear our handle so the
        # finally block doesn't GlobalFree it out from under the clipboard.
        hmem = None
        return True
    finally:
        if hmem:
            kernel32.GlobalFree(hmem)
        user32.CloseClipboard()


def _vk_event(vk, up=False):
    flags = KEYEVENTF_KEYUP if up else 0
    return INPUT(type=INPUT_KEYBOARD,
                 i=INPUT._I(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))

def _send(events):
    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

def _release_all_modifiers():
    for vk in (VK_CONTROL, 0xA2, 0xA3, 0x10, 0xA0, 0xA1, 0x12, 0xA4, 0xA5, 0x5B, 0x5C):
        try: _send([_vk_event(vk, up=True)])
        except Exception: pass

atexit.register(_release_all_modifiers)


def send_ctrl_v():
    try:
        _send([_vk_event(VK_CONTROL),
               _vk_event(VK_V),
               _vk_event(VK_V, up=True),
               _vk_event(VK_CONTROL, up=True)])
    except BaseException:
        _release_all_modifiers()
        raise

def send_enter():
    _send([_vk_event(VK_RETURN), _vk_event(VK_RETURN, up=True)])


def paste_and_enter(text: str, press_enter: bool = True, target_key="target_window") -> bool:
    # If the user has bound a target window (tray menu), bring it to
    # foreground first so the clipboard paste lands there regardless of
    # whatever the user was looking at when voice-input fired.
    tgt = find_target_window(target_key)
    if tgt:
        focused = focus_window(tgt)
        time.sleep(0.12)   # give Windows a beat to actually raise + focus
        click_key = "codex_target_click" if target_key == "codex_target_window" else "target_click"
        clicked = click_bound_position(tgt, CONFIG.get(click_key))
        if not focused:
            focus_window(tgt)
        if clicked:
            time.sleep(0.04)
        time.sleep(0.08)
    if not set_clipboard_text(text): return False
    time.sleep(0.06)
    send_ctrl_v()
    if press_enter:
        time.sleep(0.20)
        send_enter()
    return True


# ==========================================================================
# Claude status ring buffer
# ==========================================================================
_status_lock = threading.Lock()
# Protects concurrent appends to LOG_FILE. Python's GIL makes short
# writes atomic-ish but the two /status + /log handlers serve from
# different worker threads and long multi-line entries can interleave
# without a lock.
_log_file_lock = threading.Lock()
_status_log = deque(maxlen=16)
_seq = 0
_codex_status_log = deque(maxlen=16)
_codex_seq = 0
_codex_phase = "idle"  # idle | thinking | running


def _clean_text(s: str) -> str:
    return clean_text(s)


def _status_state(channel: str):
    if channel == "codex":
        return _codex_status_log, "_codex_seq", "codex"
    return _status_log, "_seq", "claude"


def _codex_phase_for_text(text: str) -> str:
    return codex_phase_for_text(text)


def add_status(text: str, channel: str = "claude") -> int | None:
    global _seq, _codex_seq, _codex_phase
    text = _clean_text((text or "").strip())
    if not text:
        return None
    log, _, label = _status_state(channel)
    with _status_lock:
        if log:
            last = log[-1]
            if last.get("text") == text and time.time() - float(last.get("ts", 0)) < 3.0:
                return int(last.get("seq", 0))
        if channel == "codex":
            _codex_seq += 1
            seq = _codex_seq
        else:
            _seq += 1
            seq = _seq
        log.append({"text": text, "ts": time.time(), "seq": seq})
        if channel == "codex":
            _codex_phase = _codex_phase_for_text(text)
    try:
        with _log_file_lock, open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{label}] {text}\n")
    except Exception as e:
        print(f"[warn] status→log write failed: {e}")
    return seq


def _latest_status_seq(channel: str) -> int:
    log, _, _ = _status_state(channel)
    with _status_lock:
        if not log:
            return 0
        return int(log[-1].get("seq", 0))


def schedule_codex_idle(send_seq: int | None) -> None:
    if not send_seq:
        return

    def _mark_idle_if_unchanged():
        if _latest_status_seq("codex") == send_seq:
            add_status("\x01C（超时空闲）", "codex")

    timer = threading.Timer(90.0, _mark_idle_if_unchanged)
    timer.daemon = True
    timer.start()


def get_status_snapshot(channel: str = "claude") -> dict:
    now = time.time()
    log, _, _ = _status_state(channel)
    with _status_lock:
        items = list(log)
        phase = _codex_phase if channel == "codex" else "idle"
    latest = items[-1] if items else None
    return {
        "latest":     (latest.get("text", "") if latest else ""),
        "latest_seq": (latest.get("seq", 0) if latest else 0),
        "age_sec":    (int(now - latest.get("ts", now)) if latest else -1),
        "history":    [{"text": it.get("text", ""), "seq": it.get("seq", 0)}
                       for it in items[-8:]],
        "state":      phase,
        "now":        int(now),
    }


# ==========================================================================
# Codex session watcher
# ==========================================================================
CODEX_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def _shorten(text: str, limit: int = 30) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text[:limit] if len(text) > limit else text


def _latest_codex_session_file() -> str | None:
    newest = None
    newest_mtime = 0.0
    if not os.path.isdir(CODEX_SESSIONS_DIR):
        return None
    try:
        for root, _dirs, files in os.walk(CODEX_SESSIONS_DIR):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime > newest_mtime:
                    newest = path
                    newest_mtime = mtime
    except Exception:
        return None
    return newest


def _codex_tool_label(payload: dict) -> str:
    name = payload.get("name") or "tool"
    detail = ""
    raw_args = payload.get("arguments") or ""
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        if isinstance(args, dict):
            if name == "shell_command":
                detail = (args.get("command") or "").splitlines()[0]
            elif name in ("read_mcp_resource", "view_image"):
                detail = args.get("uri") or args.get("path") or ""
            elif name == "apply_patch":
                detail = "patch"
    except Exception:
        detail = ""
    detail = _shorten(detail, 18)
    return f"运行 {name} {detail}".strip()


def _codex_event_label(obj: dict) -> str:
    typ = obj.get("type")
    payload = obj.get("payload") or {}
    if not isinstance(payload, dict):
        return ""

    ptyp = payload.get("type")
    if typ == "event_msg" and ptyp == "user_message":
        return "\x01U" + _shorten(payload.get("message") or "")

    if typ == "response_item" and ptyp == "function_call":
        return _codex_tool_label(payload)

    if typ == "event_msg" and ptyp == "agent_message":
        phase = payload.get("phase") or ""
        msg = _shorten(payload.get("message") or "")
        if phase == "final_answer":
            return "\x01C" + (msg or "（完成）")
        if phase == "commentary":
            return "回复中"

    return ""


def codex_session_watch_loop():
    path = None
    pos = 0
    next_scan = 0.0
    while True:
        now = time.time()
        if now >= next_scan:
            next_scan = now + 1.5
            latest = _latest_codex_session_file()
            if latest and latest != path:
                path = latest
                try:
                    pos = os.path.getsize(path)
                except OSError:
                    pos = 0
                print(f"[codex-watch] watching {path}")

        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        label = _codex_event_label(obj)
                        if label:
                            add_status(label, "codex")
                    pos = f.tell()
            except OSError:
                path = None
                pos = 0
        time.sleep(0.25)


# ==========================================================================
# HTTP server
# ==========================================================================
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        try:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            print(f"[warn] json encode failed: {e}")
            body = b'{"error":"encode"}'
            code = 500
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except Exception as e:
            print(f"[warn] send failed ({self.path}): {e}")

    def do_GET(self):
        if self.path == "/ping":
            self._send_json(200, {
                "ok": True,
                "service": "sticks3-typebuddy",
                "helper_version": HELPER_VERSION,
                "config_version": CONFIG.get("config_version"),
            })
        elif self.path == "/status":
            self._send_json(200, get_status_snapshot())
        elif self.path == "/codex/status":
            self._send_json(200, get_status_snapshot("codex"))
        else:
            self._send_json(404, {"error": "unknown path"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "bad content-length"})
            return
        if length < 0 or length > 1_048_576:  # hard cap at 1 MB (localhost only)
            self._send_json(413, {"error": "body too large"})
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return

        if self.path == "/status":
            add_status(data.get("text", ""))
            self._send_json(200, {"ok": True})
            return

        if self.path == "/codex/status":
            add_status(data.get("text", ""), "codex")
            self._send_json(200, {"ok": True})
            return

        if self.path == "/log":
            line = (data.get("text") or "").strip()
            level = (data.get("level") or "info").strip()
            src_ip = self.client_address[0] if self.client_address else "?"
            if line:
                try:
                    with _log_file_lock, open(LOG_FILE, "a", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                                f"[{level}] [from {src_ip}] {line}\n")
                except Exception as e:
                    print(f"[warn] log write failed: {e}")
            self._send_json(200, {"ok": True})
            return

        if self.path not in ("/type", "/codex/type"):
            self._send_json(404, {"error": "unknown path"})
            return

        text = (data.get("text") or "").strip()
        press_enter = bool(data.get("enter", True))
        if not text:
            self._send_json(400, {"error": "no text"})
            return

        is_codex = self.path == "/codex/type"
        fixed = fix_text(text)
        try:
            ok = paste_and_enter(
                fixed, press_enter,
                "codex_target_window" if is_codex else "target_window"
            )
        except Exception as e:
            print(f"[error] paste failed: {e}")
            self._send_json(500, {"error": str(e)})
            return

        if ok:
            channel = "codex" if is_codex else "claude"
            seq = add_status("\x01U" + fixed, channel)
            if is_codex:
                # Codex notify marks completion; this is only a stale-state
                # fallback for cases where notify is not configured or missed.
                schedule_codex_idle(seq)
            if fixed != text:
                print(f"[typed:{'codex' if is_codex else 'claude'}] {fixed}   (was: {text})")
            else:
                print(f"[typed:{'codex' if is_codex else 'claude'}] {fixed}")
        else:
            print(f"[warn] paste_and_enter returned False for: {fixed}")

        self._send_json(200, {"ok": bool(ok), "chars": len(fixed)})

    def log_message(self, *args, **kwargs):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ==========================================================================
# UDP discovery responder
#   Board broadcasts b"STICK?" to udp_port → reply b"STICK! <ip>:<http_port>"
# ==========================================================================
def discovery_loop():
    if not CONFIG.get("discovery_enabled", True):
        return
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", CONFIG["udp_port"]))
    except Exception as e:
        print(f"[warn] UDP discovery bind failed: {e}")
        return
    print(f"[discovery] listening on UDP :{CONFIG['udp_port']}")
    while True:
        try:
            data, addr = s.recvfrom(512)
        except Exception:
            continue
        if not data:
            continue
        if data.startswith(b"STICK?"):
            ip = local_ip()
            reply = f"STICK! {ip}:{CONFIG['http_port']}".encode("ascii")
            try:
                s.sendto(reply, addr)
                print(f"[discovery] replied to {addr[0]} → {ip}:{CONFIG['http_port']}")
            except Exception as e:
                print(f"[warn] discovery reply failed: {e}")


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ==========================================================================
# Autostart (Startup folder shortcut)
# ==========================================================================
STARTUP_SHORTCUT = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup\StickS3 Claude Codex 小秘书.lnk",
)


def set_autostart(enable: bool) -> None:
    if enable:
        try:
            # Pick the right target based on whether we're running as a
            # PyInstaller-frozen exe or as a plain .py script. Under the
            # exe, __file__ points at a _MEIxxx temp dir that vanishes
            # when the process exits — the shortcut has to point at
            # sys.executable (the exe itself) instead.
            if getattr(sys, "frozen", False):
                target = sys.executable
                args = ""
                workdir = os.path.dirname(sys.executable)
            else:
                target = "pythonw.exe"
                args = f'"{os.path.abspath(__file__)}"'
                workdir = _THIS_DIR
            ps = (
                f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut('
                f'"{STARTUP_SHORTCUT}");'
                f'$s.TargetPath="{target}";'
            )
            if args:
                ps += f'$s.Arguments=\'{args}\';'
            ps += f'$s.WorkingDirectory="{workdir}";$s.Save()'
            import subprocess
            subprocess.run(["powershell", "-Command", ps], check=False,
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        except Exception as e:
            print(f"[warn] autostart enable failed: {e}")
    else:
        try:
            if os.path.exists(STARTUP_SHORTCUT):
                os.remove(STARTUP_SHORTCUT)
        except Exception as e:
            print(f"[warn] autostart disable failed: {e}")


def helper_executable_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(_THIS_DIR) / "dist" / "StickS3ClaudeCodexHelper.exe"


def open_release_page():
    try:
        os.startfile(RELEASE_PAGE_URL)
    except Exception as e:
        print(f"[warn] open release page: {e}")


def export_diagnostics(parent=None):
    try:
        from tkinter import messagebox
    except Exception:
        messagebox = None

    diag_dir = Path(_THIS_DIR) / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = diag_dir / f"StickS3HelperDiag_{stamp}.zip"
    summary = {
        "helper_version": HELPER_VERSION,
        "config_version": CONFIG.get("config_version"),
        "local_ip": local_ip(),
        "http_port": CONFIG.get("http_port"),
        "udp_port": CONFIG.get("udp_port"),
        "discovery_enabled": CONFIG.get("discovery_enabled"),
        "autostart": CONFIG.get("autostart"),
        "claude_target": format_target_status("target_window", "Claude"),
        "codex_target": format_target_status("codex_target_window", "Codex"),
        "frozen": bool(getattr(sys, "frozen", False)),
        "exe": str(helper_executable_path()),
        "platform": platform.platform(),
        "python": sys.version,
    }
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
            if os.path.exists(CONFIG_PATH):
                z.write(CONFIG_PATH, "config.json")
            if os.path.exists(LOG_FILE):
                z.write(LOG_FILE, "stick_log.txt")
            latest_log = Path(_THIS_DIR) / "dist" / "stick_log.txt"
            if latest_log.exists() and str(latest_log) != LOG_FILE:
                z.write(latest_log, "dist_stick_log.txt")
        if messagebox:
            messagebox.showinfo("诊断包已导出", str(out), parent=parent)
        try:
            os.startfile(str(diag_dir))
        except Exception:
            pass
        return out
    except Exception as e:
        if messagebox:
            messagebox.showerror("导出失败", str(e), parent=parent)
        else:
            print(f"[warn] diagnostic export failed: {e}")
        return None


def install_helper_update(info: dict, parent=None) -> None:
    try:
        from tkinter import messagebox
    except Exception:
        messagebox = None

    latest = info.get("latest_version") or "latest"
    downloads = Path(_THIS_DIR) / "updates"
    dest = downloads / f"StickS3ClaudeCodexHelper-{latest}.exe"
    try:
        actual_sha = download_helper_update(info, dest)
    except Exception as e:
        if messagebox:
            messagebox.showerror("下载失败", str(e), parent=parent)
        else:
            print(f"[warn] helper update download failed: {e}")
        return

    if not getattr(sys, "frozen", False):
        if messagebox:
            messagebox.showinfo(
                "已下载助手",
                f"源码运行模式不会自动替换 exe。\n\n文件已下载：\n{dest}\nSHA256: {actual_sha}",
                parent=parent,
            )
        try:
            os.startfile(str(downloads))
        except Exception:
            pass
        return

    current = helper_executable_path()
    backup = current.with_suffix(f".{HELPER_VERSION}.bak.exe")
    script = Path(_THIS_DIR) / "apply_helper_update.ps1"
    script.write_text(
        """param(
  [string]$Current,
  [string]$NewExe,
  [string]$Backup,
  [int]$OldPid
)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $OldPid -Timeout 30 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 700
if (Test-Path -LiteralPath $Current) {
  Copy-Item -LiteralPath $Current -Destination $Backup -Force
}
Copy-Item -LiteralPath $NewExe -Destination $Current -Force
Start-Process -FilePath $Current -WorkingDirectory (Split-Path -Parent $Current) -WindowStyle Hidden
try { Remove-Item -LiteralPath $PSCommandPath -Force } catch {}
""",
        encoding="utf-8",
    )
    if messagebox:
        ok = messagebox.askyesno(
            "安装助手更新",
            "助手将退出、替换 exe，然后自动重新启动。\n\n现在安装吗？",
            parent=parent,
        )
        if not ok:
            return
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            str(current),
            str(dest),
            str(backup),
            str(os.getpid()),
        ],
        creationflags=0x08000000,
    )
    try:
        _release_all_modifiers()
    except Exception:
        pass
    os._exit(0)


# ==========================================================================
# Config dialog (tkinter)
# ==========================================================================
def format_target_status(config_key="target_window", label="Claude"):
    tgt = CONFIG.get(config_key)
    if not tgt:
        return f"{label}: 跟随焦点（未绑定）"
    title = (tgt.get("title") or "").strip() or "(无标题)"
    if len(title) > 28:
        title = title[:28] + "…"
    click_key = "codex_target_click" if config_key == "codex_target_window" else "target_click"
    suffix = " +点位" if CONFIG.get(click_key) else ""
    return f"{label}: {title}{suffix}"


def open_config_dialog():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception as e:
        print(f"[warn] tkinter missing: {e}")
        return

    root = tk.Tk()
    root.title("StickS3 小秘书 · 配置")
    root.geometry("640x720")
    root.resizable(False, False)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
    style.configure("Hint.TLabel", foreground="#6B7280")
    style.configure("Value.TLabel", foreground="#111827")
    style.configure("Accent.TButton", padding=(12, 6))

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    header = ttk.Frame(frm)
    header.pack(fill="x", pady=(0, 10))
    ttk.Label(header, text="StickS3 小秘书", style="Title.TLabel").pack(side="left")
    ttk.Label(header, text=f"v{HELPER_VERSION}", style="Hint.TLabel").pack(side="right", pady=(7, 0))

    status_box = ttk.LabelFrame(frm, text="运行状态", padding=10)
    status_box.pack(fill="x", pady=(0, 8))
    ip_var = tk.StringVar(value=local_ip())
    claude_target_var = tk.StringVar(value=format_target_status("target_window", "Claude"))
    codex_target_var = tk.StringVar(value=format_target_status("codex_target_window", "Codex"))
    ttk.Label(status_box, text="本机 IP").grid(row=0, column=0, sticky="w")
    ttk.Label(status_box, textvariable=ip_var, style="Value.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
    ttk.Label(status_box, text="Claude").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Label(status_box, textvariable=claude_target_var, style="Value.TLabel").grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(6, 0))
    ttk.Label(status_box, text="Codex").grid(row=2, column=0, sticky="w", pady=(4, 0))
    ttk.Label(status_box, textvariable=codex_target_var, style="Value.TLabel").grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(4, 0))
    status_box.columnconfigure(1, weight=1)

    net_box = ttk.LabelFrame(frm, text="网络与启动", padding=10)
    net_box.pack(fill="x", pady=(0, 8))
    ttk.Label(net_box, text="HTTP 端口").grid(row=0, column=0, sticky="w")
    http_var = tk.StringVar(value=str(CONFIG["http_port"]))
    ttk.Entry(net_box, textvariable=http_var, width=10).grid(row=0, column=1, padx=(10, 18), sticky="w")
    ttk.Label(net_box, text="UDP 发现端口").grid(row=0, column=2, sticky="w")
    udp_var = tk.StringVar(value=str(CONFIG["udp_port"]))
    ttk.Entry(net_box, textvariable=udp_var, width=10).grid(row=0, column=3, padx=(10, 0), sticky="w")
    discovery_var = tk.BooleanVar(value=CONFIG["discovery_enabled"])
    ttk.Checkbutton(net_box, text="启用 UDP 自动发现", variable=discovery_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
    autostart_var = tk.BooleanVar(value=CONFIG.get("autostart", False))
    ttk.Checkbutton(net_box, text="开机自动启动", variable=autostart_var).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

    update_box = ttk.LabelFrame(frm, text="助手更新", padding=10)
    update_box.pack(fill="x", pady=(0, 8))
    latest_var = tk.StringVar(value="尚未检查")
    update_state_var = tk.StringVar(value="官方 Release 通道")
    update_detail_var = tk.StringVar(value="更新地址由程序内置，避免误删或误改。")
    ttk.Label(update_box, text="当前版本").grid(row=0, column=0, sticky="w")
    ttk.Label(update_box, text=f"v{HELPER_VERSION}", style="Value.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))
    ttk.Label(update_box, text="最新版本").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Label(update_box, textvariable=latest_var, style="Value.TLabel").grid(row=1, column=1, sticky="w", padx=(12, 0), pady=(6, 0))
    ttk.Label(update_box, text="状态").grid(row=2, column=0, sticky="w", pady=(6, 0))
    ttk.Label(update_box, textvariable=update_state_var, style="Value.TLabel", wraplength=470).grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(6, 0))
    ttk.Label(update_box, textvariable=update_detail_var, style="Hint.TLabel", wraplength=590).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
    latest_info = {"value": None}

    def refresh_status_labels():
        ip_var.set(local_ip())
        claude_target_var.set(format_target_status("target_window", "Claude"))
        codex_target_var.set(format_target_status("codex_target_window", "Codex"))

    def test_target(target_key, label):
        if not CONFIG.get(target_key):
            messagebox.showwarning("未绑定目标", f"请先在托盘菜单里绑定 {label} 输入目标，再测试粘贴。")
            return

        def do():
            ok = paste_and_enter(f"hello from StickS3 {label}", True, target_key)
            root.after(0, lambda: messagebox.showinfo(
                "测试结果" if ok else "测试失败",
                f"{label} 测试文本已发送。" if ok else f"{label} 测试发送失败，请检查绑定窗口。"
            ))

        threading.Thread(target=do, daemon=True).start()

    def check_helper_update():
        update_state_var.set("正在检查...")
        update_detail_var.set("正在读取 GitHub 最新 Release。")
        install_btn.configure(state="disabled")

        def do():
            try:
                release = fetch_latest_release(DEFAULT_RELEASE_API_URL)
                info = helper_update_info(release, HELPER_VERSION)
                summary = format_helper_update_summary(release, HELPER_VERSION)
            except Exception as e:
                msg = str(e)
                root.after(0, lambda msg=msg: (
                    update_state_var.set("检查失败"),
                    update_detail_var.set(msg),
                    messagebox.showerror("检查失败", msg)
                ))
                return

            def done():
                latest_info["value"] = info
                latest_var.set("v" + (info.get("latest_version") or "未知"))
                if info.get("is_newer") is True:
                    update_state_var.set("发现新版")
                    install_btn.configure(state="normal" if info.get("url") else "disabled")
                elif info.get("is_newer") is False:
                    update_state_var.set("已是最新版")
                    install_btn.configure(state="disabled")
                else:
                    update_state_var.set("版本状态未知")
                    install_btn.configure(state="normal" if info.get("url") else "disabled")
                update_detail_var.set(summary.replace("\n", "  |  "))

            root.after(0, done)

        threading.Thread(target=do, daemon=True).start()

    def install_selected_update():
        info = latest_info.get("value")
        if not info:
            messagebox.showwarning("未检查更新", "请先检查助手更新。")
            return
        if not info.get("url"):
            messagebox.showerror("无法安装", "最新 Release 中没有助手 exe 下载地址。")
            return
        update_state_var.set("正在下载...")
        update_detail_var.set("下载并校验助手 exe，完成后会提示替换。")
        root.update_idletasks()
        install_helper_update(info, root)

    update_btns = ttk.Frame(update_box)
    update_btns.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
    ttk.Button(update_btns, text="检查助手更新", command=check_helper_update, style="Accent.TButton").pack(side="left")
    install_btn = ttk.Button(update_btns, text="下载并安装", command=install_selected_update, state="disabled")
    install_btn.pack(side="left", padx=(8, 0))
    ttk.Button(update_btns, text="打开发布页", command=open_release_page).pack(side="left", padx=(8, 0))

    actions_box = ttk.LabelFrame(frm, text="工具", padding=10)
    actions_box.pack(fill="x", pady=(0, 8))
    ttk.Button(actions_box, text="刷新状态", command=refresh_status_labels).pack(side="left")
    ttk.Button(actions_box, text="测试 Claude", command=lambda: test_target("target_window", "Claude")).pack(side="left", padx=(8, 0))
    ttk.Button(actions_box, text="测试 Codex", command=lambda: test_target("codex_target_window", "Codex")).pack(side="left", padx=(8, 0))
    ttk.Button(actions_box, text="导出诊断包", command=lambda: export_diagnostics(root)).pack(side="right")
    ttk.Button(actions_box, text="打开日志目录", command=open_log_folder).pack(side="right", padx=(0, 8))

    corr_box = ttk.LabelFrame(frm, text="语音纠错词表", padding=10)
    corr_box.pack(fill="both", expand=True)
    ttk.Label(corr_box, text="每行一对，格式：正则=替换", style="Hint.TLabel").pack(anchor="w")
    txt = tk.Text(corr_box, height=9, font=("Consolas", 10), relief="solid", borderwidth=1)
    txt.pack(fill="both", expand=True)
    for pat, repl in CONFIG["corrections"]:
        txt.insert("end", f"{pat}={repl}\n")

    def on_save():
        try:
            http_port = int(http_var.get())
            udp_port = int(udp_var.get())
        except ValueError:
            messagebox.showerror("错误", "端口必须是数字")
            return
        corr = []
        for line in txt.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            pat, _, repl = line.partition("=")
            corr.append([pat.strip(), repl.strip()])
        new_cfg = {
            "config_version": CONFIG_SCHEMA_VERSION,
            "http_port": http_port,
            "udp_port": udp_port,
            "discovery_enabled": discovery_var.get(),
            "autostart": autostart_var.get(),
            "codex_session_watch_enabled": CONFIG.get("codex_session_watch_enabled", False),
            # Preserve the bound target window across config-dialog saves.
            "target_window": CONFIG.get("target_window"),
            "codex_target_window": CONFIG.get("codex_target_window"),
            "target_click": CONFIG.get("target_click"),
            "codex_target_click": CONFIG.get("codex_target_click"),
            "corrections": corr,
        }
        save_config(new_cfg)
        set_autostart(autostart_var.get())
        messagebox.showinfo("已保存",
                            "修改已保存到 config.json。\n\n"
                            "端口改动需要重启助手才生效。")
        root.destroy()

    btn_row = ttk.Frame(frm); btn_row.pack(fill="x", pady=(10, 0))
    ttk.Button(btn_row, text="保存", command=on_save, style="Accent.TButton").pack(side="right")
    ttk.Button(btn_row, text="取消", command=root.destroy).pack(side="right", padx=8)

    root.mainloop()


# ==========================================================================
# Tray icon
# ==========================================================================
def open_log_folder(icon=None, item=None):
    try:
        os.startfile(_THIS_DIR)
    except Exception as e:
        print(f"[warn] open log folder: {e}")


def on_check_helper_update(icon=None, item=None):
    def do():
        try:
            release = fetch_latest_release(DEFAULT_RELEASE_API_URL)
            info = helper_update_info(release, HELPER_VERSION)
            latest = info.get("latest_version") or "未知版本"
            status = "发现新版" if info.get("is_newer") is True else "已是最新版"
            show_tray_feedback(icon, "助手更新", f"当前 v{HELPER_VERSION} / 最新 v{latest} {status}".strip(), restore_after=8.0)
        except Exception as e:
            show_tray_feedback(icon, "检查更新失败", str(e)[:80], restore_after=8.0)

    threading.Thread(target=do, daemon=True).start()


def _tray_base_title():
    return f"StickS3 小秘书 - {local_ip()}:{CONFIG['http_port']}"


def show_tray_feedback(icon, title, message, restore_after=3.0):
    print(f"[tray] {title}: {message}")
    if not icon:
        return
    try:
        icon.title = f"{title}: {message}"
    except Exception:
        pass
    try:
        icon.notify(message, title)
    except Exception as e:
        print(f"[warn] tray notify failed: {e}")

    def restore():
        try:
            icon.title = _tray_base_title()
        except Exception:
            pass

    timer = threading.Timer(restore_after, restore)
    timer.daemon = True
    timer.start()


def on_bind_target(icon=None, item=None, config_key="target_window", label="Claude"):
    """User wants to lock voice input to a specific window. Give them 3
    seconds to focus their target (e.g. the terminal running Claude/Codex),
    then capture that window's title + class."""
    def do():
        # Countdown via tooltip so user can see what's happening.
        for n in (3, 2, 1):
            try: icon.title = f"绑定{label}窗口中… {n}"
            except Exception: pass
            time.sleep(1)
        info = capture_current_window()
        try: icon.title = f"StickS3 小秘书 — {local_ip()}:{CONFIG['http_port']}"
        except Exception: pass
        if not info:
            print("[bind] no foreground window captured")
            show_tray_feedback(
                icon,
                f"{label} \u7ed1\u5b9a\u5931\u8d25",
                "\u6ca1\u6709\u6293\u5230\u524d\u53f0\u7a97\u53e3",
            )
            return
        CONFIG[config_key] = info
        save_config(CONFIG)
        print(f"[bind:{label}] locked to: {info['class']!r} / title~{info['title']!r}")
        title = (info.get("title") or "").strip() or "\u65e0\u6807\u9898"
        if len(title) > 36:
            title = title[:36] + "..."
        show_tray_feedback(
            icon,
            f"{label} \u7ed1\u5b9a\u6210\u529f",
            f"\u8f93\u5165\u76ee\u6807: {title}",
        )
    threading.Thread(target=do, daemon=True).start()


def on_clear_target(icon=None, item=None, config_key="target_window", label="Claude"):
    CONFIG[config_key] = None
    save_config(CONFIG)
    print(f"[bind:{label}] target window cleared")
    show_tray_feedback(
        icon,
        f"{label} \u5df2\u6e05\u9664",
        "\u8f93\u5165\u76ee\u6807\u5df2\u6e05\u9664",
    )


def on_bind_click(icon=None, item=None, config_key="target_window", click_key="target_click", label="Claude"):
    """Capture mouse position relative to the bound window. For Electron apps
    like VS Code this is more reliable than window focus alone because the
    text box is inside the app, not a separate Win32 control."""
    def do():
        for n in (3, 2, 1):
            try: icon.title = f"绑定{label}输入框… {n}"
            except Exception: pass
            time.sleep(1)
        pos = capture_click_position(config_key)
        try: icon.title = f"StickS3 小秘书 — {local_ip()}:{CONFIG['http_port']}"
        except Exception: pass
        if not pos:
            print(f"[bind:{label}] input click position capture failed")
            show_tray_feedback(
                icon,
                f"{label} \u70b9\u4f4d\u5931\u8d25",
                "\u6ca1\u6709\u6293\u5230\u8f93\u5165\u6846\u4f4d\u7f6e",
            )
            return
        CONFIG[click_key] = pos
        save_config(CONFIG)
        print(f"[bind:{label}] input click position: x={pos['x']} y={pos['y']}")
        show_tray_feedback(
            icon,
            f"{label} \u70b9\u4f4d\u6210\u529f",
            f"\u8f93\u5165\u6846: x={pos['x']} y={pos['y']}",
        )
    threading.Thread(target=do, daemon=True).start()


def on_clear_click(icon=None, item=None, click_key="target_click", label="Claude"):
    CONFIG[click_key] = None
    save_config(CONFIG)
    print(f"[bind:{label}] input click position cleared")
    show_tray_feedback(
        icon,
        f"{label} \u5df2\u6e05\u9664",
        "\u8f93\u5165\u6846\u4f4d\u7f6e\u5df2\u6e05\u9664",
    )


def on_quit(icon, item=None):
    # os._exit bypasses atexit handlers, so the Ctrl/Shift/etc release
    # pass we registered there won't fire. Call it explicitly so no
    # modifier key stays system-wide "down" if the user quits mid-paste.
    try:
        _release_all_modifiers()
    except Exception:
        pass
    icon.stop()
    os._exit(0)


def make_icon_image():
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Warm orange "sparkle" (Claude style)
    c = (232, 151, 111, 255)
    # four petals
    d.polygon([(32, 4), (36, 32), (32, 32)], fill=c)
    d.polygon([(32, 60), (28, 32), (32, 32)], fill=c)
    d.polygon([(4, 32), (32, 28), (32, 32)], fill=c)
    d.polygon([(60, 32), (32, 36), (32, 32)], fill=c)
    # diagonals (shorter)
    d.polygon([(14, 14), (32, 30), (30, 32)], fill=c)
    d.polygon([(50, 50), (32, 34), (34, 32)], fill=c)
    d.polygon([(14, 50), (30, 32), (32, 34)], fill=c)
    d.polygon([(50, 14), (34, 32), (32, 30)], fill=c)
    # center dot
    d.ellipse((28, 28, 36, 36), fill=c)
    return img


def start_tray():
    try:
        import pystray
    except ImportError:
        print("[error] pystray not installed. Run:  pip install pystray Pillow")
        # Fallback: just block
        while True:
            time.sleep(3600)
        return

    ip = local_ip()
    title = f"StickS3 小秘书 — {ip}:{CONFIG['http_port']}"

    menu = pystray.Menu(
        pystray.MenuItem(f"IP: {ip}:{CONFIG['http_port']}", None, enabled=False),
        pystray.MenuItem(lambda _: format_target_status("target_window", "Claude"), None, enabled=False),
        pystray.MenuItem(lambda _: format_target_status("codex_target_window", "Codex"), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("绑定 Claude 输入目标 (3 秒后抓取)",
                         lambda icon, item: on_bind_target(icon, item, "target_window", "Claude")),
        pystray.MenuItem("绑定 Codex 输入目标 (3 秒后抓取)",
                         lambda icon, item: on_bind_target(icon, item, "codex_target_window", "Codex")),
        pystray.MenuItem("绑定 Claude 输入框位置 (3 秒后抓鼠标)",
                         lambda icon, item: on_bind_click(icon, item, "target_window", "target_click", "Claude")),
        pystray.MenuItem("绑定 Codex 输入框位置 (3 秒后抓鼠标)",
                         lambda icon, item: on_bind_click(icon, item, "codex_target_window", "codex_target_click", "Codex")),
        pystray.MenuItem("清除 Claude 绑定",
                         lambda icon, item: on_clear_target(icon, item, "target_window", "Claude")),
        pystray.MenuItem("清除 Codex 绑定",
                         lambda icon, item: on_clear_target(icon, item, "codex_target_window", "Codex")),
        pystray.MenuItem("清除 Claude 输入框位置",
                         lambda icon, item: on_clear_click(icon, item, "target_click", "Claude")),
        pystray.MenuItem("清除 Codex 输入框位置",
                         lambda icon, item: on_clear_click(icon, item, "codex_target_click", "Codex")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开配置", lambda icon, item: threading.Thread(
            target=open_config_dialog, daemon=True).start()),
        pystray.MenuItem("检查助手更新", on_check_helper_update),
        pystray.MenuItem("导出诊断包", lambda icon, item: export_diagnostics()),
        pystray.MenuItem("打开发布页", lambda icon, item: open_release_page()),
        pystray.MenuItem("打开日志文件夹", open_log_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("StickS3ClaudeCodexHelper", make_icon_image(), title, menu)
    icon.run()


# ==========================================================================
# main
# ==========================================================================
def main():
    ip = local_ip()
    print("=" * 60)
    print(f"  StickS3 Claude Codex Helper")
    print(f"  HTTP :{CONFIG['http_port']}   UDP discovery :{CONFIG['udp_port']}")
    print(f"  LAN IP: {ip}")
    print(f"  Log:    {LOG_FILE}")
    print(f"  Config: {CONFIG_PATH}")
    print("=" * 60)

    # HTTP server thread
    try:
        http_srv = ThreadedHTTPServer(("0.0.0.0", CONFIG["http_port"]), Handler)
    except OSError as e:
        print(f"[error] HTTP port {CONFIG['http_port']} in use: {e}")
        sys.exit(1)
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()

    # UDP discovery thread
    threading.Thread(target=discovery_loop, daemon=True).start()

    # Fallback for old Codex builds without official hooks. Disabled by
    # default because official hooks provide cleaner event boundaries.
    if CONFIG.get("codex_session_watch_enabled", False):
        threading.Thread(target=codex_session_watch_loop, daemon=True).start()

    # Tray icon runs on main thread (required by pystray)
    try:
        start_tray()
    except KeyboardInterrupt:
        print("\n[stopped]")


if __name__ == "__main__":
    main()
