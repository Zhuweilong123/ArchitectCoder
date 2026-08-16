@chcp 65001 >nul 2>&1
@echo off
set "ROOT=%~dp0"

start "UML-Backend" cmd /k "cd /d "%ROOT%backend" && python -X utf8 -m app.main"
timeout /t 3 /nobreak >nul
start "UML-Frontend" cmd /k "cd /d "%ROOT%frontend" && npm run dev"

echo.
echo Backend:  http://localhost:8001
echo Frontend: http://localhost:3000
echo.
pause
