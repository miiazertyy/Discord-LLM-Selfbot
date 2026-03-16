#!/bin/bash
set -euo pipefail

# location of your project
cd /root/AI-Selfbot || { echo "Directory /root/AI-Selfbot not found"; exit 1; }

clear
echo "Installing AI Selfbot..."

if [ ! -d "bot-env" ]; then
    echo "'bot-env' folder not found. Installing..."

    # try create venv; if it fails, try to install venv support
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

    # activate and install dependencies
    # shellcheck source=/dev/null
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

# activate venv and run the bot
# shellcheck source=/dev/null
source bot-env/bin/activate

echo "Starting bot..."
python3 main.py

# keep terminal open
read -p "Press Enter to exit..."