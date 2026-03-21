REM .\Awsum_Run_Server.bat

@echo off
setlocal
chcp 65001 >nul
REM ===============================
REM FastAPI (Uvicorn) run script (single-process, stable)
REM ===============================

cd /d "%~dp0"
set PYTHON_EXE=C:\Users\Awsum\AppData\Local\Programs\Python\Python314\python.exe
set PORT=8001

if not exist "%PYTHON_EXE%" (
    echo [WARN] Configured Python not found. Falling back to system python.
    set PYTHON_EXE=python
)

if "%AWSUM_PLATFORM_DATABASE_URL%"=="" (
    set "AWSUM_PLATFORM_DATABASE_URL=postgresql+psycopg://postgres:Awsum123!@127.0.0.1:5432/awsum_platform"
    echo [WARN] AWSUM_PLATFORM_DATABASE_URL was not set. Applied default local URL with password.
) else (
    echo [INFO] AWSUM_PLATFORM_DATABASE_URL is set. Masked DB URL will be logged at app startup.
)

REM Stop existing listeners on 8000/8001 before starting
call Awsum_Stop_Server.bat <nul >nul
timeout /t 1 >nul

echo Starting Uvicorn on http://127.0.0.1:%PORT% ...
"%PYTHON_EXE%" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%

set EXIT_CODE=%errorlevel%
endlocal & exit /b %EXIT_CODE%