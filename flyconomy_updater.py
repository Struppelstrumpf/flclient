#!/usr/bin/env python3
"""FlyConomy Updater – keeps MiniClient and MSFS panel current.

The full client is no longer offered (owner decision Q19, 2026-10-14): it is
neither installed nor updated any more; an existing installation keeps
working and can still be uninstalled here.

1.0.7 (release-2, RL-C03): every download is checked against the SHA-256 that
versions.json lists in "hashes" before it is installed or unpacked. A mismatch is
retried with a fresh versions.json (a file uploaded a moment ago may not be in the
list yet); if it stays wrong nothing is installed and the next check (20 min) tries
again – a wrong hash never blocks updates for good.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winreg
import zipfile
from pathlib import Path
from typing import Any, Optional

import requests

try:
    import webview
except ImportError:
    webview = None

SITE_URL = "https://flyconomy.de"
VERSIONS_URL = f"{SITE_URL}/api/live/versions.json"
UPDATER_VERSION = "1.0.7"

try:
    from flyconomy_webapp import desktop_shortcut_path, ensure_desktop_shortcut
except ImportError:
    desktop_shortcut_path = None  # type: ignore
    ensure_desktop_shortcut = None  # type: ignore
PANEL_PKG = "flyconomy-ingame-panel"
CHECK_EVERY_SEC = 20 * 60
# RL-C03: a wrong SHA-256 is retried this often with a fresh versions.json, this far apart.
HASH_RETRIES = 2
HASH_RETRY_WAIT_SEC = 20.0
CHECK_WHEN_FLIGHT_SEC = 45
BRIDGE_STATUS = "http://127.0.0.1:17474"
MUTEX_NAME = "Local\\FlyConomyUpdaterSingleton"
SHOW_EVENT = "Local\\FlyConomyUpdaterShow"

MINI_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FlyConomyMiniClient"
FULL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FlyConomy"
UPDATER_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FlyConomyUpdater"
MINI_EXE = MINI_DIR / "FlyConomyMiniClient.exe"
FULL_EXE = FULL_DIR / "FlyConomyClient.exe"
MINI_UNINST = "{A1B2C3D4-E5F6-7890-ABCD-FLYCONOMYMINI}_is1"
FULL_UNINST = "{8F3C2A91-6E4B-4D2A-9C11-FLYCONOMYCLI2}_is1"
UPDATER_UNINST = "{B7E4D2A1-9C31-4F80-8A12-7C3E9F1A2B10}_is1"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
kernel32 = ctypes.windll.kernel32
ERROR_ALREADY_EXISTS = 183


def app_dir() -> Path:
    root = UPDATER_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    return app_dir() / "config.json"


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        return {"community": "", "autostart": True, "auto_setup_done": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"community": "", "autostart": True, "auto_setup_done": False}


def save_config(cfg: dict) -> None:
    config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version_tuple(ver: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", ver or "0")]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def version_less(current: Optional[str], latest: Optional[str]) -> bool:
    if not latest:
        return False
    if not current:
        return True
    return version_tuple(current) < version_tuple(latest)


def file_version(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, data):
            return None

        def _query(subblock: str) -> Optional[str]:
            u = ctypes.c_void_p()
            ln = ctypes.c_uint()
            if not ctypes.windll.version.VerQueryValueW(data, subblock, ctypes.byref(u), ctypes.byref(ln)):
                return None
            if not u.value or not ln.value:
                return None
            raw = ctypes.wstring_at(u.value)
            ver = raw.split("\0", 1)[0].strip()
            return ver or None

        # Prefer String FileVersion (matches tray/UI); numeric filevers can lag behind.
        for subblock in (
            r"\StringFileInfo\040904B0\FileVersion",
            r"\StringFileInfo\040004B0\FileVersion",
        ):
            ver = _query(subblock)
            if ver:
                return ver

        u = ctypes.c_void_p()
        ln = ctypes.c_uint()
        if not ctypes.windll.version.VerQueryValueW(data, "\\", ctypes.byref(u), ctypes.byref(ln)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", ctypes.c_uint32),
                ("dwStrucVersion", ctypes.c_uint32),
                ("dwFileVersionMS", ctypes.c_uint32),
                ("dwFileVersionLS", ctypes.c_uint32),
            ]

        info = VS_FIXEDFILEINFO.from_address(u.value)
        return f"{info.dwFileVersionMS >> 16}.{info.dwFileVersionMS & 0xFFFF}.{info.dwFileVersionLS >> 16}"
    except Exception:
        return None


def process_running(name: str) -> bool:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        return name.lower() in (r.stdout or "").lower()
    except Exception:
        return False


def kill_process(name: str) -> None:
    subprocess.run(
        ["taskkill", "/F", "/IM", name, "/T"],
        capture_output=True,
        timeout=20,
        creationflags=CREATE_NO_WINDOW,
    )


class DeferUpdate(Exception):
    """Open flight — do not kill the client; retry after it is logged."""


def client_has_open_flight() -> tuple[bool, str]:
    """Ask MiniClient/full client whether a flight is in progress.

    If the process is running but the bridge does not answer, wait — killing
    mid-flight aborts the economy flight.
    """
    running = process_running("FlyConomyMiniClient.exe") or process_running("FlyConomyClient.exe")
    if not running:
        return False, ""
    data: dict = {}
    try:
        r = requests.get(f"{BRIDGE_STATUS}/flight-busy", timeout=2)
        if r.status_code == 200:
            data = r.json() or {}
        else:
            raise RuntimeError("no-flight-busy")
    except Exception:
        try:
            r = requests.get(f"{BRIDGE_STATUS}/status", timeout=2)
            if r.status_code == 200:
                data = r.json() or {}
        except Exception:
            data = {}
    if not data:
        return True, "Client läuft, Status unklar – Update wartet (kein Kill während eines Flugs)."
    fid = data.get("active_flight_id")
    busy = bool(data.get("busy") or fid or data.get("monitoring") or data.get("pending_end"))
    if busy:
        label = str(fid or "").strip()
        extra = f" {label}" if label else ""
        return True, f"Aktiver Flug{extra} – Update erst nach dem Loggen."
    return False, ""


def start_exe(path: Path) -> None:
    if not path.is_file():
        return
    subprocess.Popen(
        [str(path)],
        cwd=str(path.parent),
        close_fds=True,
        creationflags=DETACHED,
    )


def reg_uninstall(app_id: str) -> Optional[str]:
    key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{app_id}"
    roots = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
    views = [0]
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        views.extend([winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY])
    for hive in roots:
        for view in views:
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as k:
                    val, _ = winreg.QueryValueEx(k, "UninstallString")
                    return str(val)
            except OSError:
                continue
    return None


def run_inno_uninstall(app_id: str) -> None:
    raw = reg_uninstall(app_id)
    if not raw:
        raise RuntimeError("Kein Uninstaller gefunden.")
    m = re.match(r'"([^"]+)"', raw) or re.match(r"(\S+)", raw)
    if not m:
        raise RuntimeError("Uninstaller ungültig.")
    exe = m.group(1)
    subprocess.run(
        [exe, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
        timeout=240,
        creationflags=CREATE_NO_WINDOW,
    )


def silent_install(setup: Path) -> None:
    subprocess.run(
        [
            str(setup),
            "/VERYSILENT",
            "/NORESTART",
            "/SUPPRESSMSGBOXES",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
        ],
        timeout=480,
        check=True,
        creationflags=CREATE_NO_WINDOW,
    )


def find_community_folders() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA") or ""
    appdata = os.environ.get("APPDATA") or ""
    cfgs: list[Path] = []
    if local:
        root = Path(local) / "Packages"
        cfgs.extend(root.glob("Microsoft.FlightSimulator*/LocalCache/UserCfg.opt"))
        cfgs.extend(root.glob("Microsoft.Limitless*/LocalCache/UserCfg.opt"))
    if appdata:
        cfgs.append(Path(appdata) / "Microsoft Flight Simulator" / "UserCfg.opt")
        cfgs.append(Path(appdata) / "Microsoft Flight Simulator 2024" / "UserCfg.opt")
    found: list[Path] = []
    seen: set[str] = set()
    for cfg in cfgs:
        if not cfg.is_file():
            continue
        try:
            txt = cfg.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r'InstalledPackagesPath\s+"([^"]+)"', txt)
        if not m:
            continue
        community = Path(m.group(1)) / "Community"
        key = str(community).lower()
        if key in seen:
            continue
        seen.add(key)
        if community.is_dir():
            found.append(community)
    return found


def read_panel_version(community: Path) -> Optional[str]:
    man = community / PANEL_PKG / "manifest.json"
    if not man.is_file():
        return None
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
        ver = str(data.get("package_version") or "").strip()
        return ver or None
    except Exception:
        return None


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS, *parts)
    return Path(__file__).resolve().parent.joinpath(*parts)


def startup_shortcut() -> Path:
    appdata = os.environ.get("APPDATA") or ""
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "FlyConomy Updater.lnk"


def updater_exe() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(__file__).resolve()


def set_autostart(enabled: bool) -> None:
    link = startup_shortcut()
    if not enabled:
        if link.exists():
            link.unlink()
        return
    exe = updater_exe()
    cmd = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
        f"$s.TargetPath = '{exe}'; $s.Arguments = '--tray'; "
        f"$s.WorkingDirectory = '{exe.parent}'; $s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        timeout=20,
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
    )


class Updater:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.lock = threading.Lock()
        self.busy = False
        self.busy_label = ""
        self.logs: list[str] = []
        self.latest = {
            "client": "",
            "panel": "",
            "fullClient": "",
            "updater": "",
            "clientUrl": "",
            "panelUrl": "",
            "fullClientUrl": "",
            "updaterUrl": "",
        }
        # RL-C03: file name -> SHA-256 from versions.json "hashes".
        self.hashes: dict[str, str] = {}
        # Q19: retired unless the server explicitly offers it again.
        self.full_retired = True
        self.window: Any = None
        self._stop = threading.Event()
        self._wait_flight = False
        self._wait_flight_msg = ""
        if not self.cfg.get("community"):
            folders = find_community_folders()
            if folders:
                self.cfg["community"] = str(folders[0])
                save_config(self.cfg)

    def log(self, msg: str) -> None:
        safe = (
            str(msg)
            .replace("\u2192", "->")
            .replace("\u2026", "...")
            .replace("\u2013", "-")
            .replace("\u2014", "-")
        )
        line = time.strftime("%H:%M:%S ") + safe
        self.logs.append(line)
        self.logs = self.logs[-120:]
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)

    def community(self) -> Path:
        raw = str(self.cfg.get("community") or "").strip()
        return Path(raw) if raw else Path()

    def snapshot(self) -> dict:
        community = self.community()
        mini_ver = file_version(MINI_EXE)
        full_ver = file_version(FULL_EXE)
        panel_ver = read_panel_version(community) if community.is_dir() else None
        updater_ver = file_version(updater_exe()) or UPDATER_VERSION
        return {
            "updater": UPDATER_VERSION,
            "busy": self.busy,
            "busyLabel": self.busy_label,
            "waitFlight": self._wait_flight,
            "waitFlightMsg": self._wait_flight_msg,
            "community": str(community) if community else "",
            "communityOk": community.is_dir(),
            "autostart": startup_shortcut().is_file() or bool(self.cfg.get("autostart")),
            "mini": {
                "installed": MINI_EXE.is_file(),
                "running": process_running("FlyConomyMiniClient.exe"),
                "version": mini_ver,
                "latest": self.latest.get("client") or "",
                "outdated": version_less(mini_ver, self.latest.get("client")),
            },
            "panel": {
                "installed": bool(panel_ver),
                "version": panel_ver,
                "latest": self.latest.get("panel") or "",
                "outdated": version_less(panel_ver, self.latest.get("panel")),
            },
            "full": {
                "installed": FULL_EXE.is_file(),
                "running": process_running("FlyConomyClient.exe"),
                "version": full_ver,
                "retired": self.full_retired,
                "latest": "" if self.full_retired else (self.latest.get("fullClient") or ""),
                "outdated": False
                if self.full_retired
                else version_less(full_ver, self.latest.get("fullClient")),
            },
            "self": {
                "version": updater_ver,
                "latest": self.latest.get("updater") or UPDATER_VERSION,
                "outdated": version_less(updater_ver, self.latest.get("updater")),
            },
            "logs": list(self.logs[-40:]),
        }

    def fetch_latest(self) -> None:
        r = requests.get(VERSIONS_URL, timeout=12)
        r.raise_for_status()
        data = r.json() or {}
        self.latest["client"] = str(data.get("client") or "").strip()
        self.latest["panel"] = str(data.get("panel") or data.get("latest") or "").strip()
        self.latest["fullClient"] = str(data.get("fullClient") or "").strip()
        # Q19: only an explicit "fullClientDeprecated": false offers it again.
        self.full_retired = data.get("fullClientDeprecated") is not False
        self.latest["updater"] = str(data.get("updater") or UPDATER_VERSION).strip()
        self.latest["clientUrl"] = str(data.get("clientUrl") or f"{SITE_URL}/downloads/FlyConomyMiniClient_Setup.exe")
        self.latest["panelUrl"] = str(data.get("panelUrl") or f"{SITE_URL}/downloads/FlyConomy-MSFS-Panel.zip")
        self.latest["fullClientUrl"] = str(
            data.get("fullClientUrl") or f"{SITE_URL}/downloads/FlyConomyClient_Setup.exe"
        )
        self.latest["updaterUrl"] = str(
            data.get("updaterUrl") or f"{SITE_URL}/downloads/FlyConomyUpdater_Setup.exe"
        )
        raw_hashes = data.get("hashes")
        self.hashes = {
            str(k): str(v).strip().lower()
            for k, v in (raw_hashes.items() if isinstance(raw_hashes, dict) else [])
            if isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{64}", v.strip())
        }

    def expected_hash(self, url: str) -> str:
        """SHA-256 versions.json lists for the file of this URL ('' = not listed)."""
        name = str(url or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        return self.hashes.get(name, "")

    def download_verified(self, url: str, dest: Path) -> None:
        """RL-C03: download and compare with versions.json; retry with fresh hashes; else stop.

        No hash listed (old server, file not scanned yet) = install as before (TLS only) and
        say so in the log. A mismatch never installs: after the retries this cycle ends with
        an error and the next one (20 min) starts again."""
        problem = ""
        for attempt in range(HASH_RETRIES + 1):
            if attempt:
                time.sleep(HASH_RETRY_WAIT_SEC)
                try:
                    self.fetch_latest()
                except Exception as e:
                    self.log(f"versions.json: {e}")
            self.download(url, dest)
            expected = self.expected_hash(url)
            if not expected:
                self.log(f"Hinweis: keine Prüfsumme für {dest.name} auf dem Server – Download nur per HTTPS geprüft.")
                return
            got = file_sha256(dest)
            if got == expected:
                self.log(f"Prüfsumme ok: {dest.name}")
                return
            problem = f"{dest.name}: SHA-256 {got[:12]}… statt {expected[:12]}…"
            self.log(f"Prüfsumme stimmt nicht ({problem}) – neuer Versuch.")
        try:
            dest.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"Download nicht installiert – die Prüfsumme stimmt nicht ({problem}). "
            "Der Updater versucht es beim nächsten Durchlauf erneut."
        )

    def download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1024 * 256):
                    if chunk:
                        f.write(chunk)
            tmp.replace(dest)

    def install_panel(self) -> None:
        community = self.community()
        if not community.is_dir():
            folders = find_community_folders()
            if folders:
                community = folders[0]
                self.cfg["community"] = str(community)
                save_config(self.cfg)
        if not community.is_dir():
            raise RuntimeError("Community-Ordner nicht gefunden. Bitte manuell wählen.")
        url = self.latest["panelUrl"]
        zip_path = Path(tempfile.gettempdir()) / "FlyConomy-MSFS-Panel.zip"
        self.log("Panel wird heruntergeladen…")
        self.download_verified(url, zip_path)
        extract = Path(tempfile.gettempdir()) / "flyconomy-panel-extract"
        if extract.exists():
            shutil.rmtree(extract, ignore_errors=True)
        extract.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)
        src = extract / PANEL_PKG
        if not src.is_dir():
            matches = list(extract.rglob("manifest.json"))
            if matches:
                src = matches[0].parent
        if not src.is_dir():
            raise RuntimeError("Panel-ZIP enthält keinen Addon-Ordner.")
        dest = community / PANEL_PKG
        if dest.exists():
            self.log("Altes Panel wird entfernt…")
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        self.log(f"Panel installiert: {dest}")

    def _guard_open_flight(self) -> None:
        busy, why = client_has_open_flight()
        if busy:
            self._wait_flight = True
            self._wait_flight_msg = why
            raise DeferUpdate(why)
        self._wait_flight = False
        self._wait_flight_msg = ""

    def install_mini(self) -> None:
        url = self.latest["clientUrl"]
        setup = Path(tempfile.gettempdir()) / "FlyConomyMiniClient_Setup.exe"
        was_running = process_running("FlyConomyMiniClient.exe")
        if was_running or MINI_EXE.is_file():
            self._guard_open_flight()
        self.log("MiniClient wird heruntergeladen…")
        self.download_verified(url, setup)
        if was_running or MINI_EXE.is_file():
            self._guard_open_flight()
            self.log("Alten MiniClient beenden…")
            kill_process("FlyConomyMiniClient.exe")
            time.sleep(1.2)
        self.log("MiniClient wird installiert…")
        silent_install(setup)
        time.sleep(1.0)
        start_exe(MINI_EXE)
        self.log("MiniClient gestartet.")

    def install_full(self) -> None:
        if self.full_retired:
            raise RuntimeError(
                "Der volle Client wird nicht mehr angeboten. Bitte den MiniClient nutzen – "
                "eine vorhandene Installation läuft weiter."
            )
        url = self.latest["fullClientUrl"]
        setup = Path(tempfile.gettempdir()) / "FlyConomyClient_Setup.exe"
        was_running = process_running("FlyConomyClient.exe")
        if was_running or FULL_EXE.is_file():
            self._guard_open_flight()
        self.log("Vollen Client herunterladen…")
        self.download_verified(url, setup)
        if was_running or FULL_EXE.is_file():
            self._guard_open_flight()
            self.log("Alten Client beenden…")
            kill_process("FlyConomyClient.exe")
            time.sleep(1.2)
        self.log("Vollen Client installieren…")
        silent_install(setup)
        time.sleep(1.0)
        if was_running:
            start_exe(FULL_EXE)
        self.log("Voller Client ist aktuell.")

    def update_self(self) -> None:
        url = str(self.latest.get("updaterUrl") or "").strip()
        if not url:
            raise RuntimeError("Keine Updater-Download-URL.")
        ver = str(self.latest.get("updater") or "new").strip()
        setup = Path(tempfile.gettempdir()) / f"FlyConomyUpdater_Setup_{ver}.exe"
        self.log(f"Updater {UPDATER_VERSION} -> {ver}: Download...")
        self.download_verified(url, setup)
        if not setup.is_file() or setup.stat().st_size < 10_000:
            raise RuntimeError("Updater-Download unvollständig.")
        exe_after = UPDATER_DIR / "FlyConomyUpdater.exe"
        bat = Path(tempfile.gettempdir()) / "flyconomy_self_update.cmd"
        bat.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    "setlocal",
                    "ping 127.0.0.1 -n 4 >nul",
                    f'"{setup}" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES /FORCECLOSEAPPLICATIONS',
                    "if errorlevel 1 exit /b 1",
                    "ping 127.0.0.1 -n 3 >nul",
                    f'if exist "{exe_after}" start "" "{exe_after}"',
                ]
            )
            + "\r\n",
            encoding="utf-8",
        )
        self.log("Eigene Installation läuft – der Updater startet danach neu.")
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat)],
            cwd=str(Path(tempfile.gettempdir())),
            close_fds=True,
            creationflags=DETACHED | CREATE_NO_WINDOW,
        )
        time.sleep(0.5)
        os._exit(0)

    def auto_cycle(self, include_full: bool, include_self: bool) -> None:
        self.fetch_latest()
        if include_self and version_less(UPDATER_VERSION, self.latest.get("updater")):
            self.busy_label = "Updater"
            self.log(f"Neue Updater-Version {self.latest.get('updater')} - Selbstupdate.")
            self.update_self()
            return
        mini_ver = file_version(MINI_EXE)
        need_mini = not MINI_EXE.is_file() or version_less(mini_ver, self.latest.get("client"))
        if need_mini:
            self.busy_label = "MiniClient"
            self.install_mini()
        elif not process_running("FlyConomyMiniClient.exe"):
            start_exe(MINI_EXE)
            self.log("MiniClient war nicht aktiv – gestartet.")
        community = self.community()
        panel_ver = read_panel_version(community) if community.is_dir() else None
        if version_less(panel_ver, self.latest.get("panel")):
            self.busy_label = "Panel"
            self.install_panel()
        if include_full and not self.full_retired and FULL_EXE.is_file():
            full_ver = file_version(FULL_EXE)
            latest_full = self.latest.get("fullClient")
            if version_less(full_ver, latest_full):
                self.busy_label = "Client"
                self.install_full()
        self.ensure_webapp_desktop()
        self._wait_flight = False
        self._wait_flight_msg = ""

    def ensure_webapp_desktop(self) -> None:
        if not ensure_desktop_shortcut or not desktop_shortcut_path:
            return
        dest = desktop_shortcut_path()
        if dest.is_file() and self.cfg.get("webapp_shortcut"):
            return
        try:
            path = ensure_desktop_shortcut()
            self.cfg["webapp_shortcut"] = True
            save_config(self.cfg)
            self.log(f"Browser inApp liegt auf dem Desktop ({path.name}).")
        except Exception as exc:
            self.log(f"Browser inApp konnte nicht angelegt werden: {exc}")

    def run_job(self, fn, label: str) -> dict:
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "error": "Es läuft bereits eine Aktion."}
        self.busy = True
        self.busy_label = label
        try:
            fn()
            return {"ok": True, "state": self.snapshot()}
        except DeferUpdate as e:
            self.log(str(e))
            return {"ok": True, "deferred": True, "error": str(e), "state": self.snapshot()}
        except Exception as e:
            self.log(f"Fehler: {e}")
            return {"ok": False, "error": str(e), "state": self.snapshot()}
        finally:
            self.busy = False
            self.busy_label = ""
            self.lock.release()

    def loop(self) -> None:
        time.sleep(2.5)
        while not self._stop.is_set():
            self.run_job(lambda: self.auto_cycle(include_full=True, include_self=True), "Aktualisieren")
            self.cfg["auto_setup_done"] = True
            save_config(self.cfg)
            wait = CHECK_WHEN_FLIGHT_SEC if self._wait_flight else CHECK_EVERY_SEC
            self._stop.wait(wait)


CORE = Updater()


class JsApi:
    def get_state(self) -> dict:
        return CORE.snapshot()

    def check_now(self) -> dict:
        CORE.log("Suche nach Updates…")
        return CORE.run_job(lambda: CORE.auto_cycle(include_full=True, include_self=True), "Suche")

    def install_full(self) -> dict:
        CORE.log("Vollen Client installieren…")
        return CORE.run_job(CORE.install_full, "Client")

    def start_mini(self) -> dict:
        if not MINI_EXE.is_file():
            return CORE.run_job(CORE.install_mini, "MiniClient")
        start_exe(MINI_EXE)
        CORE.log("MiniClient gestartet.")
        return {"ok": True, "state": CORE.snapshot()}

    def uninstall(self, kind: str) -> dict:
        kind = str(kind or "")

        def job() -> None:
            if kind == "mini":
                kill_process("FlyConomyMiniClient.exe")
                time.sleep(0.6)
                run_inno_uninstall(MINI_UNINST)
                CORE.log("MiniClient deinstalliert.")
            elif kind == "full":
                kill_process("FlyConomyClient.exe")
                time.sleep(0.6)
                run_inno_uninstall(FULL_UNINST)
                CORE.log("Voller Client deinstalliert.")
            elif kind == "panel":
                dest = CORE.community() / PANEL_PKG
                if dest.exists():
                    shutil.rmtree(dest)
                CORE.log("Panel entfernt.")
            else:
                raise RuntimeError("Unbekanntes Paket.")

        return CORE.run_job(job, "Deinstallieren")

    def pick_community(self) -> dict:
        if not CORE.window:
            return {"ok": False, "error": "Fenster nicht bereit."}
        kind = getattr(webview, "FOLDER_DIALOG", None)
        if kind is None:
            kind = getattr(getattr(webview, "FileDialog", object), "FOLDER", 2)
        result = CORE.window.create_file_dialog(kind)
        if not result:
            return {"ok": False, "error": "Abgebrochen."}
        folder = Path(result[0] if isinstance(result, (list, tuple)) else result)
        if folder.name.lower() != "community":
            maybe = folder / "Community"
            if maybe.is_dir():
                folder = maybe
        CORE.cfg["community"] = str(folder)
        save_config(CORE.cfg)
        CORE.log(f"Community-Ordner: {folder}")
        return {"ok": True, "state": CORE.snapshot()}

    def set_autostart(self, enabled: bool) -> dict:
        on = bool(enabled)
        set_autostart(on)
        CORE.cfg["autostart"] = on
        save_config(CORE.cfg)
        CORE.log("Autostart an." if on else "Autostart aus.")
        return {"ok": True, "state": CORE.snapshot()}

    def open_downloads(self) -> dict:
        os.startfile(SITE_URL + "/downloads")  # type: ignore[attr-defined]
        return {"ok": True}

    def hide(self) -> dict:
        if CORE.window:
            try:
                CORE.window.minimize()
            except Exception:
                pass
        return {"ok": True}


def already_running() -> bool:
    try:
        kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last = kernel32.GetLastError()
        if last == ERROR_ALREADY_EXISTS:
            ev = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)
            if ev:
                kernel32.SetEvent(ev)
                kernel32.CloseHandle(ev)
            return True
        kernel32.CreateEventW(None, False, False, SHOW_EVENT)
        return False
    except Exception:
        return False


def watch_show_event(window) -> None:
    ev = kernel32.OpenEventW(0x00100000, False, SHOW_EVENT)  # SYNCHRONIZE
    if not ev:
        ev = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
    INFINITE = 0xFFFFFFFF
    while True:
        kernel32.WaitForSingleObject(ev, INFINITE)
        try:
            window.restore()
            window.show()
        except Exception:
            pass


def run_tray(window) -> None:
    try:
        import pystray
        from PIL import Image
    except Exception:
        return
    icon_file = resource_path("tray_icon.png")
    try:
        img = Image.open(icon_file).convert("RGBA") if icon_file.is_file() else Image.new("RGBA", (64, 64), (26, 115, 232, 255))
    except Exception:
        img = Image.new("RGBA", (64, 64), (26, 115, 232, 255))

    def show(_icon=None, _item=None) -> None:
        try:
            window.restore()
            window.show()
        except Exception:
            pass

    def check(_icon=None, _item=None) -> None:
        threading.Thread(target=lambda: CORE.run_job(lambda: CORE.auto_cycle(True, True), "Suche"), daemon=True).start()

    def quit_app(icon=None, _item=None) -> None:
        CORE._stop.set()
        try:
            if icon:
                icon.stop()
        except Exception:
            pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Öffnen", show, default=True),
        pystray.MenuItem("Nach Updates suchen", check),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Beenden", quit_app),
    )
    icon = pystray.Icon("FlyConomyUpdater", img, "FlyConomy Updater", menu)
    icon.run()


def main() -> None:
    if already_running():
        return
    if webview is None:
        raise SystemExit("pywebview fehlt")
    tray = "--tray" in sys.argv
    html = resource_path("ui", "updater.html")
    if not html.is_file():
        raise SystemExit(f"UI fehlt: {html}")
    window = webview.create_window(
        f"FlyConomy Updater {UPDATER_VERSION}",
        url=html.as_uri(),
        js_api=JsApi(),
        width=820,
        height=760,
        min_size=(720, 640),
        background_color="#F3F6FB",
        text_select=True,
        hidden=tray,
    )
    CORE.window = window
    if CORE.cfg.get("autostart", True):
        try:
            set_autostart(True)
        except Exception:
            pass

    def on_started() -> None:
        threading.Thread(target=CORE.loop, daemon=True).start()
        threading.Thread(target=watch_show_event, args=(window,), daemon=True).start()
        threading.Thread(target=run_tray, args=(window,), daemon=True).start()

    webview.start(private_mode=True, debug=False, func=on_started)


if __name__ == "__main__":
    main()
