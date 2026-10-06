"""Test suite for verifying existing client MCP configuration preservation."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure root repository directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.clients.claude_desktop import ClaudeDesktopAdapter
from engine.clients.opencode import OpenCodeAdapter
from engine.clients.vscode import VSCodeAdapter
from engine.clients.antigravity import AntigravityAdapter


class TestClientConfigPreservation(unittest.TestCase):
    """Ensure ORVEX never overwrites or destroys existing user MCP configurations."""

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

    def test_claude_desktop_preserves_existing_mcps(self):
        adapter = ClaudeDesktopAdapter()
        cfg_file = self.temp_path / "claude_desktop_config.json"

        # Pre-existing configuration with multiple custom MCP servers and extra fields
        original_data = {
            "mcpServers": {
                "blender_mcp": {
                    "command": "python",
                    "args": ["-m", "blender_mcp_server"],
                    "env": {"BLENDER_PORT": "8181"},
                },
                "github_mcp": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret"},
                },
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Projects"],
                },
            },
            "customUserSetting": True,
            "theme": "dark",
        }
        cfg_file.write_text(json.dumps(original_data, indent=2), encoding="utf-8")

        with patch.object(adapter, "config_location", return_value=cfg_file):
            # Register ORVEX
            ok, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
            self.assertTrue(ok, msg)

            # Read back configuration
            after_reg = json.loads(cfg_file.read_text(encoding="utf-8"))

            # Verify original non-MCP fields preserved
            self.assertEqual(after_reg.get("customUserSetting"), True)
            self.assertEqual(after_reg.get("theme"), "dark")

            # Verify existing servers are exactly preserved
            servers = after_reg.get("mcpServers", {})
            self.assertIn("blender_mcp", servers)
            self.assertEqual(servers["blender_mcp"], original_data["mcpServers"]["blender_mcp"])
            self.assertIn("github_mcp", servers)
            self.assertEqual(servers["github_mcp"], original_data["mcpServers"]["github_mcp"])
            self.assertIn("filesystem", servers)
            self.assertEqual(servers["filesystem"], original_data["mcpServers"]["filesystem"])

            # Verify ORVEX is added alongside them
            self.assertIn("orvex", servers)

            # Now test unregistration leaves existing servers intact
            rem_ok, rem_msg = adapter.remove()
            self.assertTrue(rem_ok, rem_msg)

            after_rem = json.loads(cfg_file.read_text(encoding="utf-8"))
            servers_rem = after_rem.get("mcpServers", {})
            self.assertNotIn("orvex", servers_rem)
            self.assertIn("blender_mcp", servers_rem)
            self.assertIn("github_mcp", servers_rem)
            self.assertIn("filesystem", servers_rem)
            self.assertEqual(servers_rem["blender_mcp"], original_data["mcpServers"]["blender_mcp"])

    def test_opencode_preserves_existing_tools_and_jsonc(self):
        adapter = OpenCodeAdapter()
        cfg_file = self.temp_path / "opencode.jsonc"

        # Pre-existing JSONC content with comments
        jsonc_content = """// OpenCode Configuration File
{
  /* User general preferences */
  "theme": "nord",
  "mcp": {
    "servers": {
      "context7": {
        "type": "local",
        "command": ["context7-cli"],
        "cwd": "C:\\\\tools"
      }
    }
  }
}
"""
        cfg_file.write_text(jsonc_content, encoding="utf-8")

        with patch.object(adapter, "config_location", return_value=cfg_file):
            ok, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
            self.assertTrue(ok, msg)

            after_reg = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertEqual(after_reg.get("theme"), "nord")
            servers = after_reg.get("mcp", {}).get("servers", {})
            self.assertIn("context7", servers)
            self.assertEqual(servers["context7"]["command"], ["context7-cli"])
            self.assertIn("orvex", servers)

            rem_ok, _ = adapter.remove()
            self.assertTrue(rem_ok)

            after_rem = json.loads(cfg_file.read_text(encoding="utf-8"))
            servers_rem = after_rem.get("mcp", {}).get("servers", {})
            self.assertNotIn("orvex", servers_rem)
            self.assertIn("context7", servers_rem)

    def test_vscode_preserves_existing_servers(self):
        adapter = VSCodeAdapter()
        cfg_file = self.temp_path / "mcp.json"

        original_data = {
            "servers": {
                "copilot_db": {
                    "command": "copilot-db-agent",
                    "args": ["--port", "9000"],
                }
            }
        }
        cfg_file.write_text(json.dumps(original_data, indent=2), encoding="utf-8")

        with patch.object(adapter, "config_location", return_value=cfg_file):
            ok, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
            self.assertTrue(ok, msg)

            after_reg = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertIn("copilot_db", after_reg["servers"])
            self.assertIn("orvex", after_reg["servers"])

            rem_ok, _ = adapter.remove()
            self.assertTrue(rem_ok)

            after_rem = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertNotIn("orvex", after_rem["servers"])
            self.assertIn("copilot_db", after_rem["servers"])


if __name__ == "__main__":
    unittest.main()
