#!/usr/bin/env python3
"""Build and assemble files for a GitHub Release.

Example:
  python helper/prepare_release.py --repo OWNER/REPO --notes "修复电台退出"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from release_tools import (
    firmware_release_notes,
    helper_release_notes,
    helper_version,
    project_version,
    sync_latest_snapshot,
    write_release_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = "m5stack-sticks3-release"


def firmware_url_from_args(args: argparse.Namespace) -> str:
    if args.firmware_url:
        return args.firmware_url
    if args.repo:
        return f"https://github.com/{args.repo}/releases/latest/download/firmware.bin"
    raise SystemExit("请传 --repo OWNER/REPO 或 --firmware-url URL")


def run_build(env: str) -> None:
    cmd = [sys.executable, "-m", "platformio", "run", "-d", str(ROOT), "-e", env]
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare StickS3 release files.")
    parser.add_argument("--repo", help="GitHub repo in OWNER/REPO form.")
    parser.add_argument("--firmware-url", help="Exact firmware.bin download URL.")
    parser.add_argument("--version", help="Override APP_VERSION.")
    parser.add_argument("--notes", help="Short release notes stored in manifest.json.")
    parser.add_argument("--helper-notes", help="Short release notes stored in helper.json.")
    parser.add_argument("--env", default=DEFAULT_ENV, help="PlatformIO environment to build.")
    parser.add_argument("--no-build", action="store_true", help="Use an existing firmware.bin.")
    parser.add_argument("--firmware", type=Path, help="Existing firmware.bin path when --no-build is used.")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    parser.add_argument("--helper-exe", type=Path, default=ROOT / "helper" / "dist" / "StickS3ClaudeCodexHelper.exe")
    parser.add_argument("--sync-latest", action="store_true", help="Copy firmware.bin and manifest.json to releases/latest.")
    args = parser.parse_args()

    if not args.no_build:
        run_build(args.env)

    firmware_path = args.firmware or ROOT / ".pio" / "build" / args.env / "firmware.bin"
    if not firmware_path.exists():
        raise SystemExit(f"找不到固件: {firmware_path}")

    version = args.version or project_version(ROOT)
    notes = args.notes if args.notes is not None else firmware_release_notes(ROOT)
    helper_notes = args.helper_notes if args.helper_notes is not None else helper_release_notes(ROOT)
    manifest = write_release_bundle(
        firmware_path=firmware_path,
        out_dir=args.out,
        version=version,
        firmware_url=firmware_url_from_args(args),
        notes=notes,
        helper_exe=args.helper_exe,
        helper_version=helper_version(ROOT),
        helper_notes=helper_notes,
    )

    print(f"Release files: {args.out}")
    print(f"firmware.bin  {manifest['size']} bytes")
    print(f"sha256        {manifest['sha256']}")
    print(f"version       {manifest['version']}")
    if args.sync_latest:
        latest_dir = ROOT / "releases" / "latest"
        sync_latest_snapshot(args.out, latest_dir)
        print(f"synced        {latest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
