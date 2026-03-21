REM .\Awsum_Stop_Server.bat

@echo off
setlocal
cd /d "%~dp0"

for %%P in (8000 8001) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
        echo Terminating PID %%a on port %%P ...
        taskkill /PID %%a /F >nul 2>&1
    )
)