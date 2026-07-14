import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

import hook_notify  # noqa: E402


class HookNotifyTests(unittest.TestCase):
    def test_user_prompt_extracts_ide_request_body(self):
        prompt = (
            "# Context from my IDE setup:\n\n"
            "## Active file: SECURITY.md\n\n"
            "## Open tabs:\n"
            "- SECURITY.md: SECURITY.md\n\n"
            "## My request for Codex:\n"
            "按 Enter 提交后也不显示你看下日志显示了什么\n"
        )
        self.assertEqual(
            hook_notify.label_for({
                "hook_event_name": "UserPromptSubmit",
                "prompt": prompt,
            }),
            "\x01U按 Enter 提交后也不显示你看下日志显示了什么",
        )

    def test_user_prompt_without_ide_context_is_unchanged(self):
        self.assertEqual(
            hook_notify.label_for({
                "hook_event_name": "UserPromptSubmit",
                "prompt": "普通手工输入",
            }),
            "\x01U普通手工输入",
        )


class HelperPortTests(unittest.TestCase):
    def test_default_when_no_env_and_no_config(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKS3_HELPER_PORT", None)
            self.assertEqual(hook_notify.helper_port(tmp), 8765)

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"STICKS3_HELPER_PORT": "9001"}):
            self.assertEqual(hook_notify.helper_port(tmp), 9001)

    def test_invalid_env_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"STICKS3_HELPER_PORT": "not-a-port"}):
            self.assertEqual(hook_notify.helper_port(tmp), 8765)

    def test_reads_port_from_config_json(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKS3_HELPER_PORT", None)
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"http_port": 9002}), encoding="utf-8")
            self.assertEqual(hook_notify.helper_port(tmp), 9002)

    def test_ignores_invalid_config_port(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STICKS3_HELPER_PORT", None)
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"http_port": 0}), encoding="utf-8")
            self.assertEqual(hook_notify.helper_port(tmp), 8765)


if __name__ == "__main__":
    unittest.main()
