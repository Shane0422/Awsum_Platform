REM .\Awsum_Run_Server_Dev.bat

@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
REM ===============================
REM FastAPI (Uvicorn) run script (dev mode with --reload)
REM ===============================

cd /d "%~dp0"
set PYTHON_EXE=C:\Users\Awsum\AppData\Local\Programs\Python\Python314\python.exe
set PORT=8001

goto :main

:show_active_ports
echo [INFO] Active listeners on target ports:
for %%P in (8000 8001) do (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /R /C:":%%P .*LISTENING"') do (
        set TARGET_IMAGE=
        for /f "tokens=1,* delims= " %%I in ('tasklist /FI "PID eq %%a" /FO CSV /NH 2^>nul') do (
            set TARGET_IMAGE=%%~I
        )
        if defined TARGET_IMAGE (
            echo   - Port %%P : PID %%a ^(!TARGET_IMAGE!^)
        ) else (
            echo   - Port %%P : PID %%a
        )
    )
)
exit /b 0

:main

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

set PORT_BUSY=0
for /L %%R in (1,1,10) do (
    set PORT_BUSY=0
    for %%P in (8000 8001) do (
        netstat -ano 2>nul | findstr /R /C:":%%P .*LISTENING" >nul && set PORT_BUSY=1
    )
    if "!PORT_BUSY!"=="0" goto :ports_ready
    echo [INFO] Waiting for ports 8000/8001 to be released... (%%R/10)
    call :show_active_ports
    timeout /t 1 >nul
)

echo [ERROR] Port 8000 or 8001 is still in use. Please run Awsum_Stop_Server.bat and try again.
call :show_active_ports
exit /b 1

:ports_ready

echo Starting Uvicorn DEV server on http://127.0.0.1:%PORT% with --reload ...
"%PYTHON_EXE%" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT% --reload

set EXIT_CODE=%errorlevel%
endlocal & exit /b %EXIT_CODE%