#!/usr/bin/env python3
"""FlyConomy Browser inApp – opens the site in the default browser app window."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path
from typing import Optional

APP_URL = "https://flyconomy.de"
APP_NAME = "FlyConomy"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FlyConomyWebApp"
LAUNCHER_EXE = INSTALL_DIR / "FlyConomyWebApp.exe"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CHROMIUM = (
    "chrome",
    "msedge",
    "msedgewebview2",
    "brave",
    "opera",
    "operagx",
    "vivaldi",
    "chromium",
    "iridium",
)


def _unquote_exe(command: str) -> str:
    raw = str(command or "").strip()
    if raw.startswith('"'):
        end = raw.find('"', 1)
        if end > 1:
            return raw[1:end]
    return raw.split(" ", 1)[0]


def _reg_sz(root, path: str, name: Optional[str] = None) -> str:
    try:
        with winreg.OpenKey(root, path) as key:
            val, _ = winreg.QueryValueEx(key, name)
            return str(val or "")
    except OSError:
        return ""


def default_browser_command() -> str:
    progid = _reg_sz(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        "ProgId",
    )
    if progid:
        cmd = _reg_sz(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command")
        if cmd:
            return cmd
    return _reg_sz(winreg.HKEY_CLASSES_ROOT, r"https\shell\open\command")


def default_browser_exe() -> str:
    exe = _unquote_exe(default_browser_command())
    if exe and Path(exe).is_file():
        return exe
    edge = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if not edge.is_file():
        edge = Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    return str(edge) if edge.is_file() else exe


def browser_kind(exe: str) -> str:
    name = Path(exe).stem.lower()
    if name in CHROMIUM or "chrome" in name or "edge" in name:
        return "chromium"
    if "firefox" in name:
        return "firefox"
    return "other"


def launch_args(exe: str, url: str = APP_URL) -> list[str]:
    kind = browser_kind(exe)
    if kind == "chromium":
        return [exe, f"--app={url}", "--new-window"]
    if kind == "firefox":
        return [exe, "-new-window", url]
    return [exe, url]


def launch_flyconomy(url: str = APP_URL) -> None:
    exe = default_browser_exe()
    if not exe:
        os.startfile(url)  # type: ignore[attr-defined]
        return
    args = launch_args(exe, url)
    subprocess.Popen(args, close_fds=True, creationflags=CREATE_NO_WINDOW)


def desktop_dir() -> Path:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    desk = home / "Desktop"
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
    if desk.is_dir():
        return desk
    return public if public.is_dir() else desk


def desktop_shortcut_path() -> Path:
    return desktop_dir() / f"{APP_NAME}.lnk"


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def write_shortcut(target: str, arguments: str = "", icon: str = "") -> Path:
    dest = desktop_shortcut_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = str(Path(target).parent)
    ico = icon or target
    cmd = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('"
        + _ps_escape(str(dest))
        + "'); "
        f"$s.TargetPath = '{_ps_escape(target)}'; "
        f"$s.Arguments = '{_ps_escape(arguments)}'; "
        f"$s.WorkingDirectory = '{_ps_escape(work)}'; "
        f"$s.WindowStyle = 1; "
        f"$s.Description = 'FlyConomy Browser inApp'; "
        f"$s.IconLocation = '{_ps_escape(ico)},0'; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        timeout=20,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return dest


def launcher_path() -> Path:
    if getattr(sys, "frozen", False):
        here = Path(sys.executable)
        if here.parent.resolve() == INSTALL_DIR.resolve() or here.name.lower().startswith("flyconomywebapp"):
            if here.is_file():
                return here
    if LAUNCHER_EXE.is_file():
        return LAUNCHER_EXE
    return Path()


def is_installed_copy() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    try:
        return Path(sys.executable).resolve().parent == INSTALL_DIR.resolve()
    except OSError:
        return False


def self_install() -> Path:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = LAUNCHER_EXE
    src = Path(sys.executable) if getattr(sys, "frozen", False) else None
    if src and src.is_file() and src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    if dest.is_file():
        write_shortcut(str(dest), "", str(dest))
        return dest
    return ensure_desktop_shortcut()


def ensure_desktop_shortcut() -> Path:
    launcher = launcher_path()
    if launcher.is_file():
        return write_shortcut(str(launcher), "", str(launcher))
    exe = default_browser_exe()
    if exe and browser_kind(exe) != "chromium":
        edge = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if not edge.is_file():
            edge = Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if edge.is_file():
            exe = str(edge)
    if not exe:
        raise RuntimeError("Kein Standardbrowser gefunden.")
    args = launch_args(exe, APP_URL)
    return write_shortcut(args[0], " ".join(args[1:]), exe)


def main() -> int:
    if any(a in ("--install-shortcut", "--shortcut") for a in sys.argv[1:]):
        try:
            dest = ensure_desktop_shortcut()
            print(dest)
            return 0
        except Exception as exc:
            print(exc, file=sys.stderr)
            return 1
    if getattr(sys, "frozen", False) and not is_installed_copy():
        try:
            dest = self_install()
            if dest.is_file() and dest.resolve() != Path(sys.executable).resolve():
                subprocess.Popen([str(dest)], close_fds=True, creationflags=CREATE_NO_WINDOW)
                return 0
        except Exception:
            pass
    launch_flyconomy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
