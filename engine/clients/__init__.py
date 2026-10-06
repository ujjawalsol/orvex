"""ORVEX client adapters package."""
from .antigravity import AntigravityAdapter
from .base import ClientAdapter, load_json_or_jsonc, save_json_atomic, strip_jsonc_comments
from .claude_code import ClaudeCodeAdapter
from .claude_desktop import ClaudeDesktopAdapter
from .cursor import CursorAdapter
from .detector import (
    SUPPORTED_ADAPTERS,
    ClientStatus,
    format_detection_summary,
    get_adapter,
    get_all_adapters,
    inspect_clients,
    register_detected_clients,
    unregister_all_clients,
)
from .opencode import OpenCodeAdapter
from .vscode import VSCodeAdapter
from .windsurf import WindsurfAdapter

__all__ = [
    "AntigravityAdapter",
    "ClientAdapter",
    "ClientStatus",
    "ClaudeCodeAdapter",
    "ClaudeDesktopAdapter",
    "CursorAdapter",
    "OpenCodeAdapter",
    "SUPPORTED_ADAPTERS",
    "VSCodeAdapter",
    "WindsurfAdapter",
    "format_detection_summary",
    "get_adapter",
    "get_all_adapters",
    "inspect_clients",
    "load_json_or_jsonc",
    "register_detected_clients",
    "save_json_atomic",
    "strip_jsonc_comments",
    "unregister_all_clients",
]
