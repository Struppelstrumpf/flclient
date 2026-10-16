@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Building FlyConomyMiniClient.exe ...
python -m pip install -r requirements.txt pyinstaller pillow pystray -q
if errorlevel 1 exit /b 1
if not exist logo.ico (
  echo logo.ico missing
  exit /b 1
)
if not exist tray_icon.png (
  echo Creating tray_icon.png ...
  python -c "from PIL import Image; from pathlib import Path; p=Path(r'../frontend/public/brand/flyconomy-icon.png'); im=Image.open(p).convert('RGBA').resize((256,256)); im.save('tray_icon.png')"
)
python -m PyInstaller --noconfirm --clean flyconomy_miniclient.spec
if errorlevel 1 exit /b 1
if not exist "dist\FlyConomyMiniClient.exe" (
  echo Build failed: exe missing
  exit /b 1
)
echo OK: dist\FlyConomyMiniClient.exe

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC (
  echo Building MiniClient installer ...
  "%ISCC%" FlyConomyMiniClient_Setup.iss
) else (
  echo Inno Setup not found – skipped installer
)
