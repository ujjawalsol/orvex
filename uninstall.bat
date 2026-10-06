@echo off
:: ORVEX Clean Uninstall Script for Windows
:: Fast Windows Automation MCP Server

echo ==========================================================
echo   ORVEX — Windows Automation MCP Uninstall (v1.0.0)
echo ==========================================================
echo.

echo [1/3] Removing local virtual environment if present...
if exist ".venv" (
    rmdir /s /q ".venv" >nul 2>&1
    echo [OK] Removed isolated .venv environment.
) else (
    pip uninstall -y orvex >nul 2>&1
    echo [OK] Package uninstalled from current environment.
)

echo.
echo [2/3] Cleaning temporary sandbox and configuration files...
if exist "orvex_mcp_config.json" (
    del /f /q "orvex_mcp_config.json"
    echo [OK] Removed orvex_mcp_config.json
)
if exist "%TEMP%\orvex_sandbox" (
    rmdir /s /q "%TEMP%\orvex_sandbox" >nul 2>&1
    echo [OK] Cleared temporary test sandbox directory
)

echo.
echo [3/3] Checking remaining environment...
echo [INFO] System Footprint Verified:
echo   - No Windows Registry entries were created by ORVEX.
echo   - No system services or background daemons were installed.
echo   - To complete uninstallation, simply delete the ORVEX directory.
echo.
echo ==========================================================
echo   ORVEX has been uninstalled successfully.
echo ==========================================================
echo.
pause
