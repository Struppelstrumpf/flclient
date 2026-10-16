@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Building FlyConomyWebApp.exe ...
python -m pip install pyinstaller -q
if errorlevel 1 exit /b 1
if not exist logo.ico (
  echo logo.ico missing
  exit /b 1
)
python -m PyInstaller --noconfirm --clean flyconomy_webapp.spec
if errorlevel 1 exit /b 1
if not exist "dist\FlyConomyWebApp.exe" (
  echo Build failed: exe missing
  exit /b 1
)
echo OK: dist\FlyConomyWebApp.exe

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC (
  echo Building Browser inApp installer ...
  "%ISCC%" FlyConomyWebApp_Setup.iss
) else (
  echo Inno Setup not found – skipped installer
)
