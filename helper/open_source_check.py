#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SENSITIVE_PATTERNS = [
    ("GitHub classic token", re.compile(r"ghp_[A-Za-z0-9_]{20,}")),
    ("GitHub fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("OpenAI-style token", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    (
        "Hardcoded secret-like value",
        re.compile(
            r"(?i)[\"']?\b(?:api[_-]?key|api[_-]?secret|password|passwd|pwd|token|bearer|authorization|credential)"
            r"\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']"
        ),
    ),
]

PLACEHOLDER_MARKERS = (
    "YOUR_",
    "OWNER/REPO",
    "example",
    "placeholder",
    "firmware.bin",
    "manifest.json",
)

LOCAL_PRIVATE_PATHS = [
    "src/secrets.h",
    "helper/config.json",
    "helper/stick_log.txt",
    "helper/dist/config.json",
    "helper/dist/stick_log.txt",
    ".claude/settings.local.json",
    "upload.log",
]

LOCAL_PRIVATE_DIRS = [
    ".pio",
    "helper/build",
    "helper/dist",
    "helper/diagnostics",
    "helper/dist/diagnostics",
    "downloads",
    "memory",
]

HISTORY_RISK_PATHS = [
    "helper/dist/StickS3Helper.exe",
    "helper/dist/StickS3ClaudeCodexHelper.exe",
    "upload.log",
    "StickS3.pdf",
    "StickS3.txt",
    ".claude/settings.local.json",
]

MAX_TEXT_BYTES = 2_000_000


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def git_lines(args: list[str]) -> list[str]:
    proc = run_git(args)
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
        raise SystemExit(proc.returncode)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def is_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" in sample


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_TEXT_BYTES:
        data = data[:MAX_TEXT_BYTES]
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def is_placeholder(value: str) -> bool:
    lower = value.lower()
    return any(marker.lower() in lower for marker in PLACEHOLDER_MARKERS)


def scan_current_tree(files: list[str]) -> int:
    findings: list[str] = []
    binaries: list[str] = []

    for rel in files:
        path = ROOT / rel
        if is_binary(path):
            binaries.append(rel)
            continue

        text = read_text(path)
        if text is None:
            continue

        for name, pattern in SENSITIVE_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1) if match.lastindex else match.group(0)
                if is_placeholder(value):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{rel}:{line}: {name}")

    if findings:
        print("FAIL: suspicious secret-like content in tracked files")
        for finding in findings:
            print(f"  {finding}")
    else:
        print("OK: no obvious secret tokens found in tracked text files")

    if binaries:
        print("INFO: tracked binary files:")
        for rel in binaries:
            print(f"  {rel}")

    return 1 if findings else 0


def report_local_private_files() -> None:
    found = []
    for rel in LOCAL_PRIVATE_PATHS:
        if (ROOT / rel).exists():
            found.append(rel)
    for rel in LOCAL_PRIVATE_DIRS:
        if (ROOT / rel).exists():
            found.append(rel + "/")

    if not found:
        print("OK: no known local private files are present")
        return

    print("WARN: local private/generated files exist; do not publish a folder zip")
    for rel in found:
        print(f"  {rel}")


def report_history_risks() -> None:
    found = []
    for rel in HISTORY_RISK_PATHS:
        proc = run_git(["log", "--all", "--format=%H", "--", rel])
        if proc.returncode == 0 and proc.stdout.strip():
            found.append(rel)

    if not found:
        print("OK: no known risky artifact paths found in Git history")
        return

    print("WARN: known artifact paths exist in Git history")
    for rel in found:
        print(f"  {rel}")
    print("WARN: making this existing repository public will expose those historical objects")


def main() -> int:
    files = git_lines(["ls-files"])
    exit_code = scan_current_tree(files)
    report_local_private_files()
    report_history_risks()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
