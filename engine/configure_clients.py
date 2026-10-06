"""CLI tool to auto-detect and register/unregister ORVEX across all supported AI clients."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from engine.clients.detector import (
    format_detection_summary,
    inspect_clients,
    register_detected_clients,
    unregister_all_clients,
)


def write_local_reference_config(install_dir: Path, python_exe: Path) -> Path:
    """Write/update local orvex_mcp_config.json as standard reference."""
    ref_path = install_dir / "orvex_mcp_config.json"
    config = {
        "mcpServers": {
            "orvex": {
                "command": str(python_exe.resolve()),
                "args": ["-m", "engine.server"],
                "cwd": str(install_dir.resolve()),
                "env": {
                    "PYTHONUNBUFFERED": "1",
                },
            }
        }
    }
    ref_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return ref_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ORVEX Multi-Client Automatic MCP Registration Manager"
    )
    parser.add_argument(
        "--install",
        action="store_true",
        default=True,
        help="Register ORVEX into all detected AI clients (default)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Unregister ORVEX from all AI clients",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display detection and registration status only",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="ORVEX repository / installation root directory",
    )
    parser.add_argument(
        "--python-exe",
        type=Path,
        default=Path(sys.executable),
        help="Python executable to invoke ORVEX with",
    )

    parser.add_argument(
        "--dev",
        action="store_true",
        default=bool(os.environ.get("ORVEX_DEV")),
        help="Permit registering a development/source repository as the ORVEX runtime",
    )
    parser.add_argument(
        "--allow-temp",
        action="store_true",
        default=bool(os.environ.get("ORVEX_ALLOW_TEMP")),
        help="Permit temporary directory for unit testing only",
    )

    args = parser.parse_args()
    install_dir: Path = args.install_dir.resolve()
    python_exe: Path = args.python_exe.resolve()

    if args.uninstall:
        results = unregister_all_clients()
        print("\nUNREGISTERING ORVEX FROM AI CLIENTS:")
        for adapter, success, msg in results:
            status = "OK" if success else "FAILED"
            print(f"  [{status}] {adapter.display_name}: {msg}")
        ref_path = install_dir / "orvex_mcp_config.json"
        if ref_path.exists():
            try:
                ref_path.unlink()
            except Exception:
                pass
        return 0

    if args.status:
        print("\n" + format_detection_summary(install_dir) + "\n")
        return 0

    # Default action: install / auto-register
    reg_results = register_detected_clients(
        install_dir,
        python_exe,
        allow_dev=args.dev,
        allow_temp=args.allow_temp,
    )
    write_local_reference_config(install_dir, python_exe)

    # Print summary
    print("\n" + format_detection_summary(install_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
