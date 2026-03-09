@echo off
title Twitch Drops Bot
echo.
echo  ========================================
echo    Twitch Drops Bot
echo  ========================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo.
    echo  Download Python from: https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Install dependencies if needed
if not exist ".deps_installed" (
    echo  Installing dependencies (first time only)...
    echo.
    python -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo  [ERROR] Failed to install dependencies.
        echo  Try running: python -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo. > .deps_installed
    echo  Done!
    echo.
)

echo  Starting dashboard...
echo  (Your browser will open automatically)
echo.
echo  Leave this window open while using the bot.
echo  Press Ctrl+C to stop.
echo.

python -m src.main ui

pause
