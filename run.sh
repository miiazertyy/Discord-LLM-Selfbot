#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

clear
echo "Starting AI Selfbot..."

# Install git if missing
if ! command -v git >/dev/null; then
    echo "git not found, installing..."
    if command -v apt-get >/dev/null; then
        apt-get install -y git
    elif command -v dnf >/dev/null; then
        dnf install -y git
    fi
fi

# Initialize git repo if not already done
if [ ! -d ".git" ]; then
    echo "Initializing git repo..."
    git init
    git remote add origin https://github.com/miiazertyy/Discord-LLM-Selfbot.git
    git fetch
    git checkout main
fi

if [ ! -d "bot-env" ]; then
    echo "'bot-env' folder not found. Installing..."

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

    source bot-env/bin/activate

    if [ -f "requirements.txt" ]; then
        echo "Installing from requirements.txt..."
        pip install --upgrade pip
        pip install -r requirements.txt
    else
        echo "No requirements.txt — installing default package list..."
        pip install --upgrade pip
        pip install curl_cffi fake_useragent httpx asyncio python-dotenv pyYAML requests groq openai colorama discord.py-self
    fi

    clear
    echo "Installed."
fi

source bot-env/bin/activate

echo "Starting bot..."
python3 main.py

read -p "Press Enter to exit..."
