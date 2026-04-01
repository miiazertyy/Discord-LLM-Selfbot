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

:: Read Telegram credentials directly from config/.env
set TG_TOKEN=
set TG_OWNER=
for /f "tokens=1,* delims==" %%A in ('type config\.env 2^>nul ^| findstr /i "TELEGRAM_BOT_TOKEN"') do set TG_TOKEN=%%B
for /f "tokens=1,* delims==" %%A in ('type config\.env 2^>nul ^| findstr /i "TELEGRAM_OWNER_ID"') do set TG_OWNER=%%B

:: Clean up any leftover temp files from old version
if exist "_tg_check.py" del /f /q "_tg_check.py"
if exist "_tg_launch.bat" del /f /q "_tg_launch.bat"

if defined TG_TOKEN if defined TG_OWNER if not "%TG_OWNER%"=="0" (
    echo Starting Telegram controller...
    start "Telegram Controller" cmd /k "cd /d "%~dp0" && call bot-env\Scripts\activate.bat && python telegram\telegram_controller.py"
) else (
    echo Telegram controller not started (TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set^).
)

echo Starting bot...
title AI Selfbot
python main.py
