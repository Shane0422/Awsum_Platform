for /f "tokens=2 delims=," %%a in ('tasklist /v /fo csv ^| findstr /i "uvicorn"') do (
    echo Terminating uvicorn process with PID %%a ...
    taskkill /PID %%a /F
)

pause