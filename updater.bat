@echo off
title Updating AI Selfbot...

:: Always anchor to the script's own directory
cd /d "%~dp0"

set SOURCE=%~1
if "%SOURCE%"=="" set SOURCE=main

echo Waiting for bot to shut down...
timeout /t 5 /nobreak > nul

:: Kill any lingering Python processes holding bot-env files open
echo Killing any lingering Python processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1
timeout /t 2 /nobreak > nul

:: Clean up the update flag so the bot doesn't re-trigger on restart
if exist "config\update.flag" del /f /q "config\update.flag"

echo Pulling latest changes from GitHub...
git stash --include-untracked 2>nul

if /i "%SOURCE%"=="release" (
    echo Fetching latest release tag...
    git fetch --tags origin
    set LATEST_TAG=
    for /f "delims=" %%T in ('git tag --sort=-version:refname') do (
        if not defined LATEST_TAG set LATEST_TAG=%%T
    )
    if defined LATEST_TAG (
        echo Checking out release %LATEST_TAG%...
        git checkout %LATEST_TAG%
    ) else (
        echo No tags found, falling back to main...
        git pull --rebase origin main
    )
) else (
    git pull --rebase origin main
)

if %errorlevel% neq 0 (
    echo ERROR: git operation failed. Check your internet connection or git setup.
    pause
    exit /b 1
)
git stash pop 2>nul

echo Deleting bot-env...
:delete_retry
rmdir /s /q "%~dp0bot-env" 2>nul
if exist "%~dp0bot-env" (
    echo bot-env still locked, retrying in 3s...
    timeout /t 3 /nobreak > nul
    goto delete_retry
)

echo Reinstalling...
python -m venv "%~dp0bot-env"
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment. Is Python installed and in PATH?
    pause
    exit /b 1
)

call "%~dp0bot-env\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r "%~dp0requirements.txt"
pip install -U davey curl_cffi python-telegram-bot

echo Checking for ffmpeg...
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo ffmpeg not found, installing via winget...
    winget install --id Gyan.FFmpeg -e --silent --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo WARNING: ffmpeg install failed. Please install manually: https://ffmpeg.org/download.html
    ) else (
        echo ffmpeg installed successfully.
    )
) else (
    echo ffmpeg already installed, skipping.
)

echo Update complete. Relaunching...
start "AI Selfbot" cmd /k "cd /d "%~dp0" && call "%~dp0run.bat""
