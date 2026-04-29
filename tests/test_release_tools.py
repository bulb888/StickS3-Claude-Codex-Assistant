import hashlib
import contextlib
import json
import shutil
import sys
import uuid
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from release_tools import (
    build_manifest,
    firmware_release_notes,
    helper_release_notes,
    project_version,
    read_macro,
    release_firmware_url,
    release_manifest_url,
    release_tag_for_version,
    sync_latest_snapshot,
    helper_version,
    release_notes,
    write_release_bundle,
)
from status_logic import clean_text, codex_phase_for_text
from update_check import format_helper_update_summary, format_size, is_newer_version


@contextlib.contextmanager
def temp_workspace():
    base = ROOT / ".test_tmp"
    base.mkdir(exist_ok=True)
    path = base / f"test_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class ReleaseToolsTest(unittest.TestCase):
    def test_read_macro(self):
        self.assertRegex(read_macro(ROOT / "src" / "remote_ota_config.h", "APP_VERSION"), r"^\d+\.\d+\.\d+")

    def test_manifest_contains_hash_size_and_notes(self):
        fw = ROOT / "LICENSE"
        manifest = build_manifest("1.0.0", "https://example.com/firmware.bin", fw, "hello")
        self.assertEqual(manifest["sha256"], hashlib.sha256(fw.read_bytes()).hexdigest())
        self.assertEqual(manifest["size"], fw.stat().st_size)
        self.assertEqual(manifest["notes"], "hello")

    def test_write_release_bundle(self):
        expected = {"version": "1.0.0", "url": "https://example.com/firmware.bin", "sha256": "abc", "size": 3}
        with mock.patch("release_tools.shutil.copy2") as copy2, \
             mock.patch("release_tools.build_manifest", return_value=expected), \
             mock.patch("pathlib.Path.mkdir") as mkdir, \
             mock.patch("pathlib.Path.write_text") as write_text:
            manifest = write_release_bundle(
                ROOT / "LICENSE",
                ROOT / "dist" / "release",
                "1.0.0",
                "https://example.com/firmware.bin",
            )
        self.assertEqual(manifest, expected)
        mkdir.assert_called_once()
        copy2.assert_called_once()
        written = json.loads(write_text.call_args.args[0])
        self.assertEqual(written, expected)

    def test_write_release_bundle_includes_helper_manifest(self):
        with temp_workspace() as tmp:
            root = Path(tmp)
            fw = root / "firmware.bin"
            helper = root / "StickS3ClaudeCodexHelper.exe"
            out = root / "out"
            fw.write_bytes(b"fw")
            helper.write_bytes(b"helper")
            write_release_bundle(
                fw,
                out,
                "1.0.0",
                "https://example.com/releases/latest/download/firmware.bin",
                "notes",
                helper,
                "1.0.1",
            )
            helper_manifest = json.loads((out / "helper.json").read_text(encoding="utf-8"))
            self.assertEqual(helper_manifest["version"], "1.0.1")
            self.assertEqual(helper_manifest["asset"], "StickS3ClaudeCodexHelper.exe")
            self.assertEqual(helper_manifest["notes"], "notes")
            self.assertEqual(helper_manifest["size"], len(b"helper"))
            self.assertIn("StickS3ClaudeCodexHelper.exe", helper_manifest["url"])

    def test_write_release_bundle_can_separate_helper_notes(self):
        with temp_workspace() as tmp:
            root = Path(tmp)
            fw = root / "firmware.bin"
            helper = root / "StickS3ClaudeCodexHelper.exe"
            out = root / "out"
            fw.write_bytes(b"fw")
            helper.write_bytes(b"helper")
            write_release_bundle(
                fw,
                out,
                "1.0.0",
                "https://example.com/releases/latest/download/firmware.bin",
                "firmware notes",
                helper,
                "1.0.1",
                "helper notes",
            )
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            helper_manifest = json.loads((out / "helper.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["notes"], "firmware notes")
            self.assertEqual(helper_manifest["notes"], "helper notes")

    def test_project_version_has_default(self):
        self.assertRegex(project_version(ROOT), r"^\d+\.\d+\.\d+")
        self.assertRegex(helper_version(ROOT), r"^\d+\.\d+\.\d+")
        self.assertTrue(release_notes(ROOT))
        self.assertTrue(firmware_release_notes(ROOT))
        self.assertTrue(helper_release_notes(ROOT))
        self.assertTrue(release_manifest_url(ROOT).endswith("/manifest.json"))
        self.assertTrue(release_firmware_url(ROOT).endswith("/firmware.bin"))

    def test_release_notes_can_be_split(self):
        with temp_workspace() as tmp:
            root = Path(tmp)
            (root / "release.json").write_text(
                json.dumps(
                    {
                        "version": "1.2.3",
                        "helper_version": "1.2.0",
                        "repo": "owner/repo",
                        "notes": "legacy notes",
                        "firmware_notes": "firmware notes",
                        "helper_notes": "helper notes",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.assertEqual(release_notes(root), "firmware notes")
            self.assertEqual(firmware_release_notes(root), "firmware notes")
            self.assertEqual(helper_release_notes(root), "helper notes")
            self.assertEqual(release_manifest_url(root), "https://github.com/owner/repo/releases/latest/download/manifest.json")

    def test_project_version_ignores_local_secrets_version(self):
        with temp_workspace() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "remote_ota_config.h").write_text('#define APP_VERSION "1.2.3"\n', encoding="utf-8")
            (src / "secrets.h").write_text('#define APP_VERSION "9.9.9"\n', encoding="utf-8")
            self.assertEqual(project_version(root), "1.2.3")

    def test_release_tag_for_version(self):
        self.assertEqual(release_tag_for_version("1.2.3"), "v1.2.3")
        self.assertEqual(release_tag_for_version("v1.2.3"), "v1.2.3")

    def test_sync_latest_snapshot(self):
        with temp_workspace() as tmp:
            root = Path(tmp)
            out = root / "dist" / "release"
            out.mkdir(parents=True)
            (out / "firmware.bin").write_bytes(b"fw")
            (out / "manifest.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
            latest = root / "releases" / "latest"
            sync_latest_snapshot(out, latest)
            self.assertEqual((latest / "firmware.bin").read_bytes(), b"fw")
            self.assertEqual(json.loads((latest / "manifest.json").read_text(encoding="utf-8"))["version"], "1.0.0")


class UpdateCheckTest(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(format_size(1024), "1 KB")
        self.assertEqual(format_size(2 * 1024 * 1024), "2.0 MB")

    def test_is_newer_version(self):
        self.assertIs(is_newer_version("1.2.4", "1.2.3"), True)
        self.assertIs(is_newer_version("1.2.3", "1.2.3"), False)

    def test_format_helper_update_summary(self):
        summary = format_helper_update_summary(
            {
                "tag_name": "v1.2.3",
                "body": "hello\n\nAssets:",
                "assets": [{
                    "name": "StickS3ClaudeCodexHelper.exe",
                    "size": 2048,
                    "browser_download_url": "https://example.com/helper.exe",
                }],
            },
            "1.2.2",
        )
        self.assertIn("当前助手: 1.2.2", summary)
        self.assertIn("最新助手: 1.2.3", summary)
        self.assertIn("状态: 发现新版", summary)
        self.assertIn("大小: 2 KB", summary)
        self.assertIn("说明: hello", summary)


class StatusLogicTest(unittest.TestCase):
    def test_codex_phase_markers(self):
        self.assertEqual(codex_phase_for_text("\x01Uhello"), "thinking")
        self.assertEqual(codex_phase_for_text("\x01Cdone"), "idle")
        self.assertEqual(codex_phase_for_text("Bash(ls)"), "running")

    def test_clean_text(self):
        self.assertEqual(clean_text("  hi  "), "hi")
        self.assertEqual(clean_text(None), "")


if __name__ == "__main__":
    unittest.main()
