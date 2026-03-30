@echo off
title Updating AI Selfbot...
echo Waiting for bot to shut down...
timeout /t 3 /nobreak > nul

echo Pulling latest changes from GitHub...
git stash --include-untracked
git pull
git stash pop

echo Deleting bot-env...
rmdir /s /q bot-env

echo Reinstalling...
python -m venv bot-env
call .\bot-env\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt

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
call run.bat
