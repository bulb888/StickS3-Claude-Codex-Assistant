#!/usr/bin/env python3
"""Small release helpers for StickS3 firmware bundles."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


def load_release_config(project_root: Path) -> dict:
    path = project_root / "release.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("release.json must be a JSON object")
    return data


def read_macro(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(r'^\s*#\s*define\s+' + re.escape(name) + r'\s+"([^"]*)"', re.MULTILINE)
    match = pattern.search(text)
    return match.group(1) if match else None


def project_version(project_root: Path) -> str:
    value = str(load_release_config(project_root).get("version") or "").strip()
    if value:
        return value
    value = read_macro(project_root / "src" / "remote_ota_config.h", "APP_VERSION")
    if value:
        return value
    return "0.0.0"


def helper_version(project_root: Path) -> str:
    cfg = load_release_config(project_root)
    return str(cfg.get("helper_version") or cfg.get("version") or project_version(project_root)).strip()


def firmware_release_notes(project_root: Path) -> str:
    cfg = load_release_config(project_root)
    return str(cfg.get("firmware_notes") or cfg.get("notes") or "").strip()


def helper_release_notes(project_root: Path) -> str:
    cfg = load_release_config(project_root)
    firmware_notes = str(cfg.get("firmware_notes") or cfg.get("notes") or "").strip()
    return str(cfg.get("helper_notes") or cfg.get("notes") or firmware_notes).strip()


def release_notes(project_root: Path) -> str:
    return firmware_release_notes(project_root)


def release_manifest_url(project_root: Path) -> str:
    repo = release_repo(project_root)
    return f"https://github.com/{repo}/releases/latest/download/manifest.json" if repo else ""


def release_firmware_url(project_root: Path) -> str:
    repo = release_repo(project_root)
    return f"https://github.com/{repo}/releases/latest/download/firmware.bin" if repo else ""


def release_helper_url(project_root: Path) -> str:
    repo = release_repo(project_root)
    return f"https://github.com/{repo}/releases/latest/download/StickS3ClaudeCodexHelper.exe" if repo else ""


def release_repo(project_root: Path) -> str:
    return str(load_release_config(project_root).get("repo") or "").strip()


def release_tag_for_version(version: str) -> str:
    version = version.strip()
    if not version:
        raise ValueError("empty version")
    return version if version.startswith("v") else f"v{version}"


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(version: str, firmware_url: str, firmware_path: Path, notes: str = "") -> dict:
    manifest = {
        "version": version,
        "url": firmware_url,
        "sha256": sha256_file(firmware_path),
        "size": firmware_path.stat().st_size,
    }
    if notes.strip():
        manifest["notes"] = notes.strip()
    return manifest


def build_helper_manifest(
    version: str,
    helper_url: str,
    helper_path: Path,
    notes: str = "",
) -> dict:
    manifest = {
        "version": version,
        "asset": helper_path.name,
        "url": helper_url,
        "sha256": sha256_file(helper_path),
        "size": helper_path.stat().st_size,
    }
    if notes.strip():
        manifest["notes"] = notes.strip()
    return manifest


def write_release_bundle(
    firmware_path: Path,
    out_dir: Path,
    version: str,
    firmware_url: str,
    notes: str = "",
    helper_exe: Path | None = None,
    helper_version: str | None = None,
    helper_notes: str | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_firmware = out_dir / "firmware.bin"
    shutil.copy2(firmware_path, out_firmware)

    manifest = build_manifest(version, firmware_url, out_firmware, notes)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if helper_exe and helper_exe.exists():
        shutil.copy2(helper_exe, out_dir / helper_exe.name)
        helper_url = firmware_url.rsplit("/", 1)[0] + "/" + helper_exe.name
        helper_manifest = build_helper_manifest(
            helper_version or version,
            helper_url,
            helper_exe,
            notes if helper_notes is None else helper_notes,
        )
        (out_dir / "helper.json").write_text(
            json.dumps(helper_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return manifest


def sync_latest_snapshot(release_dir: Path, latest_dir: Path) -> None:
    latest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("firmware.bin", "manifest.json", "helper.json"):
        src = release_dir / name
        if name != "helper.json" and not src.exists():
            raise FileNotFoundError(src)
        if not src.exists():
            continue
        shutil.copy2(src, latest_dir / name)
