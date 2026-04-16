@echo off
title JellyfishBot
echo =========================================
echo   JellyfishBot Local Launcher (Windows)
echo =========================================
echo.

REM Try to find Python
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python launcher.py %*
) else (
    where python3 >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        python3 launcher.py %*
    ) else (
        echo [ERROR] Python not found. Please install Python 3.10+ and add to PATH.
        echo Download: https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

pause
