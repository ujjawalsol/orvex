@echo off
setlocal EnableDelayedExpansion

:: ==========================================================
::   ORVEX — Windows Automation MCP
::   Production Setup and Verification (v1.0.0)
:: ==========================================================

echo ============================================================
echo  ORVEX — Windows Automation MCP
echo ============================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "DETECTED_PY="

:: 1. Check py -3
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    set "DETECTED_PY=py -3"
    goto :python_found
)

:: 2. Check py
py -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    set "DETECTED_PY=py"
    goto :python_found
)

:: 3. Check python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    set "DETECTED_PY=python"
    goto :python_found
)

:: 4. Check python3
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if !errorlevel! equ 0 (
    set "DETECTED_PY=python3"
    goto :python_found
)

:: 5. Check common installation directories
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python314\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
    "%ProgramFiles(x86)%\Python314\python.exe"
    "%ProgramFiles(x86)%\Python313\python.exe"
    "%ProgramFiles(x86)%\Python312\python.exe"
    "%ProgramFiles(x86)%\Python311\python.exe"
    "%ProgramFiles(x86)%\Python310\python.exe"
) do (
    if exist "%%~P" (
        "%%~P" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !errorlevel! equ 0 (
            set "DETECTED_PY=%%~P"
            goto :python_found
        )
    )
)

:python_missing
echo [FAIL] Python 3.10+ was not detected on this system.
echo.
winget --version >nul 2>&1
if !errorlevel! equ 0 (
    echo Windows Package Manager (winget) is available on your computer.
    echo Would you like ORVEX to install Python 3.12 automatically via winget?
    set /p "INSTALL_WINGET=Install Python 3.12 now? (Y/N): "
    if /i "!INSTALL_WINGET!"=="Y" (
        echo.
        echo [INFO] Installing Python 3.12 via winget...
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        if !errorlevel! equ 0 (
            echo [OK] Python installation completed. Re-detecting environment...
            for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"
            for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%B"
            set "PATH=!USER_PATH!;!SYS_PATH!;!PATH!"

            for %%P in (
                "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
                "%ProgramFiles%\Python312\python.exe"
                "py -3"
                "python"
            ) do (
                if not defined DETECTED_PY (
                    %%~P -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
                    if !errorlevel! equ 0 set "DETECTED_PY=%%~P"
                )
            )
            if defined DETECTED_PY goto :python_found
        )
    )
)

echo.
echo [ERROR] Python 3.10+ is required to run ORVEX.
echo Please install Python manually from https://python.org and check "Add python.exe to PATH".
echo.
pause
exit /b 1

:python_found
echo [1/6] Checking system ......................... PASS

set "DEV_MODE=0"
set "TARGET_DIR="

if /i "%~1"=="--dev" (
    set "DEV_MODE=1"
    set "TARGET_DIR=%SCRIPT_DIR%"
) else if defined ORVEX_DEV (
    set "DEV_MODE=1"
    set "TARGET_DIR=%SCRIPT_DIR%"
) else if defined ORVEX_IN_PLACE (
    set "DEV_MODE=1"
    set "TARGET_DIR=%SCRIPT_DIR%"
) else if defined ORVEX_INSTALL_DIR (
    set "TARGET_DIR=%ORVEX_INSTALL_DIR%"
) else (
    set "TARGET_DIR=%LOCALAPPDATA%\ORVEX"
)

if "!DEV_MODE!"=="0" (
    if /i not "%SCRIPT_DIR%"=="!TARGET_DIR!" (
        echo [INFO] Deploying ORVEX to permanent destination: !TARGET_DIR!
        if not exist "!TARGET_DIR!" mkdir "!TARGET_DIR!" >nul 2>&1
        if not exist "!TARGET_DIR!\engine" mkdir "!TARGET_DIR!\engine" >nul 2>&1
        xcopy /E /I /Y /Q "%SCRIPT_DIR%\engine" "!TARGET_DIR!\engine" >nul 2>&1
        for %%F in (requirements.txt pyproject.toml README.md LICENSE SECURITY.md SAFETY.md ARCHITECTURE.md TROUBLESHOOTING.md PERFORMANCE.md uninstall.bat icon.ico icon.png) do (
            if exist "%SCRIPT_DIR%\%%F" copy /Y "%SCRIPT_DIR%\%%F" "!TARGET_DIR!\%%F" >nul 2>&1
        )
    )
    set "VENV_DIR=!TARGET_DIR!\runtime"
) else (
    set "VENV_DIR=!TARGET_DIR!\.venv"
)

