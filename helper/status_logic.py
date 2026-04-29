"""Pure helper status logic shared by the tray app and tests."""

from __future__ import annotations


def clean_text(text: str | None) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    try:
        return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


def codex_phase_for_text(text: str) -> str:
    if text.startswith("\x01U"):
        return "thinking"
    if text.startswith("\x01C"):
        return "idle"
    return "running"
