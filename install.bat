@echo off
:: ORVEX Installation Script for Windows
:: Fast Windows Automation MCP Server
:: Usage: Double-click OR run in terminal.

echo ==========================================================
echo   ORVEX — High-Performance Windows Automation MCP
echo   Production Setup and Verification (v1.0.0)
echo ==========================================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Official Prerequisite:
    echo   Python 3.11 or later is required.
    echo.
    echo How to install:
    echo   1. Download Python 3.11+ from https://python.org
    echo      (IMPORTANT: Check "Add python.exe to PATH" during installation)
    echo   OR run via Windows Package Manager:
    echo      winget install Python.Python.3.12
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% detected.

:: Check Python version >= 3.11
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11 or later is required. Found %PYVER%.
    echo Please upgrade Python from https://python.org
    pause
    exit /b 1
)

:: Set up isolated environment if not in an active virtualenv
if not defined VIRTUAL_ENV (
    if not exist ".venv" (
        echo [INFO] Creating isolated virtual environment in .venv...
        python -m venv .venv
        if errorlevel 1 (
            echo [WARN] Could not create .venv; proceeding with current environment.
        )
    )
    if exist ".venv\Scripts\python.exe" (
        set "PY_EXE=%~dp0.venv\Scripts\python.exe"
        set "PIP_EXE=%~dp0.venv\Scripts\pip.exe"
    ) else (
        set "PY_EXE=python"
        set "PIP_EXE=pip"
    )
) else (
    set "PY_EXE=python"
    set "PIP_EXE=pip"
)

echo.
echo [1/3] Installing/verifying 5 direct production dependencies...
echo [INFO] Direct dependencies: mcp, uiautomation, comtypes, websockets, psutil.
echo [INFO] Using native CDP for browser automation (zero Playwright, zero browser downloads).
echo.

"%PIP_EXE%" install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation from requirements.txt failed.
    echo Trying direct install of core dependencies...
    "%PIP_EXE%" install "mcp>=2.0.0,<3.0.0" "uiautomation>=2.0.18" "comtypes>=1.2.0" "websockets>=12.0" "psutil>=5.9.0"
    if errorlevel 1 (
        echo [ERROR] Installation failed. Please check internet connection or permissions.
        pause
        exit /b 1
    )
)

echo.
echo [2/3] Installing ORVEX package metadata...
echo.
"%PIP_EXE%" install -e . --no-deps >nul 2>&1

echo.
echo [3/3] Generating MCP configuration and verifying engine...
echo.

"%PY_EXE%" -c "import mcp; import uiautomation; import websockets; import psutil; from engine.compiler import compile_intent; from engine.safety import SafetyPolicy; print('[OK] Engine and direct dependencies verified successfully!')"
if errorlevel 1 (
    echo [WARN] Verification had warnings or errors. Check your Python environment.
)

:: Generate ready-to-copy MCP configuration file with properly resolved absolute paths
"%PY_EXE%" -c "import json, os, sys; p = os.path.abspath('engine/server.py'); py = os.path.abspath(sys.executable); cfg = {'mcpServers': {'orvex': {'command': py, 'args': [p]}}}; open('orvex_mcp_config.json', 'w').write(json.dumps(cfg, indent=2)); print('[OK] Created orvex_mcp_config.json ready for MCP clients!')"

echo.
echo ==========================================================
echo   ORVEX v1.0.0 is ready for production use!
echo ==========================================================
echo.
echo [INFO] System Footprint:
echo   - Zero Windows Registry keys created or modified.
echo   - Zero background services or daemons installed.
echo   - Zero bundled browser binaries downloaded.
echo.
echo An 'orvex_mcp_config.json' file has been created in this folder
echo with your exact configuration:
echo.
type orvex_mcp_config.json
echo.
echo ==========================================================
echo Add the snippet above to your MCP client configuration
echo (e.g., Claude Desktop, Cursor, Goose, Zed).
echo.
pause
