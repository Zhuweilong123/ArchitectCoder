@chcp 65001 >nul 2>&1
@echo off
set "ROOT=%~dp0"
set "FRONTEND=%ROOT%frontend"

rem Install frontend dependencies on a fresh checkout.
if not exist "%FRONTEND%\node_modules\.bin\vite.cmd" (
    echo Frontend dependencies are missing. Installing with npm ci...
    where npm.cmd >nul 2>&1
    if errorlevel 1 (
        echo ERROR: npm.cmd was not found. Please install Node.js first.
        pause
        exit /b 1
    )
    pushd "%FRONTEND%"
    call npm.cmd ci
    if errorlevel 1 (
        popd
        echo ERROR: frontend dependency installation failed.
        pause
        exit /b 1
    )
    popd
)

rem Start backend
start "UML-Backend" /d "%ROOT%backend" cmd /k "python -X utf8 -m app.main"

rem Wait 3 seconds
timeout /t 3 /nobreak >nul

rem Start frontend with memory limit
start "UML-Frontend" /d "%FRONTEND%" cmd /k "set NODE_OPTIONS=--max-old-space-size=4096 && npm.cmd run dev"

echo.
echo Backend:  http://localhost:8001
echo Frontend: http://localhost:3000
echo.
pause
