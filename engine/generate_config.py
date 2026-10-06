"""Generates orvex_mcp_config.json with absolute paths to the local virtualenv."""
from __future__ import annotations

import json
import os
import sys

def main() -> int:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py_exe = os.path.join(root_dir, ".venv", "Scripts", "python.exe")
    if not os.path.exists(py_exe):
        py_exe = sys.executable

    server_script = os.path.join(root_dir, "engine", "server.py")
    cfg = {
        "mcpServers": {
            "orvex": {
                "command": os.path.abspath(py_exe),
                "args": [
                    "-m",
                    "engine.server"
                ],
                "cwd": os.path.abspath(root_dir),
                "env": {
                    "PYTHONUNBUFFERED": "1"
                }
            }
        }
    }

    out_path = os.path.join(root_dir, "orvex_mcp_config.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    print(f"[OK] Generated {out_path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
