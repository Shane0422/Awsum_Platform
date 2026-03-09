@echo off
chcp 65001 >nul
REM ===============================
REM FastAPI (Uvicorn) run script (force kill by port 8000)
REM ===============================

cd /d D:\Awsum_Projects\Awsum_Platform

REM Kill any process using port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000') do (
    echo Port 8000 in use by PID %%a. Terminating...
    taskkill /PID %%a /F
    timeout /t 2 >nul
)

REM Log file location
set LOGFILE=D:\Awsum_Projects\Awsum_Platform\server.log

echo Starting new Uvicorn server...
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload

REM start "Uvicorn" python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
REM python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
pause