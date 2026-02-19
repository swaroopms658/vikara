@echo off
echo Starting Voice Scheduling Agent...
echo Ensure you have set your API keys in .env file.

if not exist .env (
    echo .env file not found! Copying .env.example to .env...
    copy .env.example .env
    echo Please edit .env with your API keys and restart.
    pause
    exit /b
)

uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
