@echo off
CLS
title Installing AI Selfbot...
set PATH=%PATH%;%~dp0

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

if not exist bot-env (
    echo 'bot-env' folder not found. Installing...
    python -m venv bot-env
    call .\bot-env\Scripts\activate.bat
    pip install --upgrade pip
    pip install -r requirements.txt
    cls
    echo Installed.
)

call .\bot-env\Scripts\activate.bat
echo Starting bot...
title AI Selfbot
python "main.py"
cmd /k
