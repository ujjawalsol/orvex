"""Claude Code CLI MCP client adapter."""
from __future__ import annotations

from pathlib import Path
import shutil

from .base import (
    ClientAdapter,
    is_temporary_path,
    load_json_or_jsonc,
    save_json_atomic,
    validate_runtime_paths,
)


class ClaudeCodeAdapter(ClientAdapter):
    name = "claude_code"
    display_name = "Claude Code"

    def detect(self) -> bool:
        if shutil.which("claude") is not None:
            return True
        if (Path.home() / ".claude.json").exists():
            return True
        if (Path.home() / ".claude").is_dir():
            return True
        return False

    def config_location(self) -> Path | None:
        return Path.home() / ".claude.json"

    def is_registered(self, install_dir: Path, allow_temp: bool = False) -> bool:
        loc = self.config_location()
        if not loc or not loc.exists():
            return False
        data = load_json_or_jsonc(loc)
        servers = data.get("mcpServers", {})
        if "orvex" not in servers:
            return False
        entry = servers["orvex"]
        cwd = entry.get("cwd", "")
        if not cwd or (not allow_temp and is_temporary_path(cwd)) or not Path(cwd).exists():
            return False
        if str(Path(cwd).resolve()) != str(install_dir.resolve()):
            return False
        cmd = entry.get("command", "")
        if cmd and ((not allow_temp and is_temporary_path(cmd)) or not Path(cmd).exists()):
            return False
        args = entry.get("args", [])
        return any("engine.server" in str(a) for a in args)

    def register(
        self,
        install_dir: Path,
        python_exe: Path,
        allow_dev: bool = False,
        allow_temp: bool = False,
    ) -> tuple[bool, str]:
        validate_runtime_paths(install_dir, python_exe, allow_dev=allow_dev, allow_temp=allow_temp)
        loc = self.config_location()
        if not loc:
            return False, "Cannot determine Claude Code configuration path"
        try:
            data = load_json_or_jsonc(loc)
            if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
                data["mcpServers"] = {}

            data["mcpServers"]["orvex"] = {
                "command": str(python_exe.resolve()),
                "args": ["-m", "engine.server"],
                "cwd": str(install_dir.resolve()),
            }

            save_json_atomic(loc, data)
            return True, f"Registered globally in {loc}"
        except Exception as e:
            return False, f"Failed to register in Claude Code: {e}"

    def remove(self) -> tuple[bool, str]:
        loc = self.config_location()
        if not loc or not loc.exists():
            return True, "No configuration to remove"
        try:
            data = load_json_or_jsonc(loc)
            if "mcpServers" in data and isinstance(data["mcpServers"], dict):
                if "orvex" in data["mcpServers"]:
                    del data["mcpServers"]["orvex"]
                    save_json_atomic(loc, data)
                    return True, f"Removed from {loc}"
            return True, "Already not registered"
        except Exception as e:
            return False, f"Failed to remove from Claude Code: {e}"

    def validate(self, allow_temp: bool = False) -> tuple[bool, str]:
        loc = self.config_location()
        if not loc or not loc.exists():
            return False, "Config file does not exist"
        data = load_json_or_jsonc(loc)
        servers = data.get("mcpServers", {})
        if "orvex" not in servers:
            return False, "orvex server not found in mcpServers"
        entry = servers["orvex"]
        if not entry.get("command"):
            return False, "Missing command field"
        cwd = entry.get("cwd", "")
        if not cwd or not Path(cwd).exists():
            return False, f"Configured cwd does not exist: {cwd}"
        if not allow_temp and is_temporary_path(cwd):
            return False, f"Configured cwd is a temporary path: {cwd}"
        return True, "Configuration valid"
