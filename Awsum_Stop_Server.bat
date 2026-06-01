REM .\Awsum_Stop_Server.bat

@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

goto :main

:show_listener
set TARGET_PORT=%~1
set TARGET_PID=%~2
echo [INFO] Port %TARGET_PORT% is owned by PID %TARGET_PID%
exit /b 0

:main

REM 1) 포트 8000/8001 LISTENING 프로세스 종료 (여러 번 재시도)
set FOUND=0
for /L %%R in (1,1,5) do (
    set FOUND=0
    for %%P in (8000 8001) do (
        for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /R /C:":%%P .*LISTENING"') do (
            call :show_listener %%P %%a
            echo Terminating PID %%a on port %%P ...
            taskkill /PID %%a /F >nul 2>&1
            if errorlevel 1 (
                powershell -NoProfile -Command "Stop-Process -Id %%a -Force -ErrorAction SilentlyContinue" >nul 2>&1
            )
            set FOUND=1
        )
    )
    if "!FOUND!"=="0" goto :after_port_kill
    timeout /t 1 >nul
)

:after_port_kill

REM 2) LISTENING 없으면 남은 python.exe 전체 종료 시도
if "!FOUND!"=="0" (
    tasklist /FI "IMAGENAME eq python.exe" 2>nul | findstr /i "python.exe" >nul
    if not errorlevel 1 (
        echo [INFO] No LISTENING port found. Killing remaining python.exe processes ...
        taskkill /F /IM python.exe >nul 2>&1
    ) else (
        echo [INFO] Server is not running.
    )
)

echo [OK] Stop routine complete.
