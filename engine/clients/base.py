"""Base classes and utilities for AI client MCP registration."""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import re
import tempfile


def strip_jsonc_comments(content: str) -> str:
    """Safely strip single-line and multi-line comments from JSONC text."""
    pattern = r'("(?:\\.|[^"\\])*")|//.*?$|/\*.*?\*/'

    def replace(match: re.Match) -> str:
        if match.group(1):
            return match.group(1)
        return ""

    return re.sub(pattern, replace, content, flags=re.MULTILINE | re.DOTALL)


def load_json_or_jsonc(path: Path) -> dict:
    """Load JSON or JSONC file, returning empty dict if missing or invalid."""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return {}
        cleaned = strip_jsonc_comments(raw)
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json_atomic(path: Path, data: dict) -> None:
    """Atomically write formatted JSON to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def is_temporary_path(path: Path | str) -> bool:
    """Return True if path resolves inside a temporary directory, cache, or zip extraction."""
    resolved = str(Path(path).resolve()).lower()
    temp_dirs = [
        tempfile.gettempdir().lower(),
        os.environ.get("TEMP", "").lower(),
        os.environ.get("TMP", "").lower(),
    ]
    for td in temp_dirs:
        if td and (resolved == td or resolved.startswith(td.rstrip("\\/") + "\\") or resolved.startswith(td.rstrip("\\/") + "/")):
            return True
    if "\\temp\\" in resolved or "/temp/" in resolved:
        return True
    if ".zip." in resolved or "_orvex-v" in resolved or "orvex_zip_" in resolved:
        return True
    return False


def is_source_repo_path(path: Path | str) -> bool:
    """Return True if path is a developer source repository checkout."""
    resolved = str(Path(path).resolve()).lower()
    dev_markers = [
        "documents\\github\\orvex",
        "documents\\experiments\\mcp",
        "documents/github/orvex",
        "documents/experiments/mcp",
    ]
    for marker in dev_markers:
        if marker in resolved:
            return True
    try:
        p = Path(path).resolve()
        while p != p.parent:
            if (p / ".git").is_dir():
                return True
            p = p.parent
    except Exception:
        pass
    return False


def validate_runtime_paths(
    install_dir: Path,
    python_exe: Path,
    allow_dev: bool = False,
    allow_temp: bool = False,
) -> None:
    """Strictly assert that ORVEX runtime is not in a temporary or dev directory."""
    if not allow_temp:
        if is_temporary_path(install_dir):
            raise ValueError(
                f"REFUSING REGISTRATION: install_dir '{install_dir}' is inside a temporary directory. "
                "Production ORVEX must be installed in a permanent location (e.g. %LOCALAPPDATA%\\ORVEX)."
            )
        if is_temporary_path(python_exe):
            raise ValueError(
                f"REFUSING REGISTRATION: python_exe '{python_exe}' is inside a temporary directory. "
                "Production ORVEX must use a permanent runtime (e.g. %LOCALAPPDATA%\\ORVEX\\runtime\\Scripts\\python.exe)."
            )
    if not allow_dev:
        if is_source_repo_path(install_dir):
            raise ValueError(
                f"REFUSING REGISTRATION: install_dir '{install_dir}' is a developer source repository. "
                "Production ORVEX must be installed in a permanent directory (e.g. %LOCALAPPDATA%\\ORVEX), "
                "or run with --dev for development mode."
            )


class ClientAdapter(ABC):
    """Abstract base class for AI client MCP adapters."""

    name: str = ""
    display_name: str = ""

    @abstractmethod
    def detect(self) -> bool:
        """Return True if this client is installed or present on the system."""
        ...

    @abstractmethod
    def config_location(self) -> Path | None:
        """Return path to the client's global/user configuration file, or None."""
        ...

    @abstractmethod
    def is_registered(self, install_dir: Path, allow_temp: bool = False) -> bool:
        """Return True if ORVEX is registered in this client."""
        ...

    @abstractmethod
    def register(
        self,
        install_dir: Path,
        python_exe: Path,
        allow_dev: bool = False,
        allow_temp: bool = False,
    ) -> tuple[bool, str]:
        """Register ORVEX into this client idempotently while preserving all existing config."""
        ...

    @abstractmethod
    def remove(self) -> tuple[bool, str]:
        """Remove ORVEX registration from this client, leaving other servers intact."""
        ...

    @abstractmethod
    def validate(self, allow_temp: bool = False) -> tuple[bool, str]:
        """Verify that the client configuration is syntactically and semantically valid."""
        ...
