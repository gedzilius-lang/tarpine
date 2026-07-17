@echo off
setlocal EnableExtensions
title Patas Curve Monitor

rem Switch the console to UTF-8 so the dashboard's block/emoji glyphs render
rem correctly on legacy conhost (Windows Terminal is already UTF-8). Harmless if
rem it fails. Also tell Python to use UTF-8 for its own I/O.
chcp 65001 >nul 2>nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Always run from this script's own folder, so double-clicking works from anywhere.
cd /d "%~dp0"

echo ============================================================
echo   PATAS CURVE MONITOR - launcher
echo ============================================================
echo.

rem --- 1. Find a Python interpreter (prefer the py launcher, fall back to python) ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] Python was not found on your PATH.
    echo     Install Python 3 from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH" during setup.
    goto :fail
)
echo [i] Using interpreter: %PY%

rem --- 2. Make sure the module files are actually here ---
for %%F in (main.py telemetry.py processor.py ui.py notifier.py) do (
    if not exist "%%F" (
        echo [X] Missing required file: %%F
        echo     Keep all .py files together in this folder.
        goto :fail
    )
)

rem --- 3. Ensure a .env exists; seed it from the example on first run ---
if not exist ".env" (
    if exist ".env.example" (
        echo [i] No .env found - creating one from .env.example
        copy /y ".env.example" ".env" >nul
        echo [!] Edit .env and set RPC_URL before the monitor can connect.
        echo     Opening it now...
        start "" notepad ".env"
        echo.
        echo     Save the file, then press any key to continue.
        pause >nul
    ) else (
        echo [X] No .env and no .env.example to seed from.
        goto :fail
    )
)

rem --- 4. Check dependencies; offer to install if anything is missing ---
%PY% -c "import solders, requests, colorama, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [!] Some dependencies are missing.
    set /p "INSTALL=    Install them now with pip? [Y/n]: "
    if /i not "%INSTALL%"=="n" (
        echo [i] Installing from requirements.txt ...
        %PY% -m pip install -r requirements.txt
        if errorlevel 1 (
            echo [X] pip install failed. Resolve the error above and retry.
            goto :fail
        )
    ) else (
        echo [X] Cannot launch without dependencies.
        goto :fail
    )
)

rem --- 5. Launch. main.py owns all threads (scanner, price, telegram, UI). ---
echo.
echo [i] Starting monitor. Press Ctrl+C in the window to stop.
echo ------------------------------------------------------------
%PY% main.py
set "RC=%errorlevel%"

echo ------------------------------------------------------------
if not "%RC%"=="0" (
    echo [!] Monitor exited with code %RC%.
) else (
    echo [i] Monitor stopped.
)
echo.
pause
endlocal
exit /b %RC%

:fail
echo.
pause
endlocal
exit /b 1
