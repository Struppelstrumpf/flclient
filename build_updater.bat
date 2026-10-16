@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Building FlyConomyUpdater.exe ...
python -m pip install -r requirements.txt pyinstaller pillow pystray -q
if errorlevel 1 exit /b 1
if not exist logo.ico (
  echo logo.ico missing
  exit /b 1
)
python -m PyInstaller --noconfirm --clean flyconomy_updater.spec
if errorlevel 1 exit /b 1
if not exist "dist\FlyConomyUpdater.exe" (
  echo Build failed: exe missing
  exit /b 1
)
echo OK: dist\FlyConomyUpdater.exe

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC (
  echo Building updater installer ...
  "%ISCC%" FlyConomyUpdater_Setup.iss
) else (
  echo Inno Setup not found – skipped installer
)