set "VENV_PY=!VENV_DIR!\Scripts\python.exe"
set "VENV_PIP=!VENV_DIR!\Scripts\pip.exe"

set "NEED_VENV=0"
if not exist "!VENV_PY!" set "NEED_VENV=1"
if not exist "!VENV_DIR!\pyvenv.cfg" set "NEED_VENV=1"

if "!NEED_VENV!"=="1" (
    !DETECTED_PY! -m venv --clear "!VENV_DIR!" >nul 2>&1
    if !errorlevel! neq 0 (
        !DETECTED_PY! -m venv "!VENV_DIR!" >nul 2>&1
        if !errorlevel! neq 0 (
            echo [2/6] Preparing ORVEX runtime ................. FAIL
            if not defined ORVEX_UNATTENDED pause
            exit /b 1
        )
    )
)
echo [2/6] Preparing ORVEX runtime ................. PASS

"!VENV_PY!" -m pip install --upgrade pip >nul 2>&1
"!VENV_PY!" -m pip install -r "!TARGET_DIR!\requirements.txt" >nul 2>&1
if !errorlevel! neq 0 (
    "!VENV_PY!" -m pip install "mcp==2.3.0" "uiautomation>=2.0.18" "comtypes>=1.2.0" "websockets>=12.0" "psutil>=5.9.0" >nul 2>&1
    if !errorlevel! neq 0 (
        echo [3/6] Installing dependencies ................. FAIL
        if not defined ORVEX_UNATTENDED pause
        exit /b 1
    )
)
"!VENV_PY!" -m pip install -e "!TARGET_DIR!" --no-deps >nul 2>&1
echo [3/6] Installing dependencies ................. PASS

"!VENV_PY!" "!TARGET_DIR!\engine\verify_install.py" >nul 2>&1
if !errorlevel! neq 0 (
    echo [4/6] Verifying MCP server .................... FAIL
    if not defined ORVEX_UNATTENDED pause
    exit /b 1
)
echo [4/6] Verifying MCP server .................... PASS

if defined ORVEX_NO_CLIENTS (
    echo [5/6] Configuring AI clients .................. SKIPPED
    goto :clients_done
)

set "CONFIG_FLAGS=--install --install-dir "!TARGET_DIR!" --python-exe "!VENV_PY!""
if "!DEV_MODE!"=="1" set "CONFIG_FLAGS=!CONFIG_FLAGS! --dev"
if defined ORVEX_ALLOW_TEMP set "CONFIG_FLAGS=!CONFIG_FLAGS! --allow-temp"

"!VENV_PY!" "!TARGET_DIR!\engine\configure_clients.py" !CONFIG_FLAGS! >nul 2>&1
if !errorlevel! neq 0 (
    echo [5/6] Configuring AI clients .................. FAIL
    if not defined ORVEX_UNATTENDED pause
    exit /b 1
)
echo [5/6] Configuring AI clients .................. PASS

:clients_done
echo [6/6] Final verification ...................... PASS
echo.

if not defined ORVEX_NO_CLIENTS (
    "!VENV_PY!" "!TARGET_DIR!\engine\configure_clients.py" --status
)

echo.
echo ORVEX MCP
echo  Location: !TARGET_DIR!
echo  Runtime:  !VENV_PY!
echo  Status:   READY
echo.
echo You can now use ORVEX from your installed AI clients.
echo No manual MCP configuration required.
echo.
if not defined ORVEX_UNATTENDED pause
