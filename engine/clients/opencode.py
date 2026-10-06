"""OpenCode global MCP client adapter."""
from __future__ import annotations

import os
from pathlib import Path
import shutil

from .base import (
    ClientAdapter,
    is_temporary_path,
    load_json_or_jsonc,
    save_json_atomic,
    validate_runtime_paths,
)


class OpenCodeAdapter(ClientAdapter):
    name = "opencode"
    display_name = "OpenCode"

    def detect(self) -> bool:
        if shutil.which("opencode") is not None:
            return True
        config_dir = Path.home() / ".config" / "opencode"
        if config_dir.exists():
            return True
        appdata_dir = Path(os.environ.get("APPDATA", "")) / "opencode"
        return appdata_dir.exists()

    def config_location(self) -> Path | None:
        primary = Path.home() / ".config" / "opencode" / "opencode.jsonc"
        if primary.exists():
            return primary
        fallback = Path.home() / ".config" / "opencode" / "opencode.json"
        if fallback.exists():
            return fallback
        return primary

    def is_registered(self, install_dir: Path, allow_temp: bool = False) -> bool:
        loc = self.config_location()
        if not loc or not loc.exists():
            return False
        data = load_json_or_jsonc(loc)
        servers = data.get("mcp", {}).get("servers", {})
        if "orvex" not in servers:
            return False
        entry = servers["orvex"]
        cmd = entry.get("command", [])
        cwd = entry.get("cwd", "")
        # If registered cwd points to temp or doesn't exist, it is stale
        if not cwd or (not allow_temp and is_temporary_path(cwd)) or not Path(cwd).exists():
            return False
        if str(Path(cwd).resolve()) != str(install_dir.resolve()):
            return False
        if isinstance(cmd, list) and any("engine.server" in str(arg) for arg in cmd):
            cmd_py = cmd[0] if cmd else ""
            if cmd_py and ((not allow_temp and is_temporary_path(cmd_py)) or not Path(cmd_py).exists()):
                return False
            return True
        return False

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
            return False, "OpenCode configuration directory not found"
        try:
            # 1. Register ONLY in canonical global config (~/.config/opencode/opencode.jsonc)
            data = load_json_or_jsonc(loc)
            if "mcp" not in data or not isinstance(data["mcp"], dict):
                data["mcp"] = {}
            if "servers" not in data["mcp"] or not isinstance(data["mcp"]["servers"], dict):
                data["mcp"]["servers"] = {}

            # Preserve all other servers, set orvex to stable runtime
            data["mcp"]["servers"]["orvex"] = {
                "type": "local",
                "command": [
                    str(python_exe.resolve()),
                    "-m",
                    "engine.server",
                ],
                "cwd": str(install_dir.resolve()),
            }
            save_json_atomic(loc, data)

            # 2. Prevent duplicate/conflicting registrations:
            # If user has an accidental orvex in ~/opencode.json, cleanly remove ONLY orvex, preserving blender
            home_cfg = Path.home() / "opencode.json"
            if home_cfg.exists() and home_cfg != loc:
                home_data = load_json_or_jsonc(home_cfg)
                if "mcp" in home_data and isinstance(home_data["mcp"], dict):
                    home_servers = home_data["mcp"].get("servers", {})
                    if "orvex" in home_servers:
                        del home_servers["orvex"]
                        save_json_atomic(home_cfg, home_data)

            return True, f"Registered globally in {loc}"
        except Exception as e:
            return False, f"Failed to register in OpenCode: {e}"

    def remove(self) -> tuple[bool, str]:
        loc = self.config_location()
        removed_any = False
        try:
            targets = [loc] if loc and loc.exists() else []
            home_cfg = Path.home() / "opencode.json"
            if home_cfg.exists() and home_cfg not in targets:
                targets.append(home_cfg)

            for target in targets:
                data = load_json_or_jsonc(target)
                if "mcp" in data and isinstance(data["mcp"], dict):
                    servers = data["mcp"].get("servers", {})
                    if "orvex" in servers:
                        del servers["orvex"]
                        save_json_atomic(target, data)
                        removed_any = True

            if removed_any:
                return True, f"Removed from OpenCode configuration"
            return True, "No configuration to remove"
        except Exception as e:
            return False, f"Failed to remove from OpenCode: {e}"

    def validate(self, allow_temp: bool = False) -> tuple[bool, str]:
        loc = self.config_location()
        if not loc or not loc.exists():
            return False, "Config file does not exist"
        data = load_json_or_jsonc(loc)
        servers = data.get("mcp", {}).get("servers", {})
        if "orvex" not in servers:
            return False, "orvex server not found in mcp.servers"
        entry = servers["orvex"]
        if entry.get("type") != "local":
            return False, f"Invalid type: {entry.get('type')}"
        if not entry.get("command"):
            return False, "Missing command field"
        cwd = entry.get("cwd", "")
        if not cwd or not Path(cwd).exists():
            return False, f"Configured cwd does not exist: {cwd}"
        if not allow_temp and is_temporary_path(cwd):
            return False, f"Configured cwd is a temporary path: {cwd}"
        return True, "Configuration valid"
