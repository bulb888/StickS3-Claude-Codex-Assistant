#!/usr/bin/env python3
"""Sync generated version files from release.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_JSON = ROOT / "release.json"
REMOTE_OTA_CONFIG = ROOT / "src" / "remote_ota_config.h"
VERSION_INFO = ROOT / "helper" / "version_info.py"


def load_release_config() -> dict:
    data = json.loads(RELEASE_JSON.read_text(encoding="utf-8"))
    required = ("version", "helper_version", "repo")
    missing = [key for key in required if not str(data.get(key, "")).strip()]
    firmware_notes = str(data.get("firmware_notes") or data.get("notes") or "").strip()
    if not firmware_notes:
        missing.append("firmware_notes or notes")
    if missing:
        raise ValueError(f"release.json missing: {', '.join(missing)}")
    cfg = {key: str(data[key]).strip() for key in required}
    cfg["firmware_notes"] = firmware_notes
    cfg["helper_notes"] = str(data.get("helper_notes") or data.get("notes") or firmware_notes).strip()
    cfg["notes"] = firmware_notes
    cfg["manifest_url"] = f"https://github.com/{cfg['repo']}/releases/latest/download/manifest.json"
    return cfg


def rendered_version_info(cfg: dict) -> str:
    return (
        "#!/usr/bin/env python3\n"
        '"""Generated project version constants.\n\n'
        "Run `python helper/version_sync.py` after editing release.json.\n"
        '"""\n\n'
        f"PROJECT_VERSION = {cfg['version']!r}\n"
        f"HELPER_VERSION = {cfg['helper_version']!r}\n"
        f"RELEASE_REPO = {cfg['repo']!r}\n"
        f"FIRMWARE_RELEASE_NOTES = {cfg['firmware_notes']!r}\n"
        f"HELPER_RELEASE_NOTES = {cfg['helper_notes']!r}\n"
        "RELEASE_NOTES = FIRMWARE_RELEASE_NOTES\n"
        'RELEASE_API_URL = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"\n'
        'RELEASE_PAGE_URL = f"https://github.com/{RELEASE_REPO}/releases/latest"\n'
        'HELPER_ASSET_NAME = "StickS3ClaudeCodexHelper.exe"\n'
        'HELPER_MANIFEST_NAME = "helper.json"\n'
    )


def rendered_remote_ota_config(cfg: dict) -> str:
    text = REMOTE_OTA_CONFIG.read_text(encoding="utf-8")
    version_pattern = re.compile(r'#define\s+APP_VERSION\s+"[^"]*"')
    if not version_pattern.search(text):
        raise ValueError("APP_VERSION macro not found in src/remote_ota_config.h")
    text = version_pattern.sub(f'#define APP_VERSION "{cfg["version"]}"', text, count=1)

    manifest_pattern = re.compile(r'(#ifndef\s+REMOTE_OTA_MANIFEST_URL\s*\n#define\s+REMOTE_OTA_MANIFEST_URL\s+)"[^"]*"')
    if not manifest_pattern.search(text):
        raise ValueError("REMOTE_OTA_MANIFEST_URL default macro not found in src/remote_ota_config.h")
    return manifest_pattern.sub(rf'\1"{cfg["manifest_url"]}"', text, count=1)


def check_file(path: Path, expected: str) -> bool:
    actual = path.read_text(encoding="utf-8")
    return actual == expected


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync version files from release.json.")
    parser.add_argument("--check", action="store_true", help="Fail if generated files are stale.")
    args = parser.parse_args()

    cfg = load_release_config()
    expected = {
        VERSION_INFO: rendered_version_info(cfg),
        REMOTE_OTA_CONFIG: rendered_remote_ota_config(cfg),
    }

    stale = [path for path, text in expected.items() if not check_file(path, text)]
    if args.check:
        if stale:
            for path in stale:
                print(f"stale: {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        return 0

    for path, text in expected.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"synced {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
