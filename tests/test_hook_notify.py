import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
