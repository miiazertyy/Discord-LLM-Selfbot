#!/bin/bash
set -euo pipefail

export GIT_EDITOR=true
export GIT_TERMINAL_PROMPT=0

cd "$(dirname "$0")"

echo "Waiting for bot to shut down..."
sleep 3

echo "Pulling latest changes from GitHub..."
git stash --include-untracked
git pull origin main
git stash pop

echo "Deleting bot-env..."
rm -rf bot-env

echo "Recreating virtual environment..."
if ! python3 -m venv bot-env 2>/dev/null; then
    echo "python3 venv creation failed. Attempting to install python3-venv..."
    if command -v dnf >/dev/null; then
        dnf install -y python3-venv
    elif command -v apt-get >/dev/null; then
        apt-get update
        apt-get install -y python3-venv
    else
        echo "No supported package manager found. Install python3-venv manually."
        exit 1
    fi
    python3 -m venv bot-env
fi

echo "Installing dependencies..."
source bot-env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Checking for ffmpeg..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found, installing..."
    if command -v apt-get >/dev/null; then
        apt-get update && apt-get install -y ffmpeg
    elif command -v dnf >/dev/null; then
        dnf install -y ffmpeg
    elif command -v brew >/dev/null; then
        brew install ffmpeg
    else
        echo "WARNING: No supported package manager found. Please install ffmpeg manually: https://ffmpeg.org/download.html"
    fi
    echo "ffmpeg installed successfully."
else
    echo "ffmpeg already installed, skipping."
fi

echo "Update complete. Relaunching..."
bash run.sh
