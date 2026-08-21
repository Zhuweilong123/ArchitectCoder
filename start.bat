@chcp 65001 >nul 2>&1
@echo off
set "ROOT=%~dp0"

rem Start backend
start "UML-Backend" /d "%ROOT%backend" cmd /k "python -X utf8 -m app.main"

rem Wait 3 seconds
timeout /t 3 /nobreak >nul

rem Start frontend with memory limit
start "UML-Frontend" /d "%ROOT%frontend" cmd /k "set NODE_OPTIONS=--max-old-space-size=4096 && npm run dev"

echo.
echo Backend:  http://localhost:8001
echo Frontend: http://localhost:3000
echo.
pause