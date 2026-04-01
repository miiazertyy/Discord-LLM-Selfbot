@echo off
CLS
title Installing AI Selfbot...

:: cd to the script's own folder first — everything uses relative paths from here
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

:: Write a temporary launcher for the Telegram controller window.
:: This avoids the nested-quote hell of passing a multi-command string to cmd /k.
echo @echo off > _tg_launch.bat
echo cd /d "%~dp0" >> _tg_launch.bat
echo call "bot-env\Scripts\activate.bat" >> _tg_launch.bat
echo python telegram_controller.py >> _tg_launch.bat

:: Launch Telegram controller in a separate window if token AND owner ID are configured
set "TG_TOKEN="
set "TG_OWNER="
for /f "tokens=2 delims==" %%A in ('findstr /i "^TELEGRAM_BOT_TOKEN=" "config\.env" 2^>nul') do set "TG_TOKEN=%%A"
for /f "tokens=2 delims==" %%A in ('findstr /i "^TELEGRAM_OWNER_ID=" "config\.env" 2^>nul') do set "TG_OWNER=%%A"
set "TG_TOKEN=%TG_TOKEN: =%"
set "TG_OWNER=%TG_OWNER: =%"

if defined TG_TOKEN if defined TG_OWNER (
    if not "%TG_TOKEN%"=="" if not "%TG_OWNER%"=="" if not "%TG_OWNER%"=="0" (
        echo Starting Telegram controller...
        start "Telegram Controller" cmd /k "_tg_launch.bat"
    ) else (
        echo Telegram controller not started (TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set).
    )
) else (
    echo Telegram controller not started (TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_ID not set).
)

echo Starting bot...
title AI Selfbot
python main.py
