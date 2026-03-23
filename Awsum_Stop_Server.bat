REM .\Awsum_Stop_Server.bat

@echo off
setlocal
cd /d "%~dp0"

REM 1) 포트 8000/8001 LISTENING 프로세스 종료
set FOUND=0
for %%P in (8000 8001) do (
    for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr /R /C:":%%P .*LISTENING"') do (
        echo Terminating PID %%a on port %%P ...
        taskkill /PID %%a /F >nul 2>&1
        set FOUND=1
    )
)

REM 2) LISTENING 없으면 남은 python.exe 전체 종료 시도
if "%FOUND%"=="0" (
    tasklist /FI "IMAGENAME eq python.exe" 2>nul | findstr /i "python.exe" >nul
    if not errorlevel 1 (
        echo No LISTENING port found. Killing all python.exe processes ...
        taskkill /F /IM python.exe >nul 2>&1
    ) else (
        echo Server is not running.
    )
)

echo Done.
