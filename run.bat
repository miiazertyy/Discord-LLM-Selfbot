@echo off
CLS
title Installing AI Selfbot...

cd /d "%~dp0"

where git >nul 2>&1
if %errorlevel% neq 0 (
    echo Git is not installed. Please install it from https://git-scm.com/download/win and rerun this script.
    pause
    exit /b 1
)

if not exist ".git" (
    echo Initializing git repo...
    git init
    git remote add origin https://github.com/miiazertyy/Discord-LLM-Selfbot.git
    git fetch
    git checkout main
)

if not exist "bot-env" (
    echo bot-env not found. Installing...
    python -m venv bot-env
    call "bot-env\Scripts\activate.bat"
    pip install --upgrade pip
    pip install -r requirements.txt
    cls
    echo Installed.
)

call "bot-env\Scripts\activate.bat"

:: Write the Telegram launcher helper
echo @echo off > _tg_launch.bat
echo cd /d "%~dp0" >> _tg_launch.bat
echo call "bot-env\Scripts\activate.bat" >> _tg_launch.bat
echo python telegram_controller.py >> _tg_launch.bat

:: Write a Python helper script to check credentials (avoids cmd quoting issues with token values)
echo import os, sys; f=open(os.path.join('config','.env')); d=dict(l.strip().split('=',1) for l in f if '=' in l); t=d.get('TELEGRAM_BOT_TOKEN','').strip(); o=d.get('TELEGRAM_OWNER_ID','0').strip(); sys.exit(0 if t and o and o!='0' else 1) > _tg_check.py

python _tg_check.py
if %errorlevel%==0 (
    echo Starting Telegram controller...
    start "Telegram Controller" cmd /k "_tg_launch.bat"
) else (
    echo Telegram controller not started (TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set).
)

echo Starting bot...
title AI Selfbot
python main.py
