#!/usr/bin/env python3
"""Build, package, and publish a StickS3 GitHub Release.

This uses the local Git Credential Manager entry for github.com instead of
the gh CLI token. It never prints or stores the credential.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from prepare_release import DEFAULT_ENV, ROOT, firmware_url_from_args, run_build
from release_tools import (
    firmware_release_notes,
    helper_release_notes,
    helper_version,
    load_manifest,
    project_version,
    release_notes,
    release_tag_for_version,
    sync_latest_snapshot,
    write_release_bundle,
)


API_ROOT = "https://api.github.com"
UPLOAD_ROOT = "https://uploads.github.com"
USER_AGENT = "StickS3ReleasePublisher"


class GithubError(RuntimeError):
    pass


def github_auth_header() -> str:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        raise GithubError(f"git credential fill failed: {detail}")

    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value

    username = fields.get("username") or "x-access-token"
    password = fields.get("password")
    if not password:
        raise GithubError("GitHub credential not found. Confirm git push works on this machine.")

    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def github_request(
    method: str,
    url: str,
    auth_header: str,
    *,
    json_body: dict | None = None,
    raw_body: bytes | None = None,
    content_type: str = "application/json",
    allowed: tuple[int, ...] = (200,),
) -> tuple[int, object, dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": auth_header,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = raw_body
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif raw_body is not None:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            body = resp.read()
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read(4096)
        resp_headers = dict(e.headers)

    text = body.decode("utf-8", errors="replace") if body else ""
    if status not in allowed:
        raise GithubError(f"GitHub {method} {url} -> HTTP {status}: {text[:600]}")
    if not text:
        return status, None, resp_headers
    try:
        return status, json.loads(text), resp_headers
    except json.JSONDecodeError:
        return status, text, resp_headers


def release_by_tag(auth_header: str, repo: str, tag: str) -> dict | None:
    tag_q = urllib.parse.quote(tag, safe="")
    status, body, _ = github_request(
        "GET",
        f"{API_ROOT}/repos/{repo}/releases/tags/{tag_q}",
        auth_header,
        allowed=(200, 404),
    )
    return body if status == 200 and isinstance(body, dict) else None


def create_or_update_release(
    auth_header: str,
    repo: str,
    tag: str,
    title: str,
    body: str,
    *,
    draft: bool,
    prerelease: bool,
    target_commitish: str | None,
) -> tuple[dict, bool]:
    existing = release_by_tag(auth_header, repo, tag)
    payload = {
        "tag_name": tag,
        "name": title,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
    }
    if target_commitish:
        payload["target_commitish"] = target_commitish

    if existing:
        release_id = existing["id"]
        _, body_obj, _ = github_request(
            "PATCH",
            f"{API_ROOT}/repos/{repo}/releases/{release_id}",
            auth_header,
            json_body=payload,
            allowed=(200,),
        )
        return body_obj, False

    _, body_obj, _ = github_request(
        "POST",
        f"{API_ROOT}/repos/{repo}/releases",
        auth_header,
        json_body=payload,
        allowed=(201,),
    )
    return body_obj, True


def release_body_from_manifest(manifest: dict, notes: str | None) -> str:
    body_notes = notes if notes is not None else str(manifest.get("notes") or "").strip()
    lines: list[str] = []
    if body_notes:
        lines.append(body_notes)
        lines.append("")
    lines.append("Assets:")
    lines.append("- firmware.bin")
    lines.append("- manifest.json")
    lines.append("- StickS3ClaudeCodexHelper.exe")
    lines.append("- helper.json")
    return "\n".join(lines)


def asset_paths(release_dir: Path) -> list[Path]:
    required = [release_dir / "firmware.bin", release_dir / "manifest.json"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    paths = list(required)
    helper_exe = release_dir / "StickS3ClaudeCodexHelper.exe"
    if helper_exe.exists():
        paths.append(helper_exe)
        helper_manifest = release_dir / "helper.json"
        if not helper_manifest.exists():
            raise FileNotFoundError(helper_manifest)
        paths.append(helper_manifest)
    return paths


def upload_asset(
    auth_header: str,
    repo: str,
    release: dict,
    path: Path,
    *,
    replace: bool,
) -> None:
    name = path.name
    assets = release.get("assets") or []
    existing = next((a for a in assets if a.get("name") == name), None)
    if existing:
        if not replace:
            raise GithubError(f"asset already exists: {name}")
        github_request(
            "DELETE",
            f"{API_ROOT}/repos/{repo}/releases/assets/{existing['id']}",
            auth_header,
            allowed=(204,),
        )

    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if not upload_url:
        upload_url = f"{UPLOAD_ROOT}/repos/{repo}/releases/{release['id']}/assets"
    query = urllib.parse.urlencode({"name": name})
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    github_request(
        "POST",
        f"{upload_url}?{query}",
        auth_header,
        raw_body=path.read_bytes(),
        content_type=content_type,
        allowed=(201,),
    )


def prepare_bundle(args: argparse.Namespace) -> dict:
    if args.skip_prepare:
        return load_manifest(args.out / "manifest.json")

    if not args.no_build:
        run_build(args.env)

    firmware_path = args.firmware or ROOT / ".pio" / "build" / args.env / "firmware.bin"
    if not firmware_path.exists():
        raise FileNotFoundError(firmware_path)

    version = args.version or project_version(ROOT)
    notes = args.notes if args.notes is not None else firmware_release_notes(ROOT)
    helper_notes = args.helper_notes if args.helper_notes is not None else helper_release_notes(ROOT)
    return write_release_bundle(
        firmware_path=firmware_path,
        out_dir=args.out,
        version=version,
        firmware_url=firmware_url_from_args(args),
        notes=notes,
        helper_exe=args.helper_exe,
        helper_version=helper_version(ROOT),
        helper_notes=helper_notes,
    )


def run_checked(cmd: list[str], *, cwd: Path = ROOT) -> None:
    print("running:", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def git_output(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def preflight(args: argparse.Namespace, tag: str) -> None:
    if not args.allow_dirty:
        dirty = git_output(["status", "--porcelain", "--untracked-files=no"])
        if dirty:
            raise GithubError("tracked worktree changes exist; commit before publishing or pass --allow-dirty")

    run_checked([sys.executable, "helper/version_sync.py", "--check"])
    run_checked([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    run_checked([
        sys.executable, "-m", "py_compile",
        "helper/type_server.py",
        "helper/update_check.py",
        "helper/publish_release.py",
        "helper/release_tools.py",
        "helper/prepare_release.py",
        "helper/status_logic.py",
        "helper/version_info.py",
        "helper/version_sync.py",
    ])
    if args.skip_prepare and not args.skip_preflight_build:
        run_build(args.env)

    if args.helper_exe and not args.helper_exe.exists():
        raise GithubError(f"helper exe missing: {args.helper_exe}")

    try:
        tag_sha = git_output(["rev-list", "-n", "1", tag])
    except subprocess.CalledProcessError:
        tag_sha = ""
    if tag_sha:
        head_sha = git_output(["rev-parse", "HEAD"])
        if tag_sha != head_sha:
            raise GithubError(f"tag {tag} points at {tag_sha[:12]}, not HEAD {head_sha[:12]}")


def verify_uploaded_assets(auth_header: str, repo: str, tag: str, expected_paths: list[Path]) -> None:
    release = release_by_tag(auth_header, repo, tag)
    if not release:
        raise GithubError(f"release not found after upload: {tag}")
    assets = {asset.get("name"): asset for asset in release.get("assets") or [] if isinstance(asset, dict)}
    for path in expected_paths:
        asset = assets.get(path.name)
        if not asset:
            raise GithubError(f"asset missing after upload: {path.name}")
        if int(asset.get("size") or -1) != path.stat().st_size:
            raise GithubError(f"asset size mismatch after upload: {path.name}")
    print("Verified uploaded assets:", ", ".join(path.name for path in expected_paths))


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish StickS3 firmware to GitHub Releases.")
    parser.add_argument("--repo", required=True, help="GitHub repo in OWNER/REPO form.")
    parser.add_argument("--firmware-url", help="Exact firmware.bin download URL.")
    parser.add_argument("--version", help="Override APP_VERSION.")
    parser.add_argument("--tag", help="Release tag. Defaults to v<version>.")
    parser.add_argument("--title", help="Release title. Defaults to tag.")
    parser.add_argument("--notes", help="Release notes and manifest notes.")
    parser.add_argument("--helper-notes", help="Helper manifest notes. Defaults to release.json helper_notes.")
    parser.add_argument("--env", default=DEFAULT_ENV, help="PlatformIO environment to build.")
    parser.add_argument("--no-build", action="store_true", help="Use an existing firmware.bin.")
    parser.add_argument("--firmware", type=Path, help="Existing firmware.bin path when --no-build is used.")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "release")
    parser.add_argument("--helper-exe", type=Path, default=ROOT / "helper" / "dist" / "StickS3ClaudeCodexHelper.exe")
    parser.add_argument("--skip-prepare", action="store_true", help="Publish existing files from --out.")
    parser.add_argument("--sync-latest", action="store_true", help="Copy firmware.bin and manifest.json to releases/latest.")
    parser.add_argument("--draft", action="store_true", help="Create/update as draft release.")
    parser.add_argument("--prerelease", action="store_true", help="Mark as prerelease.")
    parser.add_argument("--target", help="Target commitish for a new tag/release.")
    parser.add_argument("--no-replace-assets", action="store_true", help="Fail if an asset with the same name exists.")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip tests/version checks before publishing.")
    parser.add_argument("--skip-preflight-build", action="store_true", help="Skip preflight release build when using --skip-prepare.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow tracked worktree changes during publish.")
    args = parser.parse_args()

    version = args.version or project_version(ROOT)
    tag = args.tag or release_tag_for_version(version)
    if not args.skip_preflight:
        preflight(args, tag)

    manifest = prepare_bundle(args)
    if args.sync_latest:
        sync_latest_snapshot(args.out, ROOT / "releases" / "latest")

    title = args.title or tag
    body = release_body_from_manifest(manifest, args.notes if args.notes is not None else release_notes(ROOT))

    auth_header = github_auth_header()
    release, created = create_or_update_release(
        auth_header,
        args.repo,
        tag,
        title,
        body,
        draft=args.draft,
        prerelease=args.prerelease,
        target_commitish=args.target,
    )
    action = "created" if created else "updated"
    print(f"Release {action}: {release.get('html_url')}")

    paths = asset_paths(args.out)
    for path in paths:
        upload_asset(
            auth_header,
            args.repo,
            release,
            path,
            replace=not args.no_replace_assets,
        )
        print(f"Uploaded: {path.name} ({path.stat().st_size} bytes)")
    verify_uploaded_assets(auth_header, args.repo, tag, paths)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GithubError, OSError, ValueError) as e:
        print(f"publish failed: {e}", file=sys.stderr)
        raise SystemExit(1)
