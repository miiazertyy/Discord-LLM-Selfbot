@echo off
CLS
title Installing AI Selfbot...

:: cd to the script's own folder — everything uses relative paths from here
cd /d "%~dp0"

:: Check if git is installed
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo Git is not installed. Please install it from https://git-scm.com/download/win and rerun this script.
    pause
    exit /b 1
)

:: Initialize git repo if not already done
if not exist ".git" (
    echo Initializing git repo...
    git init
    git remote add origin https://github.com/miiazertyy/Discord-LLM-Selfbot.git
    git fetch
    git checkout main
)

:: Install venv if missing
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

:: Write a temporary launcher for the Telegram controller window
echo @echo off > _tg_launch.bat
echo cd /d "%~dp0" >> _tg_launch.bat
echo call "bot-env\Scripts\activate.bat" >> _tg_launch.bat
echo python telegram_controller.py >> _tg_launch.bat

:: Read Telegram credentials
set "TG_TOKEN="
set "TG_OWNER="
for /f "usebackq tokens=1,* delims==" %%A in ("config\.env") do (
    if /i "%%A"=="TELEGRAM_BOT_TOKEN" set "TG_TOKEN=%%B"
    if /i "%%A"=="TELEGRAM_OWNER_ID" set "TG_OWNER=%%B"
)

:: Strip spaces
if defined TG_TOKEN set "TG_TOKEN=%TG_TOKEN: =%"
if defined TG_OWNER set "TG_OWNER=%TG_OWNER: =%"

:: Launch Telegram controller only if both values are set and owner is not 0
set "TG_START=0"
if defined TG_TOKEN if defined TG_OWNER (
    if not "%TG_OWNER%"=="0" set "TG_START=1"
)

if "%TG_START%"=="1" (
    echo Starting Telegram controller...
    start "Telegram Controller" cmd /k "_tg_launch.bat"
) else (
    echo Telegram controller not started (TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set).
)

echo Starting bot...
title AI Selfbot
python main.py
