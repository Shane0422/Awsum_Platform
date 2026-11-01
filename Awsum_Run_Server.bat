REM .\Awsum_Run_Server.bat

@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
REM ===============================
REM FastAPI (Uvicorn) run script (single-process, stable)
REM ===============================

cd /d "%~dp0"
set PYTHON_EXE=C:\Users\Awsum\AppData\Local\Programs\Python\Python314\python.exe
set PORT=8001
set TARGET_PORT=%PORT%

goto :main

:show_active_ports
echo [INFO] Active listeners on target ports:
for %%P in (8000 8001) do (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /R /C:":%%P .*LISTENING"') do (
        echo   - Port %%P : PID %%a
    )
)
exit /b 0

:is_port_busy
set CHECK_PORT=%~1
netstat -ano 2>nul | findstr /R /C:":%CHECK_PORT% .*LISTENING" >nul
if errorlevel 1 (
    exit /b 0
) else (
    exit /b 1
)

:pick_fallback_port
for %%C in (8001 8002 8003 8011 8012 8021) do (
    call :is_port_busy %%C
    if not errorlevel 1 (
        set TARGET_PORT=%%C
        exit /b 0
    )
)
exit /b 1

:main

if not exist "%PYTHON_EXE%" (
    echo [WARN] Configured Python not found. Falling back to system python.
    set PYTHON_EXE=python
)

if "%AWSUM_PLATFORM_DATABASE_URL%"=="" (
    set "AWSUM_PLATFORM_DATABASE_URL=postgresql+psycopg://postgres:Awsum123^!@127.0.0.1:5432/awsum_platform"
    echo [WARN] AWSUM_PLATFORM_DATABASE_URL was not set. Applied default local URL with password.
) else (
    echo [INFO] AWSUM_PLATFORM_DATABASE_URL is set. Masked DB URL will be logged at app startup.
)

REM Stop existing listeners on 8000/8001 before starting
call Awsum_Stop_Server.bat >nul 2>&1

set PORT_BUSY=0
for /L %%R in (1,1,10) do (
    set PORT_BUSY=0
    for %%P in (8000 8001) do (
        netstat -ano 2>nul | findstr /R /C:":%%P .*LISTENING" >nul && set PORT_BUSY=1
    )
    if "!PORT_BUSY!"=="0" goto :ports_ready
    echo [INFO] Waiting for ports 8000/8001 to be released... ^(%%R/10^)
    call :show_active_ports
    timeout /t 1 >nul
)

echo [ERROR] Port 8000 or 8001 is still in use. Please run Awsum_Stop_Server.bat and try again.
call :show_active_ports
call :pick_fallback_port
if errorlevel 1 exit /b 1
if not "%TARGET_PORT%"=="%PORT%" (
    echo [WARN] Falling back to available port %TARGET_PORT%.
    set PORT=%TARGET_PORT%
)

:ports_ready

echo Starting Uvicorn on http://127.0.0.1:%PORT% ...
"%PYTHON_EXE%" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT% --no-proxy-headers

set EXIT_CODE=%errorlevel%
endlocal & exit /b %EXIT_CODE%
