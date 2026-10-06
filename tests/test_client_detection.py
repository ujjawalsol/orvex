"""Test suite for AI client detection."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Ensure root repository directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.clients.antigravity import AntigravityAdapter
from engine.clients.claude_code import ClaudeCodeAdapter
from engine.clients.claude_desktop import ClaudeDesktopAdapter
from engine.clients.cursor import CursorAdapter
from engine.clients.detector import (
    SUPPORTED_ADAPTERS,
    get_adapter,
    get_all_adapters,
    inspect_clients,
)
from engine.clients.opencode import OpenCodeAdapter
from engine.clients.vscode import VSCodeAdapter
from engine.clients.windsurf import WindsurfAdapter


class TestClientDetection(unittest.TestCase):
    """Verify detection logic for all supported AI clients."""

    def test_all_supported_adapters_present(self):
        """Ensure all 7 core adapters are registered in detector."""
        names = {a.name for a in SUPPORTED_ADAPTERS}
        expected = {
            "opencode",
            "claude_code",
            "vscode",
            "cursor",
            "windsurf",
            "antigravity",
            "claude_desktop",
        }
        self.assertEqual(names, expected)
        self.assertEqual(len(SUPPORTED_ADAPTERS), 7)

    def test_get_adapter(self):
        """Ensure adapter lookup by name works."""
        self.assertIsInstance(get_adapter("opencode"), OpenCodeAdapter)
        self.assertIsInstance(get_adapter("vscode"), VSCodeAdapter)
        self.assertIsInstance(get_adapter("claude_desktop"), ClaudeDesktopAdapter)
        self.assertIsInstance(get_adapter("antigravity"), AntigravityAdapter)
        self.assertIsNone(get_adapter("nonexistent_client"))

    def test_detection_never_raises(self):
        """Detect method on any adapter must return bool and never raise."""
        for adapter in SUPPORTED_ADAPTERS:
            try:
                res = adapter.detect()
                self.assertIsInstance(res, bool)
            except Exception as e:
                self.fail(f"adapter.detect() raised unexpectedly for {adapter.name}: {e}")

    def test_missing_client_graceful_handling(self):
        """Simulate a completely missing client and verify detect() returns False."""
        adapter = CursorAdapter()
        with patch("shutil.which", return_value=None), \
             patch.object(Path, "is_dir", return_value=False), \
             patch.dict("os.environ", {"APPDATA": "", "LOCALAPPDATA": ""}):
            self.assertFalse(adapter.detect())

    def test_inspect_clients_output(self):
        """inspect_clients returns structured info for all adapters."""
        repo_root = Path(__file__).resolve().parent.parent
        statuses = inspect_clients(repo_root)
        self.assertEqual(len(statuses), 7)
        for s in statuses:
            self.assertIsInstance(s.detected, bool)
            self.assertIsInstance(s.registered, bool)
            if not s.detected:
                self.assertFalse(s.registered)


if __name__ == "__main__":
    unittest.main()
