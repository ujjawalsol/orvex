"""Test suite for AI client registration and removal."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure root repository directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.clients.antigravity import AntigravityAdapter
from engine.clients.claude_code import ClaudeCodeAdapter
from engine.clients.claude_desktop import ClaudeDesktopAdapter
from engine.clients.cursor import CursorAdapter
from engine.clients.detector import SUPPORTED_ADAPTERS
from engine.clients.opencode import OpenCodeAdapter
from engine.clients.vscode import VSCodeAdapter
from engine.clients.windsurf import WindsurfAdapter


class TestClientRegistration(unittest.TestCase):
    """Verify registration, validation, and removal across all adapters."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.dummy_install = self.temp_path / "orvex_install"
        self.dummy_python = self.temp_path / "venv" / "Scripts" / "python.exe"
        self.dummy_install.mkdir(parents=True, exist_ok=True)
        self.dummy_python.parent.mkdir(parents=True, exist_ok=True)
        self.dummy_python.touch()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_opencode_registration_lifecycle(self):
        adapter = OpenCodeAdapter()
        cfg_file = self.temp_path / "opencode.jsonc"
        with patch.object(adapter, "config_location", return_value=cfg_file):
            self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))

            success, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
            self.assertTrue(success, msg)
            self.assertTrue(adapter.is_registered(self.dummy_install, allow_temp=True))

            valid, val_msg = adapter.validate(allow_temp=True)
            self.assertTrue(valid, val_msg)

            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertIn("mcp", data)
            self.assertIn("servers", data["mcp"])
            self.assertIn("orvex", data["mcp"]["servers"])
            orvex = data["mcp"]["servers"]["orvex"]
            self.assertEqual(orvex["type"], "local")
            self.assertIn("-m", orvex["command"])
            self.assertIn("engine.server", orvex["command"])
            self.assertEqual(orvex["cwd"], str(self.dummy_install.resolve()))

            rem_ok, rem_msg = adapter.remove()
            self.assertTrue(rem_ok, rem_msg)
            self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))

    def test_vscode_registration_lifecycle(self):
        adapter = VSCodeAdapter()
        cfg_file = self.temp_path / "Code" / "User" / "mcp.json"
        with patch.object(adapter, "config_location", return_value=cfg_file):
            self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))

            success, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
            self.assertTrue(success, msg)
            self.assertTrue(adapter.is_registered(self.dummy_install, allow_temp=True))

            valid, val_msg = adapter.validate(allow_temp=True)
            self.assertTrue(valid, val_msg)

            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            # VS Code requires 'servers' root key, not 'mcpServers'
            self.assertIn("servers", data)
            self.assertIn("orvex", data["servers"])
            orvex = data["servers"]["orvex"]
            self.assertEqual(orvex["args"], ["-m", "engine.server"])
            self.assertEqual(orvex["cwd"], str(self.dummy_install.resolve()))

            rem_ok, rem_msg = adapter.remove()
            self.assertTrue(rem_ok, rem_msg)
            self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))

    def test_mcpservers_based_adapters(self):
        """Test adapters using the standard mcpServers schema: Claude Code, Claude Desktop, Antigravity, Cursor, Windsurf."""
        adapters = [
            ClaudeCodeAdapter(),
            ClaudeDesktopAdapter(),
            AntigravityAdapter(),
            CursorAdapter(),
            WindsurfAdapter(),
        ]
        for adapter in adapters:
            cfg_file = self.temp_path / f"{adapter.name}_config.json"
            with patch.object(adapter, "config_location", return_value=cfg_file):
                self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))

                success, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
                self.assertTrue(success, f"Failed for {adapter.name}: {msg}")
                self.assertTrue(adapter.is_registered(self.dummy_install, allow_temp=True))

                valid, val_msg = adapter.validate(allow_temp=True)
                self.assertTrue(valid, f"Validation failed for {adapter.name}: {val_msg}")

                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                self.assertIn("mcpServers", data, f"Missing mcpServers in {adapter.name}")
                self.assertIn("orvex", data["mcpServers"])
                orvex = data["mcpServers"]["orvex"]
                self.assertEqual(orvex["command"], str(self.dummy_python.resolve()))
                self.assertEqual(orvex["args"], ["-m", "engine.server"])
                self.assertEqual(orvex["cwd"], str(self.dummy_install.resolve()))

                rem_ok, rem_msg = adapter.remove()
                self.assertTrue(rem_ok, rem_msg)
                self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))

    def test_strict_rejection_of_temporary_path(self):
        """Hard validation must reject registering any temporary path when allow_temp=False."""
        adapter = OpenCodeAdapter()
        with self.assertRaises(ValueError) as ctx:
            adapter.register(self.dummy_install, self.dummy_python, allow_temp=False)
        self.assertIn("is inside a temporary directory", str(ctx.exception))

    def test_strict_rejection_of_source_repo(self):
        """Hard validation must reject registering developer source repo unless allow_dev=True."""
        adapter = OpenCodeAdapter()
        repo_dir = Path(__file__).resolve().parent.parent
        py_exe = repo_dir / ".venv" / "Scripts" / "python.exe"
        with self.assertRaises(ValueError) as ctx:
            adapter.register(repo_dir, py_exe, allow_dev=False, allow_temp=True)
        self.assertIn("is a developer source repository", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
