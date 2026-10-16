@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo [FlyConomy] Python wurde nicht gefunden.
  echo Bitte Python 3.10+ installieren und "Add Python to PATH" aktivieren:
  echo https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo [FlyConomy] Abhaengigkeiten werden geprueft/installiert...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo [FlyConomy] pip install fehlgeschlagen.
  pause
  exit /b 1
)

echo [FlyConomy] Client startet...
echo API-URL im Client: https://flyconomy.de/api
echo Token von https://flyconomy.de/downloads kopieren und als Passwort einfuegen.
echo.
python flyconomy_client.py
echo.
pause
