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

echo Update complete. Relaunching...
start run.bat
exit
