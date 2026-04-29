#!/usr/bin/env python3
"""Assistant update checks shared by the Windows helper UI."""

from __future__ import annotations

import json
import hashlib
import re
import urllib.request
from pathlib import Path

from version_info import HELPER_ASSET_NAME, HELPER_MANIFEST_NAME, RELEASE_API_URL, RELEASE_PAGE_URL, RELEASE_REPO

DEFAULT_REPO = RELEASE_REPO
DEFAULT_RELEASE_API_URL = RELEASE_API_URL
HELPER_RELEASE_PAGE = RELEASE_PAGE_URL


def fetch_latest_release(url: str = DEFAULT_RELEASE_API_URL, timeout: int = 8) -> dict:
    url = (url or "").strip()
    if not url:
        raise ValueError("release API URL is empty")
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "StickS3ClaudeCodexHelper",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2 * 1024 * 1024)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("release response is not a JSON object")
    return data


def fetch_json_url(url: str, timeout: int = 8) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "StickS3ClaudeCodexHelper"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1024 * 1024)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON response is not an object: {url}")
    return data


def normalized_version(version: object) -> str:
    text = str(version or "").strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    return text


def version_key(version: object) -> tuple[int, ...] | None:
    text = normalized_version(version)
    if not re.fullmatch(r"\d+(?:\.\d+)*", text):
        return None
    return tuple(int(part) for part in text.split("."))


def is_newer_version(latest: object, current: object) -> bool | None:
    latest_key = version_key(latest)
    current_key = version_key(current)
    if latest_key is None or current_key is None:
        return None
    width = max(len(latest_key), len(current_key))
    latest_key += (0,) * (width - len(latest_key))
    current_key += (0,) * (width - len(current_key))
    return latest_key > current_key


def find_asset(release: dict, name: str = HELPER_ASSET_NAME) -> dict | None:
    assets = release.get("assets") or []
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name:
            return asset
    return None


def asset_sha256(asset: dict | None) -> str:
    if not asset:
        return ""
    digest = str(asset.get("digest") or "").strip()
    if digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    return ""


def helper_manifest_for_release(release: dict) -> dict:
    manifest_asset = find_asset(release, HELPER_MANIFEST_NAME)
    url = str((manifest_asset or {}).get("browser_download_url") or "").strip()
    if not url:
        return {}
    try:
        return fetch_json_url(url)
    except Exception:
        return {}


def helper_update_info(release: dict, current_version: str) -> dict:
    latest_tag = str(release.get("tag_name") or release.get("name") or "")
    latest_version = normalized_version(latest_tag)
    asset = find_asset(release, HELPER_ASSET_NAME)
    helper_meta = helper_manifest_for_release(release)
    if helper_meta:
        latest_version = normalized_version(helper_meta.get("version") or latest_version)

    expected_sha = str(helper_meta.get("sha256") or asset_sha256(asset)).lower().strip()
    size = helper_meta.get("size") if helper_meta else (asset or {}).get("size")
    url = str(helper_meta.get("url") or (asset or {}).get("browser_download_url") or "").strip()
    notes = str(helper_meta.get("notes") or release.get("body") or "").strip()
    newer = is_newer_version(latest_version, current_version)
    return {
        "current_version": normalized_version(current_version),
        "latest_version": latest_version or latest_tag,
        "is_newer": newer,
        "asset": HELPER_ASSET_NAME,
        "url": url,
        "sha256": expected_sha,
        "size": int(size or 0),
        "notes": notes,
        "release_page": str(release.get("html_url") or HELPER_RELEASE_PAGE),
        "has_asset": bool(asset),
        "has_manifest": bool(helper_meta),
    }


def format_size(size: object) -> str:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


def format_helper_update_summary(
    release: dict,
    current_version: str,
    asset_name: str = HELPER_ASSET_NAME,
) -> str:
    info = helper_update_info(release, current_version)
    latest_version = info["latest_version"]
    current = info["current_version"]
    lines = [
        f"当前助手: {current or '未知'}",
        f"最新助手: {latest_version or '未知'}",
    ]
    newer = info["is_newer"]
    if newer is True:
        lines.append("状态: 发现新版")
    elif newer is False:
        lines.append("状态: 已是最新版")

    if not info["has_asset"]:
        lines.append(f"未找到资产: {asset_name}")
        lines.append(f"发布页: {info['release_page']}")
        return "\n".join(lines)

    size = format_size(info["size"])
    if size:
        lines.append(f"大小: {size}")
    if info["sha256"]:
        lines.append(f"SHA256: {info['sha256'][:12]}...")
    notes = info["notes"]
    if notes:
        first_line = notes.splitlines()[0].strip()
        if first_line:
            lines.append(f"说明: {first_line[:120]}")
    if info["url"]:
        lines.append(f"下载: {info['url']}")
    return "\n".join(lines)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_helper_update(info: dict, dest: Path, timeout: int = 60) -> str:
    url = str(info.get("url") or "").strip()
    if not url:
        raise ValueError("helper download URL is empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "StickS3ClaudeCodexHelper"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    expected_size = int(info.get("size") or 0)
    if expected_size and dest.stat().st_size != expected_size:
        raise ValueError(f"download size mismatch: {dest.stat().st_size}/{expected_size}")
    actual_sha = sha256_file(dest)
    expected_sha = str(info.get("sha256") or "").lower().strip()
    if expected_sha and actual_sha.lower() != expected_sha:
        raise ValueError(f"SHA256 mismatch: {actual_sha[:12]} != {expected_sha[:12]}")
    return actual_sha
