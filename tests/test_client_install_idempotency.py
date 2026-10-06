"""Test suite for client installation idempotency."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure root repository directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.clients.detector import SUPPORTED_ADAPTERS


class TestClientInstallIdempotency(unittest.TestCase):
    """Ensure running registration or unregistration repeatedly is strictly idempotent."""

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

    def test_all_adapters_repeated_registration_idempotency(self):
        """Repeatedly registering 5 times across every adapter must result in exactly 1 orvex entry."""
        for adapter in SUPPORTED_ADAPTERS:
            cfg_file = self.temp_path / f"idempotent_{adapter.name}.json"
            with patch.object(adapter, "config_location", return_value=cfg_file):
                # Run register 5 times
                for i in range(5):
                    ok, msg = adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
                    self.assertTrue(ok, f"Registration {i+1} failed for {adapter.name}: {msg}")

                # Read JSON
                data = json.loads(cfg_file.read_text(encoding="utf-8"))

                # Determine server map
                if adapter.name == "opencode":
                    servers = data.get("mcp", {}).get("servers", {})
                elif adapter.name == "vscode":
                    servers = data.get("servers", {})
                else:
                    servers = data.get("mcpServers", {})

                # Verify exactly 1 server key and it is 'orvex'
                orvex_keys = [k for k in servers.keys() if "orvex" in k.lower()]
                self.assertEqual(
                    orvex_keys,
                    ["orvex"],
                    f"Duplicate or malformed orvex keys found in {adapter.name}: {orvex_keys}",
                )
                self.assertEqual(len(servers), 1)

                # Validate
                valid, val_msg = adapter.validate(allow_temp=True)
                self.assertTrue(valid, val_msg)

    def test_repeated_removal_idempotency(self):
        """Repeated unregistration calls should return cleanly without error."""
        for adapter in SUPPORTED_ADAPTERS:
            cfg_file = self.temp_path / f"idempotent_rem_{adapter.name}.json"
            with patch.object(adapter, "config_location", return_value=cfg_file):
                # Register once
                adapter.register(self.dummy_install, self.dummy_python, allow_temp=True)
                self.assertTrue(adapter.is_registered(self.dummy_install, allow_temp=True))

                # Remove 3 times
                for _ in range(3):
                    ok, msg = adapter.remove()
                    self.assertTrue(ok, f"Repeated removal failed for {adapter.name}: {msg}")

                self.assertFalse(adapter.is_registered(self.dummy_install, allow_temp=True))


if __name__ == "__main__":
    unittest.main()
