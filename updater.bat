@echo off
title Updating AI Selfbot...

set SOURCE=%~1
if "%SOURCE%"=="" set SOURCE=main

echo Waiting for bot to shut down...
timeout /t 4 /nobreak > nul

echo Pulling latest changes from GitHub...
git stash --include-untracked 2>nul

if /i "%SOURCE%"=="release" (
    echo Fetching latest release tag...
    git fetch --tags origin
    for /f "delims=" %%T in ('git tag --sort=-version:refname') do (
        set LATEST_TAG=%%T
        goto :got_tag
    )
    :got_tag
    if defined LATEST_TAG (
        echo Checking out release %LATEST_TAG%...
        git checkout %LATEST_TAG%
    ) else (
        echo No tags found, falling back to main...
        git pull origin main
    )
) else (
    git pull origin main
)
if %errorlevel% neq 0 (
    echo ERROR: git operation failed. Check your internet connection or git setup.
    pause
    exit /b 1
)
git stash pop 2>nul

echo Deleting bot-env...
rmdir /s /q bot-env

echo Reinstalling...
python -m venv bot-env
call .\bot-env\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt
pip install -U davey curl_cffi

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
start "AI Selfbot" cmd /k "call run.bat"
