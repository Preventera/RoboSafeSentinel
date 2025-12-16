@echo off
REM ============================================
REM RoboSafe Sentinel - Script de lancement
REM ============================================

echo.
echo ========================================
echo    RoboSafe Sentinel - Demarrage
echo ========================================
echo.

REM Activer le venv
call venv\Scripts\activate.bat

REM Mode simulation par défaut
set MODE=--simulate
set PORT=8080

REM Parser les arguments
:parse
if "%~1"=="" goto :run
if /i "%~1"=="--prod" set MODE=--config config/production.yaml
if /i "%~1"=="--port" set PORT=%~2 & shift
shift
goto :parse

:run
echo Mode: %MODE%
echo Port: %PORT%
echo.
echo Dashboard: http://localhost:%PORT%/static/dashboard.html
echo API:       http://localhost:%PORT%/docs
echo.
echo Ctrl+C pour arreter
echo.

python -m robosafe.integration %MODE% --port %PORT%

pause
