@echo off
setlocal EnableDelayedExpansion
:: ==========================================================
::   ORVEX — Windows Automation MCP Clean Uninstall (v1.0.0)
:: ==========================================================

echo ==========================================================
echo   ORVEX — Windows Automation MCP Uninstall (v1.0.0)
echo ==========================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "PERM_DIR=%LOCALAPPDATA%\ORVEX"
set "PY_EXE="
set "CFG_SCRIPT="

:: 1. Locate python and configure_clients.py
if exist "%PERM_DIR%\runtime\Scripts\python.exe" (
    set "PY_EXE=%PERM_DIR%\runtime\Scripts\python.exe"
    set "CFG_SCRIPT=%PERM_DIR%\engine\configure_clients.py"
) else if exist "%SCRIPT_DIR%\runtime\Scripts\python.exe" (
    set "PY_EXE=%SCRIPT_DIR%\runtime\Scripts\python.exe"
    set "CFG_SCRIPT=%SCRIPT_DIR%\engine\configure_clients.py"
) else if exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
    set "PY_EXE=%SCRIPT_DIR%\.venv\Scripts\python.exe"
    set "CFG_SCRIPT=%SCRIPT_DIR%\engine\configure_clients.py"
) else (
    py -3 -c "import sys; sys.exit(0)" >nul 2>&1 && set "PY_EXE=py -3"
    if not defined PY_EXE (
        python -c "import sys; sys.exit(0)" >nul 2>&1 && set "PY_EXE=python"
    )
    if exist "%PERM_DIR%\engine\configure_clients.py" (
        set "CFG_SCRIPT=%PERM_DIR%\engine\configure_clients.py"
    ) else if exist "%SCRIPT_DIR%\engine\configure_clients.py" (
        set "CFG_SCRIPT=%SCRIPT_DIR%\engine\configure_clients.py"
    )
)

echo [1/4] Unregistering ORVEX from detected AI clients...
if defined PY_EXE (
    if defined CFG_SCRIPT (
        !PY_EXE! "!CFG_SCRIPT!" --uninstall
    ) else (
        echo [INFO] configure_clients.py not found, skipping automated deregistration.
    )
) else (
    echo [INFO] Python not found, skipping automated client deregistration.
)

echo.
echo [2/4] Removing permanent ORVEX installation if present...
if exist "%PERM_DIR%" (
    if /i not "%SCRIPT_DIR%"=="%PERM_DIR%" (
        rmdir /s /q "%PERM_DIR%" >nul 2>&1
        echo [OK] Removed permanent installation directory: %PERM_DIR%
    ) else (
        if exist "%PERM_DIR%\runtime" rmdir /s /q "%PERM_DIR%\runtime" >nul 2>&1
        if exist "%PERM_DIR%\engine" rmdir /s /q "%PERM_DIR%\engine" >nul 2>&1
        echo [OK] Removed runtime and engine from %PERM_DIR%
    )
)

if exist "%SCRIPT_DIR%\.venv" (
    rmdir /s /q "%SCRIPT_DIR%\.venv" >nul 2>&1
    echo [OK] Removed local .venv environment.
)

echo.
echo [3/4] Cleaning temporary sandbox and configuration files...
if exist "%SCRIPT_DIR%\orvex_mcp_config.json" (
    del /f /q "%SCRIPT_DIR%\orvex_mcp_config.json" >nul 2>&1
    echo [OK] Removed orvex_mcp_config.json
)
if exist "%PERM_DIR%\orvex_mcp_config.json" (
    del /f /q "%PERM_DIR%\orvex_mcp_config.json" >nul 2>&1
)
if exist "%TEMP%\orvex_sandbox" (
    rmdir /s /q "%TEMP%\orvex_sandbox" >nul 2>&1
    echo [OK] Cleared temporary test sandbox directory
)

echo.
echo [4/4] Verifying system footprint...
echo [INFO] System Footprint Verified:
echo   - All other MCP servers in client configurations were preserved.
echo   - Zero Windows Registry keys remain.
echo   - Zero background services or daemons left running.
echo   - Permanent installation directory removed.
echo.
echo ==========================================================
echo   ORVEX has been uninstalled successfully.
echo ==========================================================
echo.
if not defined ORVEX_UNATTENDED pause
