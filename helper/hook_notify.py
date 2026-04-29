#!/usr/bin/env python3
"""
Claude Code hook → StickS3 status bridge.

Reads the JSON event Claude Code pipes on stdin, or the JSON string Codex
passes as a notify argument, derives a short Chinese label describing what's
happening, and POSTs it to the voice keyboard helper running on localhost:8765
so the StickS3 screen can show it.

Wire this into .claude/settings.json as PreToolUse / PostToolUse / Stop hook.

Always exits quickly (1s timeout). Never blocks Claude Code.
"""

import argparse
import json
import os
import sys
import urllib.request

TIMEOUT    = 0.8

# Display tool events in the Claude-Code terminal style:
#   Bash(echo hi)
#   >> hi
# so the board's log reads like the CLI you're staring at on your PC.


def infer_event(data: dict) -> str:
    """Some Claude Code hook payloads don't include hook_event_name — fall
    back to inferring from which fields are present."""
    ev = data.get("hook_event_name", "") or ""
    if ev:
        return ev
    if data.get("tool_name"):
        # PostToolUse has tool_response; PreToolUse only has tool_input.
        return "PostToolUse" if (data.get("tool_response") is not None) else "PreToolUse"
    if data.get("prompt") is not None:
        return "UserPromptSubmit"
    if data.get("stop_hook_active") is not None:
        return "Stop"
    return ""


def label_for(data: dict) -> str:
    if data.get("type") == "agent-turn-complete":
        full = (
            data.get("last-assistant-message")
            or data.get("last_assistant_message")
            or ""
        ).replace("\n", " ").strip()
        return "\x01C" + (full[:30] if full else "（完成）")

    event = infer_event(data)
    tool  = data.get("tool_name", "")

    if event == "PreToolUse":
        inp = data.get("tool_input", {}) or {}
        arg = ""
        if tool in ("Bash", "shell_command"):
            arg = (inp.get("command") or "").split("\n")[0]
        elif tool in ("Read", "Edit", "Write"):
            p = inp.get("file_path") or ""
            arg = p.split("\\")[-1].split("/")[-1]
        elif tool in ("Grep", "Glob"):
            arg = inp.get("pattern") or ""
        arg = arg[:24]
        return f"{tool}({arg})" if arg else tool

    if event == "PostToolUse":
        # For Bash show first line of stdout (that's what the terminal ⎿ line shows);
        # for other tools, skip emitting — the PreToolUse entry is enough noise.
        if tool == "Bash":
            resp = data.get("tool_response") or {}
            stdout = (resp.get("stdout") or "").strip()
            first = stdout.split("\n")[0].strip()[:26] if stdout else ""
            if first:
                return f">> {first}"
        return ""  # signal: skip POST
    if event == "Stop" or event == "SubagentStop":
        # Always emit a \x01C-marked line so the board switches to "idle",
        # even if we couldn't extract Claude's reply text from the transcript.
        full = ""
        tp = data.get("transcript_path") or ""
        if tp and os.path.exists(tp):
            try:
                with open(tp, encoding="utf-8") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("type") != "assistant":
                        continue
                    blocks = (msg.get("message") or {}).get("content") or []
                    if not isinstance(blocks, list):
                        continue
                    parts = []
                    for b in blocks:
                        if isinstance(b, dict) and b.get("type") == "text":
                            t = (b.get("text") or "").strip()
                            if t:
                                parts.append(t)
                    full = " ".join(parts).replace("\n", " ").strip()
                    break
            except Exception:
                pass
        return "\x01C" + (full[:30] if full else "（完成）")
    if event == "UserPromptSubmit":
        p = (data.get("prompt") or "").replace("\n", " ").strip()
        # \x01U marker tells the board to render in "user-prompt" color.
        return "\x01U" + p[:30]
    if event == "SessionStart":
        return "会话开始"
    if event == "Notification":
        return "需要注意"
    # Unknown but non-empty event: at least show the event name, never bare "?".
    return event or "活动中"


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--channel", choices=("claude", "codex"), default=os.environ.get("STICKS3_CHANNEL", "claude"))
    args, rest = parser.parse_known_args()
    helper_url = "http://localhost:8765/codex/status" if args.channel == "codex" else "http://localhost:8765/status"

    try:
        if rest:
            raw = rest[-1]
        else:
            # Claude Code pipes JSON in UTF-8; Windows' default stdin encoding is
            # GBK/cp936 which mangles CJK. Read raw bytes and decode explicitly.
            raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return
        data = json.loads(raw)
    except Exception:
        return
    text = label_for(data)
    if not text:
        return  # empty label = skip POST (e.g. PostToolUse for non-Bash)
    try:
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(helper_url, data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        # Bypass any HTTP_PROXY env var — helper is on localhost, should
        # never be routed through a system proxy (e.g. TUN tinyproxy).
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(req, timeout=TIMEOUT)
    except Exception:
        pass  # hook must never block Claude


if __name__ == "__main__":
    main()
