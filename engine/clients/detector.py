"""Client detector and batch registration manager."""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from .antigravity import AntigravityAdapter
from .base import ClientAdapter
from .claude_code import ClaudeCodeAdapter
from .claude_desktop import ClaudeDesktopAdapter
from .cursor import CursorAdapter
from .opencode import OpenCodeAdapter
from .vscode import VSCodeAdapter
from .windsurf import WindsurfAdapter

SUPPORTED_ADAPTERS: list[ClientAdapter] = [
    OpenCodeAdapter(),
    ClaudeCodeAdapter(),
    VSCodeAdapter(),
    CursorAdapter(),
    WindsurfAdapter(),
    AntigravityAdapter(),
    ClaudeDesktopAdapter(),
]


class ClientStatus(NamedTuple):
    adapter: ClientAdapter
    detected: bool
    registered: bool
    config_path: Path | None
    message: str = ""


def get_all_adapters() -> list[ClientAdapter]:
    return list(SUPPORTED_ADAPTERS)


def get_adapter(name: str) -> ClientAdapter | None:
    for adapter in SUPPORTED_ADAPTERS:
        if adapter.name == name:
            return adapter
    return None


def inspect_clients(install_dir: Path) -> list[ClientStatus]:
    """Inspect all supported clients on the system."""
    results = []
    for adapter in SUPPORTED_ADAPTERS:
        detected = adapter.detect()
        registered = adapter.is_registered(install_dir) if detected else False
        config_path = adapter.config_location()
        results.append(
            ClientStatus(
                adapter=adapter,
                detected=detected,
                registered=registered,
                config_path=config_path,
            )
        )
    return results


def register_detected_clients(
    install_dir: Path,
    python_exe: Path,
    allow_dev: bool = False,
    allow_temp: bool = False,
) -> list[tuple[ClientAdapter, bool, str]]:
    """Register ORVEX with all clients detected on the current system.
    
    Preserves all existing client configurations and other MCP servers.
    Idempotent: safe to run repeatedly.
    """
    results = []
    for adapter in SUPPORTED_ADAPTERS:
        if not adapter.detect():
            continue
        success, msg = adapter.register(
            install_dir,
            python_exe,
            allow_dev=allow_dev,
            allow_temp=allow_temp,
        )
        results.append((adapter, success, msg))
    return results


def unregister_all_clients() -> list[tuple[ClientAdapter, bool, str]]:
    """Unregister ORVEX from all supported clients without touching other servers."""
    results = []
    for adapter in SUPPORTED_ADAPTERS:
        # Only attempt removal if client exists or config file exists
        if adapter.detect() or (adapter.config_location() and adapter.config_location().exists()):
            success, msg = adapter.remove()
            results.append((adapter, success, msg))
    return results


def format_detection_summary(install_dir: Path) -> str:
    """Format human-readable detection/registration table."""
    statuses = inspect_clients(install_dir)
    lines = [
        "AI CLIENTS",
        "",
    ]
    for s in statuses:
        if s.detected:
            if s.registered:
                status_str = "Connected"
                mark = "✓"
            else:
                status_str = "Detected"
                mark = "•"
        else:
            status_str = "Not installed"
            mark = "—"
        lines.append(f" {mark} {s.adapter.display_name:<18} {status_str}")
    return "\n".join(lines)
