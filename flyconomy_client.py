#!/usr/bin/env python3
"""
FlyConomy SimConnect Client (Own Economy)
- Liest MSFS via SimConnect
- Start / Tracking / End gegen eigene FlyConomy-API
- Lokaler Bridge-Server für das MSFS In-Game-Panel (http://127.0.0.1:17474)
"""

from __future__ import annotations

import configparser
import ctypes
import email.utils
import glob
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import queue
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import webbrowser

# Started as a script (full client / dev run), this file is "__main__". The helper
# modules do "from flyconomy_client import STATE" – without this line that import
# loads the file a second time with its own STATE, bridge and monitor (D4).
sys.modules.setdefault("flyconomy_client", sys.modules[__name__])

import requests

from cockpit_lamp_test import bind_logger, schedule_lamp_test
from payware_payload import apply_payware_payload, bind_logger as bind_payware_logger
from payware_payload import distribute_payload_stations, split_pax_cargo_lb

try:
    from SimConnect import AircraftEvents, AircraftRequests, SimConnect
except ImportError:
    print("Bitte installieren: pip install SimConnect requests pywebview")
    sys.exit(1)

from sim_ground import GROUND, SPAWN_CATALOG, heading_deg

from discord_presence import DiscordPresence
from client_gateway import GATEWAY

try:
    import webview
except ImportError:
    webview = None

SITE_URL = "https://flyconomy.de"
CLIENT_VERSION = "2.11.13"
PANEL_FALLBACK_LATEST = "1.4.31"
CLIENT_FALLBACK_LATEST = "2.11.13"
# Start gate when versions.json cannot be read: never block a client or panel that
# was allowed before (old backend fields "client"/"panel" at the time of 2.11.13).
CLIENT_FALLBACK_MIN = "2.11.12"
PANEL_FALLBACK_MIN = "1.4.30"
# Only used when versions.json is unreachable – then the push is skipped anyway.
UPDATER_PUSH_FALLBACK = "1.0.5"
PANEL_PKG_NAME = "flyconomy-ingame-panel"
# Panel 1.4.31+ asks twice before /cancel; older panels get a Windows question.
PANEL_TWO_TAP_CANCEL = "1.4.31"

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 17474
SAMPLE_SEC = 1.0  # sim poll for gates / park / teleport; track is much rarer
MIN_TRACK_POINTS = 3
MAX_TRACK_POINTS_SEND = 200  # ~5 min spacing → 1h ≈ 12 pts; long haul still compact
MAX_TRACK_POINTS_LOCAL = 400
TRACK_INTERVAL_MS = 5 * 60 * 1000  # cruise: one signed point every 5 minutes (+ start/end)
TRACK_INTERVAL_SHORT_MS = 90 * 1000  # denser samples for short hops (first 12 min)
TRACK_DENSE_FOR_MS = 12 * 60 * 1000
INTEGRITY_INTERVAL_SEC = 30.0
LIVE_POS_SEC = 3.0
INTEGRITY_GRACE_SEC = 45.0
LB_PER_GAL = 6.7
WEIGHT_UNEXPLAINED_LB = 220.0
FUEL_INCREASE_ABORT_GAL = 2.0
GSX_REFUEL_TIMEOUT_SEC = 120
LOADING_WINDOW_SEC = 1200  # gate: Fenix/GSX/EFB may change fuel+payload
GATE_LOADING_GS_KT = 25.0
PARKED_IDLE_GS_KT = 8.0  # engine-off / pause / alt-tab: SimConnect fuel often reads 0
FUEL_GLITCH_MIN_GAL = 8.0
FUEL_ZERO_SPIKE_GAL = 0.5
PARKED_FUEL_DROP_HOLD_GAL = 25.0
REFUEL_STATIONARY_GS_KT = 6.0
CARAVAN_CAP_GAL = 332.0
CARAVAN_FULL_SEC = 180.0
A320_CAP_GAL = 6275.0
A320_FULL_SEC = 450.0
MAX_FULL_FILL_SEC = 600.0
PARK_GS_KT = 3.0  # GA: still tight so taxi hold-shorts do not auto-end
PARK_GS_KT_AIRLINER = 8.0  # Fenix/GSX creep, tug, SimConnect jitter
PARK_AGL_FT = 80.0  # jet CG at the gate is often 15–40 ft; 25 blocked A220/Fenix
AIRBORNE_GS_KT = 40.0
GROUND_GLITCH_AGL_FT = 1000.0
MAX_SIM_RATE = 16.0  # default; /start sends max_sim_rate (D10)
GROUND_GLITCH_GS_KT = 250.0
PARK_BRAKE_LVARS = (
    "L:S_MIP_PARK_BRAKE",
    "L:S_PED_PARK_BRAKE",
    "L:S_MIP_PARKING_BRAKE",
    "L:A32NX_PARK_BRAKE_LEVER_POS",
    "L:A32NX_PARK_BRAKE",
    "L:INI_PARKING_BRAKE",
    "L:INI_PARK_BRAKE",
    "L:A220_PARK_BRAKE",
    "L:A220_PARKING_BRAKE",
    "L:XMLVAR_PARKING_BRAKE",
)
_CUSTOM_FUEL_MARKERS = (
    "fenix",
    "fnx32",
    "fnxa32",
    "fenixsim",
    "pmdg",
    "toliss",
    "to liss",
    "ifly",
    "i fly",
    "fslabs",
    "fs labs",
    "inibuilds",
    "ini builds",
    "synaptic",
    "a220",
    "bd-500",
    "cs300",
    "tristar",
    "l1011",
    "l-1011",
    "headwind",
    "qualitywings",
    "felis",
    "maddog",
    "leonardo",
)
# Writable tanks, but FUEL_TOTAL_QUANTITY jumps (EFB / cargo-empty / taxi).
# Do NOT add these to _CUSTOM_FUEL_MARKERS — that would skip SimConnect fill.
_UNRELIABLE_FUEL_MARKERS = (
    "skycourier",
    "sky courier",
    "c408",
    "cessna 408",
)
# Twin-engine turboprops: only LEFT/RIGHT MAIN — aux/tip/center often mirror capacity
# and a second fill pass stacks fuel (ATR 42/72, Dash 8, …).
_MAIN_PAIR_ONLY_MARKERS = (
    "atr",
    "at45",
    "at46",
    "at75",
    "at76",
    "dash 8",
    "dash-8",
    "dash8",
    "q400",
    "dhc-8",
    "dhc8",
    "saab 340",
    "saab340",
    "beech 1900",
    "b1900",
    "do 228",
    "dornier 228",
)


def _main_pair_only_fill(blob: str) -> bool:
    b = (blob or "").lower()
    return any(m in b for m in _MAIN_PAIR_ONLY_MARKERS)
GSX_REFUEL_MENU_ITEM = 3  # GSX main menu: Request Refueling
GSX_MENU_INDEX = {
    "deboard": 0,
    "catering": 1,
    "refuel": 2,
    "board": 3,
    "pushback": 4,
    "jetway": 5,
    "stairs": 6,
    "more": 7,
    "reset": 8,
    "reposition": 9,
}
GSX_LVAR_JETWAY = "L:FSDT_GSX_OPERATEJETWAYS"
GSX_LVAR_STAIRS = "L:FSDT_GSX_OPERATESTAIRS"
GSX_LVAR_COUATL = "L:FSDT_GSX_COUATL_STARTED"
GSX_LVAR_MENU_OPEN = "L:FSDT_GSX_MENU_OPEN"
GSX_LVAR_MENU_CHOICE = "L:FSDT_GSX_MENU_CHOICE"
GSX_LVAR_REFUEL = "L:FSDT_GSX_REFUELING_STATE"
GSX_LVAR_HOSE = "L:FSDT_GSX_FUELHOSE_CONNECTED"
GSX_LVAR_SET_CUST_FUEL = "L:FSDT_GSX_SET_DETECT_CUST_REFUEL"
GSX_LVAR_SET_PROG_FUEL = "L:FSDT_GSX_SET_PROGRESS_REFUEL"
AUTO_END_FAIL_COOLDOWN_SEC = 90
# Parked at an airport too small for the aircraft: wait before asking again.
HEAVY_AIRPORT_COOLDOWN_SEC = 600
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".flyconomy_client.ini")
PENDING_END_PATH = os.path.join(os.path.expanduser("~"), ".flyconomy_pending_end.json")
PENDING_START_PATH = os.path.join(os.path.expanduser("~"), ".flyconomy_pending_start.json")
PENDING_START_MAX_SEC = 600
FLIGHT_SESSION_PATH = os.path.join(os.path.expanduser("~"), ".flyconomy_flight_session.json")
RETRY_INTERVAL_SEC = 60
# Production API is fixed — users never need to type this.
DEFAULT_API_URL = "https://flyconomy.de/api"
# A queued start is dropped when the aircraft moved in the meantime (D7).
PENDING_START_MAX_GS_KT = 5.0
PENDING_START_MAX_MOVE_NM = 1.0
# Bridge: browsers may only talk to us from the panel (coui://) or our own pages.
BRIDGE_SELF_ORIGINS = ("http://127.0.0.1:17474", "http://localhost:17474")


# ---------------------------------------------------------------------------
# Crash-safe files (D3): temp file + flush + fsync, previous version kept as .bak,
# atomic os.replace (retried while Windows/AV briefly locks the target).
# ---------------------------------------------------------------------------


def _replace_with_retry(src: str, dst: str, retries: int = 10) -> None:
    for i in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == retries - 1:
                raise
            time.sleep(0.05 * (i + 1))


def atomic_write_text(path: str, text: str, *, keep_bak: bool = True) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if keep_bak and os.path.exists(path):
            try:
                shutil.copyfile(path, path + ".bak")
            except OSError:
                pass  # the main file is still intact; a missing .bak is no harm
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_write_json(path: str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data))


def read_json_file(path: str) -> Any:
    """Main file first, the .bak copy if the main file is missing or broken."""
    for candidate in (path, path + ".bak"):
        try:
            if not os.path.exists(candidate):
                continue
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None


def remove_file_and_bak(path: str) -> None:
    # .bak first: a crash in between leaves the old main file, never a resurrected .bak
    for candidate in (path + ".bak", path):
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError:
            pass


def file_or_bak_exists(path: str) -> bool:
    return os.path.exists(path) or os.path.exists(path + ".bak")


# ---------------------------------------------------------------------------
# Server clock (D5): offset from the HTTP Date header, used when signing points.
# ---------------------------------------------------------------------------


class ServerClock:
    DEAD_BAND_MS = 2000  # below this the PC clock is fine – keep 0 (no jitter)
    HINT_MS = 60_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: List[float] = []
        self.offset_ms = 0
        self.raw_ms = 0.0
        self.known = False

    def observe(self, date_header: Any, t_send: float, t_recv: float) -> None:
        if not date_header:
            return
        rtt = t_recv - t_send
        if rtt < 0 or rtt > 10:
            return
        try:
            dt = email.utils.parsedate_to_datetime(str(date_header))
        except (TypeError, ValueError, IndexError):
            return
        if dt is None:
            return
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # The header is cut to whole seconds: the true time is +0…999 ms.
        server_ms = dt.timestamp() * 1000.0 + 500.0
        local_ms = (t_send + t_recv) * 500.0
        with self._lock:
            self._samples.append(server_ms - local_ms)
            del self._samples[:-7]
            med = statistics.median(self._samples)
            self.raw_ms = med
            self.known = True
            self.offset_ms = int(round(med)) if abs(med) >= self.DEAD_BAND_MS else 0

    def now_ms(self) -> int:
        return int(time.time() * 1000) + int(self.offset_ms)

    def reset(self) -> None:
        with self._lock:
            self._samples = []
            self.offset_ms = 0
            self.raw_ms = 0.0
            self.known = False


SERVER_CLOCK = ServerClock()


def now_ms() -> int:
    """Milliseconds on the server's clock (local clock + measured offset)."""
    return SERVER_CLOCK.now_ms()


def js_to_fixed(value: Any, digits: int) -> str:
    """Exactly JavaScript's Number.prototype.toFixed (the backend signs with it).

    Python's format() rounds an exact binary tie (26.125 → "26.12") half-to-even,
    JavaScript rounds it up ("26.13"), and Python keeps "-0.0" where JS writes
    "0.0". Both differences broke track signatures for such values.
    """
    x = float(value)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    neg = x < 0
    q = Decimal(abs(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    text = format(q, "f")
    return "-" + text if neg else text


def _sim_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", errors="ignore")
    return str(val).replace("\x00", "").strip()


class RetryableError(RuntimeError):
    """Server is temporarily unavailable (maintenance window, network glitch)."""

    def __init__(self, message: str, retry_after: int = RETRY_INTERVAL_SEC) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AirportRejectedError(RuntimeError):
    """Airliner parked at an airport that cannot handle it – the flight stays open."""

    def __init__(self, message: str, airport: str = "") -> None:
        super().__init__(message)
        self.airport = airport


class KeepFlyingError(RuntimeError):
    """Server refused this /end attempt; the in_progress flight and local track stay."""

    def __init__(self, message: str, code: str = "KEEP_FLYING") -> None:
        super().__init__(message)
        self.code = code


class EndBusyError(RetryableError):
    """The server is still processing an earlier /end of this flight (END_BUSY)."""


class SessionInvalidError(KeepFlyingError):
    """SESSION_INVALID / SESSION_MISSING: this end payload can never succeed."""


class FlightClosedError(RuntimeError):
    """The server closed the flight for good (rejected / cancelled)."""

    def __init__(self, message: str, code: str = "FLIGHT_CLOSED") -> None:
        super().__init__(message)
        self.code = code


class NoActiveFlightError(RuntimeError):
    """Old server: 'Kein aktiver Flug.' – the flight is gone (maybe already logged)."""


class ServerRejectedError(RuntimeError):
    """/flights/tracking: the server rejected the running flight (it is over)."""


class VersionGateError(RuntimeError):
    """Client or panel below clientMin/panelMin – starting is not allowed."""

    def __init__(self, message: str, info: Optional[dict] = None) -> None:
        super().__init__(message)
        self.info = info or {}


def scrub_config_leftovers() -> None:
    """After a login or logout: no copy of an old token may stay on disk (review S8).

    atomic_write_text keeps the previous ini as .bak – with the token of the account
    that just logged out. A hard crash can also leave a .tmp-<pid>-<tid> file."""
    try:
        if os.path.exists(CONFIG_PATH + ".bak"):
            os.remove(CONFIG_PATH + ".bak")
    except OSError:
        pass
    own = f".tmp-{os.getpid()}-"
    for leftover in glob.glob(glob.escape(CONFIG_PATH) + ".tmp-*"):
        if own in leftover:
            continue  # another thread of this process may be writing right now
        try:
            os.remove(leftover)
        except OSError:
            pass


class ConfigManager:
    def __init__(self) -> None:
        self.config = configparser.ConfigParser()
        for candidate in (CONFIG_PATH, CONFIG_PATH + ".bak"):
            if not os.path.exists(candidate):
                continue
            try:
                parsed = configparser.ConfigParser()
                parsed.read(candidate, encoding="utf-8")
                if candidate.endswith(".bak") and parsed.has_section("Credentials"):
                    # Never sign in from the backup copy: it may hold the token of a
                    # session that was logged out since (review S8). Re-login instead.
                    for secret in ("access_token", "password"):
                        parsed.remove_option("Credentials", secret)
                self.config = parsed
                break
            except (configparser.Error, OSError, UnicodeDecodeError):
                continue  # half-written ini (old client, power cut): try the .bak
        if "Credentials" not in self.config:
            self.config["Credentials"] = {}
        if "Settings" not in self.config:
            self.config["Settings"] = {}
        # Always pin production API (ignore old localhost entries).
        self.config["Settings"]["api_url"] = DEFAULT_API_URL

    def save(self, *, secret_changed: bool = False) -> None:
        self.config["Settings"]["api_url"] = DEFAULT_API_URL
        buf = io.StringIO()
        self.config.write(buf)
        atomic_write_text(CONFIG_PATH, buf.getvalue(), keep_bak=not secret_changed)
        if secret_changed:
            scrub_config_leftovers()

    def feature_override(self, name: str) -> Optional[bool]:
        """[Settings] feature_<name> = true/false in the ini (beta testers)."""
        raw = self.config.get("Settings", f"feature_{name}", fallback="").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return None

    @property
    def username(self) -> str:
        return self.config.get("Credentials", "username", fallback="")

    @property
    def password(self) -> str:
        return self.config.get("Credentials", "password", fallback="")

    @property
    def access_token(self) -> str:
        # Prefer access_token; fall back to legacy password field if it looks like a JWT.
        token = self.config.get("Credentials", "access_token", fallback="").strip()
        if token:
            return token
        legacy = self.config.get("Credentials", "password", fallback="").strip()
        if legacy.count(".") >= 2 and len(legacy) > 40:
            return legacy
        return ""

    @property
    def api_url(self) -> str:
        return DEFAULT_API_URL

    def set_credentials(self, username: str, password: str) -> None:
        self.config["Credentials"]["username"] = username
        self.config["Credentials"]["password"] = password
        self.save(secret_changed=True)

    def set_access_token(self, token: str, username: str = "") -> None:
        token = token.strip()
        creds = self.config["Credentials"]
        changed = (
            creds.get("access_token", "") != token
            or creds.get("password", "") != ""
            or (bool(username) and creds.get("username", "") != username)
        )
        if not changed:
            return  # D3: only a real token change touches the ini
        creds["access_token"] = token
        # Clear legacy password so we don't keep stale secrets around.
        creds["password"] = ""
        if username:
            creds["username"] = username
        self.save(secret_changed=True)

    def set_api_url(self, url: str) -> None:
        # Kept for compatibility with older panel payloads; always ignored.
        self.config["Settings"]["api_url"] = DEFAULT_API_URL
        self.save()

    @property
    def share_live_position(self) -> Optional[bool]:
        if "share_live_position" not in self.config["Settings"]:
            return None
        val = self.config.get("Settings", "share_live_position", fallback="").strip().lower()
        if val in ("", "none", "null"):
            return None
        return val in ("1", "true", "yes", "on")

    def set_share_live_position(self, value: bool) -> None:
        self.config["Settings"]["share_live_position"] = "true" if value else "false"
        self.config["Settings"]["onboarding_done"] = "true"
        self.save()

    @property
    def onboarding_done(self) -> bool:
        return self.config.get("Settings", "onboarding_done", fallback="false").lower() in (
            "1",
            "true",
            "yes",
        )

    @property
    def locale(self) -> str:
        val = self.config.get("Settings", "locale", fallback="de").strip().lower()
        return "en" if val == "en" else "de"

    def set_locale(self, locale: str) -> None:
        self.config["Settings"]["locale"] = "en" if str(locale).strip().lower() == "en" else "de"
        self.save()

    @property
    def fuel_mode(self) -> str:
        val = self.config.get("Settings", "fuel_mode", fallback="auto").strip().lower()
        return val if val in ("auto", "direct", "gsx") else "auto"

    def set_fuel_mode(self, mode: str) -> None:
        m = str(mode or "auto").strip().lower()
        if m not in ("auto", "direct", "gsx"):
            m = "auto"
        self.config["Settings"]["fuel_mode"] = m
        self.save()

    @property
    def device_id(self) -> str:
        d = self.config.get("Settings", "device_id", fallback="").strip()
        if not d:
            d = str(uuid.uuid4())
            self.config["Settings"]["device_id"] = d
            self.save()
        return d

    @property
    def hotkey_start(self) -> str:
        return self.config.get("Settings", "hotkey_start", fallback="").strip()

    @property
    def hotkey_end(self) -> str:
        return self.config.get("Settings", "hotkey_end", fallback="").strip()

    @property
    def hotkey_volume(self) -> float:
        try:
            n = float(self.config.get("Settings", "hotkey_volume", fallback="35"))
        except (TypeError, ValueError):
            n = 35.0
        return max(0.0, min(1.0, n / 100.0 if n > 1.5 else n))

    def set_hotkey(self, slot: str, definition: str) -> None:
        val = str(definition or "").strip()
        self.config["Settings"]["hotkey_start"] = val
        self.config["Settings"]["hotkey_end"] = val
        self.save()

    def set_hotkey_volume(self, volume: float) -> None:
        pct = int(round(max(0.0, min(1.0, float(volume))) * 100))
        self.config["Settings"]["hotkey_volume"] = str(pct)
        self.save()

    @property
    def cockpit_lamp_test_enabled(self) -> bool:
        val = self.config.get("Settings", "cockpit_lamp_test", fallback="true").strip().lower()
        return val in ("1", "true", "yes", "on")

    @property
    def cockpit_lamp_test_seconds(self) -> float:
        try:
            n = float(self.config.get("Settings", "cockpit_lamp_test_seconds", fallback="3"))
        except (TypeError, ValueError):
            n = 3.0
        return max(0.5, min(10.0, n))


MSFS_PROCESS_NAMES = (
    "FlightSimulator.exe",
    "FlightSimulator2024.exe",
    "FlightSimulator2020.exe",
)
TASKLIST_CACHE_SEC = 5.0
TASKLIST_MISSES_TO_DROP = 3


class _ProcessWatch:
    """tasklist at most every 5 s (it ran twice per second); MSFS counts as gone
    only after three negative answers in a row (D9)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.checked_at = 0.0
        self.last_seen: Optional[bool] = None  # None = never asked yet
        self.misses = 0

    def _query(self) -> Optional[bool]:
        if sys.platform != "win32":
            return True
        try:
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).decode("utf-8", errors="ignore").lower()
            return any(n.lower() in out for n in MSFS_PROCESS_NAMES)
        except Exception:
            return None  # tasklist hung or failed: no verdict

    def running(self) -> bool:
        with self._lock:
            now = time.time()
            if self.last_seen is not None and now - self.checked_at < TASKLIST_CACHE_SEC:
                return bool(self.last_seen)
            self.checked_at = now
        found = self._query()
        with self._lock:
            if found is None:
                return bool(self.last_seen)
            if found:
                self.misses = 0
                self.last_seen = True
            elif self.last_seen is None:
                self.last_seen = False  # first answer: no flapping to filter yet
            else:
                self.misses += 1
                if self.misses >= TASKLIST_MISSES_TO_DROP:
                    self.last_seen = False
            return bool(self.last_seen)

    def reset(self) -> None:
        with self._lock:
            self.checked_at = 0.0
            self.last_seen = None
            self.misses = 0


PROCESS_WATCH = _ProcessWatch()


def _msfs_process_running() -> bool:
    """True while an MSFS process runs (cached; 3 negative tasklist answers to drop)."""
    return PROCESS_WATCH.running()


# Writable tanks (python-simconnect names). LEVEL is 0.0–1.0 (Percent Over 100).
_FUEL_TANKS = (
    ("FUEL_TANK_LEFT_MAIN_CAPACITY", "FUEL_TANK_LEFT_MAIN_QUANTITY", "FUEL_TANK_LEFT_MAIN_LEVEL"),
    ("FUEL_TANK_RIGHT_MAIN_CAPACITY", "FUEL_TANK_RIGHT_MAIN_QUANTITY", "FUEL_TANK_RIGHT_MAIN_LEVEL"),
    ("FUEL_TANK_CENTER_CAPACITY", "FUEL_TANK_CENTER_QUANTITY", "FUEL_TANK_CENTER_LEVEL"),
    ("FUEL_TANK_CENTER2_CAPACITY", "FUEL_TANK_CENTER2_QUANTITY", "FUEL_TANK_CENTER2_LEVEL"),
    ("FUEL_TANK_CENTER3_CAPACITY", "FUEL_TANK_CENTER3_QUANTITY", "FUEL_TANK_CENTER3_LEVEL"),
    ("FUEL_TANK_LEFT_AUX_CAPACITY", "FUEL_TANK_LEFT_AUX_QUANTITY", "FUEL_TANK_LEFT_AUX_LEVEL"),
    ("FUEL_TANK_RIGHT_AUX_CAPACITY", "FUEL_TANK_RIGHT_AUX_QUANTITY", "FUEL_TANK_RIGHT_AUX_LEVEL"),
    ("FUEL_TANK_LEFT_TIP_CAPACITY", "FUEL_TANK_LEFT_TIP_QUANTITY", "FUEL_TANK_LEFT_TIP_LEVEL"),
    ("FUEL_TANK_RIGHT_TIP_CAPACITY", "FUEL_TANK_RIGHT_TIP_QUANTITY", "FUEL_TANK_RIGHT_TIP_LEVEL"),
    ("FUEL_TANK_EXTERNAL1_CAPACITY", "FUEL_TANK_EXTERNAL1_QUANTITY", "FUEL_TANK_EXTERNAL1_LEVEL"),
    ("FUEL_TANK_EXTERNAL2_CAPACITY", "FUEL_TANK_EXTERNAL2_QUANTITY", "FUEL_TANK_EXTERNAL2_LEVEL"),
)


CORE_SIMVARS = (
    "PLANE_LATITUDE",
    "PLANE_LONGITUDE",
    "PLANE_ALTITUDE",
    "GROUND_VELOCITY",
    "SIMULATION_RATE",
    "SIM_ON_GROUND",
)
CORE_READ_ATTEMPTS = 14  # × 10 ms; the default 4 gave up after 40 ms under load (D9)
PAUSE_EX1_EVENT_ID = 0xFC01  # our own client event id for the "Pause_EX1" system event


class SimReader:
    def __init__(self) -> None:
        self.sm: Optional[SimConnect] = None
        self.aq: Optional[AircraftRequests] = None
        self.ae: Optional[Any] = None
        self.connected = False
        self._misses = 0
        self._io = threading.RLock()
        self._atc_def = None
        self._atc_def256 = None
        self._lvars: Dict[str, Any] = {}
        self._adhoc: Dict[str, Any] = {}
        self._custom_fuel: Optional[bool] = None
        self._last_sim_time: Optional[float] = None
        self._last_sim_time_wall: float = 0.0
        self._pause_ex1: Optional[int] = None
        self._pause_ex1_on = False
        self.read_stats: Dict[str, float] = {"last_ms": 0.0, "max_ms": 0.0, "avg_ms": 0.0, "n": 0.0}

    def _boost_core_reads(self) -> None:
        if not self.aq:
            return
        for key in CORE_SIMVARS:
            try:
                req = self.aq.find(key)
                if req is not None:
                    req.attemps = CORE_READ_ATTEMPTS
            except Exception:
                pass

    def _subscribe_pause_ex1(self) -> None:
        """Real pause detection via the Pause_EX1 system event (behind a switch, D9)."""
        self._pause_ex1 = None
        self._pause_ex1_on = False
        sm = self.sm
        if sm is None or not feature_enabled("pause_ex1"):
            return
        try:
            original = sm.handle_id_event
            reader = self

            def handler(evt: Any) -> None:
                try:
                    if int(evt.uEventID) == PAUSE_EX1_EVENT_ID:
                        reader._pause_ex1 = int(evt.dwData)
                        return
                except Exception:
                    pass
                original(evt)

            sm.handle_id_event = handler
            err = sm.dll.SubscribeToSystemEvent(sm.hSimConnect, PAUSE_EX1_EVENT_ID, b"Pause_EX1")
            self._pause_ex1_on = bool(sm.IsHR(err, 0))
        except Exception as e:
            print("Pause_EX1:", e)
            self._pause_ex1_on = False

    def pause_ex1_active(self) -> Optional[bool]:
        """True/False from Pause_EX1, None while unknown or switched off."""
        if not self._pause_ex1_on or self._pause_ex1 is None:
            return None
        return self._pause_ex1 != 0

    def note_read_ms(self, ms: float) -> None:
        st = self.read_stats
        n = st.get("n", 0.0) + 1.0
        st["n"] = n
        st["last_ms"] = round(ms, 1)
        st["max_ms"] = round(max(st.get("max_ms", 0.0), ms), 1)
        st["avg_ms"] = round(st.get("avg_ms", 0.0) + (ms - st.get("avg_ms", 0.0)) / min(n, 50.0), 1)

    def connect(self) -> tuple[bool, str]:
        self.close()
        if not _msfs_process_running():
            return False, "MSFS läuft nicht – bitte Microsoft Flight Simulator starten."
        try:
            self.sm = SimConnect()
            self.aq = AircraftRequests(self.sm, _time=150, _attemps=4)
            self._boost_core_reads()
            try:
                self.ae = AircraftEvents(self.sm)
            except Exception:
                self.ae = None
            # SimConnect() can succeed briefly even without a live session – verify data.
            for _ in range(8):
                time.sleep(0.25)
                lat = self._get("PLANE_LATITUDE")
                lon = self._get("PLANE_LONGITUDE")
                if lat is not None and lon is not None:
                    self.connected = True
                    self._misses = 0
                    self._subscribe_pause_ex1()
                    try:
                        GROUND.attach(self.sm)
                        GROUND.start_def = STATE.config.hotkey_start
                        GROUND.end_def = STATE.config.hotkey_end
                        GROUND.volume = STATE.config.hotkey_volume
                        GROUND.apply_hotkeys()
                    except Exception as e:
                        print("hotkey attach:", e)
                    return True, "Mit MSFS verbunden"
            self.close()
            return False, "MSFS antwortet nicht (noch im Menü / nicht bereit)."
        except Exception as e:
            self.close()
            return False, f"SimConnect-Fehler: {e}"

    def close(self) -> None:
        try:
            GROUND.detach()
        except Exception:
            pass
        with self._io:
            if self.sm:
                try:
                    self.sm.exit()
                except Exception:
                    pass
            self.sm = None
            self.aq = None
            self.ae = None
            self.connected = False
            self._misses = 0
            self._atc_def = None
            self._atc_def256 = None
            self._lvars = {}
            self._adhoc = {}
            self._custom_fuel = None
            self._last_sim_time = None
            self._last_sim_time_wall = 0.0
            self._pause_ex1 = None
            self._pause_ex1_on = False

    def ping_process(self) -> bool:
        """Cheap health check for the UI thread — never blocks on SimConnect."""
        if not self.connected:
            return False
        if not _msfs_process_running():
            self.close()
            return False
        return True

    def refresh_connection(self) -> bool:
        """Keep connected flag honest – drop it when MSFS disappears or reads fail."""
        if not self.connected:
            return False
        if not _msfs_process_running():
            self.close()
            return False
        sample = self.read()
        if sample is None:
            self._misses += 1
            if self._misses >= 8:
                self.close()
                return False
            return True
        self._misses = 0
        return True

    def sim_title(self) -> str:
        for name in ("TITLE", "ATC_TYPE", "ATC_MODEL"):
            val = _sim_str(self._get(name))
            if val:
                return val
        return ""

    def has_custom_fuel(self, extra: str = "") -> bool:
        """Fenix/PMDG/ToLiss etc. own the tanks — SimConnect writes bounce and look like cheating."""
        blob = f"{self.sim_title()} {extra}".lower()
        self._custom_fuel = any(m in blob for m in _CUSTOM_FUEL_MARKERS)
        return bool(self._custom_fuel)

    def _get(self, name: str) -> Any:
        if not self.aq:
            return None
        with self._io:
            try:
                return self.aq.get(name)
            except Exception:
                return None

    def _get_any(self, *names: str) -> Any:
        for name in names:
            v = self._get(name)
            if v is not None:
                return v
        return None

    def _sim_truthy(self, val: Any) -> bool:
        if val is None:
            return False
        if isinstance(val, (bytes, bytearray)):
            val = val.decode("ascii", "ignore")
        if isinstance(val, str):
            return val.strip().lower() in ("1", "-1", "true", "yes")
        try:
            # SimConnect BOOLs sometimes arrive as -1 (all bits set) – that is "true".
            return abs(float(val)) > 0.5
        except (TypeError, ValueError):
            return bool(val)

    def _adhoc_get(self, name: str, unit: str) -> Optional[float]:
        """A simvar outside the python-simconnect catalog (cached request)."""
        if not self.sm:
            return None
        key = f"{name}|{unit}"
        req = self._adhoc.get(key)
        try:
            if req is None:
                from SimConnect.RequestList import Request

                req = Request((name.encode("utf-8"), unit.encode("utf-8")), self.sm, _time=500, _attemps=10)
                self._adhoc[key] = req
            with self._io:
                val = req.value
            return float(val) if val is not None else None
        except Exception:
            return None

    def fuel_system_info(self) -> dict:
        """E7: what the sim says about its fuel system (reported to the server)."""
        out: Dict[str, Any] = {}
        if not self.connected:
            return out

        def num(v: Any) -> Optional[float]:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return round(f, 3) if math.isfinite(f) else None

        nfs = self._adhoc_get("NEW FUEL SYSTEM", "Bool")
        out["new_fuel_system"] = None if nfs is None else bool(abs(nfs) > 0.5)
        out["fuel_weight_per_gallon_lb"] = num(self._get("FUEL_WEIGHT_PER_GALLON"))
        out["sim_capacity_gal"] = num(self._get("FUEL_TOTAL_CAPACITY"))
        out["fuel_total_gal"] = num(self._get("FUEL_TOTAL_QUANTITY"))
        # MSFS 2024 (SU2+) EX1 variants; names not verifiable here – None when unknown.
        out["sim_capacity_ex1_gal"] = num(self._adhoc_get("FUEL TOTAL CAPACITY EX1", "Gallons"))
        out["fuel_total_ex1_gal"] = num(self._adhoc_get("FUEL TOTAL QUANTITY EX1", "Gallons"))
        out["title"] = self.sim_title()[:120]
        return out

    def reposition(self, point: dict) -> bool:
        """Put the aircraft back on a recorded track point (pilot's explicit choice)."""
        if not self.connected or not self.aq:
            return False
        try:
            lat = float(point["latitude"])
            lon = float(point["longitude"])
        except (KeyError, TypeError, ValueError):
            return False
        alt = _finite(point.get("altitude"))
        hdg = point.get("heading")
        gs = _finite(point.get("ground_speed"))
        on_ground = bool(point.get("on_ground"))
        with self._io:
            ok = self._set("PLANE_LATITUDE", lat, force=True)
            ok = self._set("PLANE_LONGITUDE", lon, force=True) and ok
            ok = self._set("PLANE_ALTITUDE", alt, force=True) and ok
            if hdg is not None:
                self._set("PLANE_HEADING_DEGREES_TRUE", math.radians(_finite(hdg)), force=True)
            if not on_ground and gs > 30:
                # keep flying at the recorded ground speed (ft/s along the body axis)
                self._set("VELOCITY_BODY_Z", gs * 1.68781, force=True)
        return ok

    def _set(self, name: str, value: Any, force: bool = False) -> bool:
        """Set sim vars. force=True also writes when the catalog omits settable."""
        if not self.aq:
            return False
        with self._io:
            try:
                req = self.aq.find(name)
                if req is None:
                    return False
                if not force and not getattr(req, "settable", False):
                    return False
                req.value = value
                return True
            except Exception as e:
                print(f"Sim set {name} failed:", e)
                return False

    def _tank_capacity(self, cap_name: str) -> float:
        cap = self._get(cap_name)
        try:
            cap_f = float(cap) if cap is not None else 0.0
        except (TypeError, ValueError):
            cap_f = 0.0
        return cap_f if cap_f > 0.05 else 0.0

    def _write_fuel_tank(self, qty_name: str, lvl_name: str, cap: float, qty: float) -> int:
        writes = 0
        qty = min(max(0.0, qty), cap) if cap > 0 else 0.0
        level = (qty / cap) if cap > 0 else 0.0
        if self._set(qty_name, qty):
            writes += 1
        if self._set(lvl_name, level):
            writes += 1
        return writes

    def engines_or_apu_alive(self) -> bool:
        """True if a turbine/piston is combusting or the APU is spooled — do not dump tanks."""
        for name in (
            "GENERAL_ENG_COMBUSTION:1",
            "GENERAL_ENG_COMBUSTION:2",
            "GENERAL_ENG_COMBUSTION:3",
            "GENERAL_ENG_COMBUSTION:4",
            "GENERAL ENG COMBUSTION:1",
            "GENERAL ENG COMBUSTION:2",
            "ENG_COMBUSTION:1",
            "ENG COMBUSTION:1",
        ):
            if self._sim_truthy(self._get(name)):
                return True
        for name, floor in (
            ("APU_PCT_RPM", 8.0),
            ("APU PCT RPM", 8.0),
            ("TURB_ENG_N1:1", 8.0),
            ("TURB ENG N1:1", 8.0),
            ("PROP_RPM:1", 200.0),
            ("PROP RPM:1", 200.0),
            ("GENERAL_ENG_RPM:1", 200.0),
            ("GENERAL ENG RPM:1", 200.0),
        ):
            v = self._get(name)
            try:
                if v is not None and float(v) >= floor:
                    return True
            except (TypeError, ValueError):
                pass
        return False

    def _read_fuel_total_gal(self) -> float:
        raw = self._get("FUEL_TOTAL_QUANTITY")
        try:
            return float(raw) if raw is not None else -1.0
        except (TypeError, ValueError):
            return -1.0

    def _fill_fuel_wings_first(
        self,
        fuel_gal: float,
        *,
        zero_first: bool = False,
        main_pair_only: bool = False,
    ) -> tuple[int, int]:
        """Fill wings before the center tank.

        Never dump every tank to 0 first — that flames out a running engine/APU
        (Caravan, Cessna, Fenix, …) in the 150 ms gap. Unused groups are left
        alone: zeroing “empty” tip/aux tanks on a Cessna 152 dumps the real
        wing fuel (SimConnect 24 gal → 0, engine will not restart).
        """
        groups = (
            (_FUEL_TANKS[0], _FUEL_TANKS[1]),
            (_FUEL_TANKS[5], _FUEL_TANKS[6]),
            (_FUEL_TANKS[7], _FUEL_TANKS[8]),
            (_FUEL_TANKS[2],),
            (_FUEL_TANKS[3],),
            (_FUEL_TANKS[4],),
            (_FUEL_TANKS[9], _FUEL_TANKS[10]),
        )
        if main_pair_only:
            groups = (groups[0],)
        resolved: List[List[tuple[str, str, float]]] = []
        writes = 0
        for group in groups:
            tanks: List[tuple[str, str, float]] = []
            for cap_name, qty_name, lvl_name in group:
                cap = self._tank_capacity(cap_name)
                if cap <= 0:
                    continue
                tanks.append((qty_name, lvl_name, cap))
                if zero_first:
                    writes += self._write_fuel_tank(qty_name, lvl_name, cap, 0.0)
            if tanks:
                resolved.append(tanks)
        if zero_first:
            time.sleep(0.15)

        remain = max(0.0, float(fuel_gal))
        for tanks in resolved:
            if remain <= 0.02:
                # Leave unused groups alone. Zeroing "empty" tip/aux tanks on a
                # Cessna 152 dumps the real wing fuel (SimConnect 24 gal → 0).
                break
            total_cap = sum(t[2] for t in tanks)
            take = min(remain, total_cap)
            leftover = take
            used_total = 0.0
            for i, (qty_name, lvl_name, cap) in enumerate(tanks):
                if i == len(tanks) - 1:
                    used = min(cap, leftover)
                else:
                    used = min(cap, take * (cap / total_cap) if total_cap > 0 else 0.0)
                    leftover -= used
                writes += self._write_fuel_tank(qty_name, lvl_name, cap, used)
                used_total += used
            remain -= used_total
        return writes, len(resolved)

    def _set_absolute_fuel(
        self,
        fuel_gal: float,
        *,
        sim_title: str = "",
        allow_zero: bool = True,
    ) -> tuple[int, int]:
        """Set total fuel to an absolute gallon target (start / fast refuel)."""
        alive = self.engines_or_apu_alive()
        main_only = _main_pair_only_fill(f"{sim_title} {self.sim_title()}")
        zero = allow_zero and not alive
        fuel_set, tank_groups = self._fill_fuel_wings_first(
            fuel_gal, zero_first=zero, main_pair_only=main_only
        )
        time.sleep(0.35)
        sim_total_f = self._read_fuel_total_gal()
        tol = max(1.0, fuel_gal * 0.08)
        if sim_total_f >= 0 and abs(sim_total_f - fuel_gal) > tol:
            fuel_set, tank_groups = self._fill_fuel_wings_first(
                fuel_gal, zero_first=zero, main_pair_only=main_only
            )
            time.sleep(0.25)
            sim_total_f = self._read_fuel_total_gal()
        # ATR/Dash: aux tanks kept old fuel or a retry stacked a second full load.
        if sim_total_f > fuel_gal * 1.06 and not alive:
            fuel_set, tank_groups = self._fill_fuel_wings_first(
                fuel_gal, zero_first=True, main_pair_only=main_only
            )
            time.sleep(0.25)
            sim_total_f = self._read_fuel_total_gal()
        return fuel_set, tank_groups

    def _lvar(self, name: str) -> Any:
        """Cached SimConnect request for a GSX (or other) LVar."""
        req = self._lvars.get(name)
        if req is not None:
            return req
        if not self.sm:
            return None
        try:
            from SimConnect.RequestList import Request

            req = Request(
                (name.encode("utf-8"), b"Number"),
                self.sm,
                _time=80,
                _settable=True,
                _attemps=6,
            )
            self._lvars[name] = req
            return req
        except Exception:
            return None

    def _lvar_get(self, name: str) -> Optional[float]:
        req = self._lvar(name)
        if req is None:
            return None
        try:
            val = req.value
            if val is None:
                return None
            return float(val)
        except (TypeError, ValueError, Exception):
            return None

    def _lvar_set(self, name: str, value: float) -> bool:
        req = self._lvar(name)
        if req is None:
            return False
        try:
            req.settable = True
            req.value = float(value)
            return True
        except Exception:
            return False

    def gsx_running(self) -> bool:
        """True only when GSX Pro's Couatl engine is actually up (not a leftover LVar)."""
        started = self._lvar_get(GSX_LVAR_COUATL)
        return started is not None and started >= 1

    def gsx_refuel_ready(self) -> bool:
        hose = self._lvar_get(GSX_LVAR_HOSE)
        if hose is not None and hose >= 1:
            return True
        state = self._lvar_get(GSX_LVAR_REFUEL)
        return state is not None and state >= 5

    def gsx_refuel_state(self) -> float:
        val = self._lvar_get(GSX_LVAR_REFUEL)
        return float(val) if val is not None else -1.0

    def gsx_call_fuel_truck(self) -> bool:
        """Ask GSX to send the fuel truck (menu item 3). Falls back if GSX is idle."""
        self._lvar_set(GSX_LVAR_SET_CUST_FUEL, 1)
        self._lvar_set(GSX_LVAR_SET_PROG_FUEL, 1)
        state = self.gsx_refuel_state()
        if state >= 4:
            return True
        self._lvar_set(GSX_LVAR_MENU_OPEN, 1)
        self._fire_event("EXTERNAL_SYSTEM_TOGGLE")
        time.sleep(1.1)
        # MENU_CHOICE is 0-based; item 3 = Request Refueling
        ok = self._lvar_set(GSX_LVAR_MENU_CHOICE, float(GSX_REFUEL_MENU_ITEM - 1))
        time.sleep(0.5)
        # Operator picker (first company) — ignored if that menu is not open
        self._lvar_set(GSX_LVAR_MENU_CHOICE, 0)
        return ok

    def gsx_menu_choice(self, index: int) -> bool:
        """Open GSX main menu and pick a 0-based item (Deboard=0 … Reposition=9)."""
        idx = max(0, min(9, int(index)))
        self._lvar_set(GSX_LVAR_MENU_OPEN, 1)
        self._fire_event("EXTERNAL_SYSTEM_TOGGLE")
        time.sleep(1.1)
        ok = self._lvar_set(GSX_LVAR_MENU_CHOICE, float(idx))
        time.sleep(0.4)
        if idx in (5,):
            self._lvar_set(GSX_LVAR_JETWAY, 1)
        if idx in (6,):
            self._lvar_set(GSX_LVAR_STAIRS, 1)
        return ok

    def _set_payload_station(self, index: int, pounds: float) -> bool:
        """Write PAYLOAD STATION WEIGHT for station index (1-based)."""
        names = (
            f"PAYLOAD_STATION_WEIGHT:{index}",
            f"PAYLOAD STATION WEIGHT:{index}",
        )
        ok = False
        for name in names:
            if self._set(name, float(pounds), force=True):
                ok = True
                break
        return ok

    def apply_economy_load(self, load: dict, *, repair: bool = False) -> dict:
        """
        Write economy fuel + payload into MSFS (FSE-style after /flights/start).
        repair=True: only fix drifted payload stations — never rewrite fuel mid-flight
        (that would refill tanks and bounce the aircraft every few seconds).
        """
        result: Dict[str, Any] = {"fuel_ok": False, "payload_ok": False, "details": []}
        if not self.connected or not self.aq or not isinstance(load, dict):
            result["details"].append("not_connected")
            return result

        fuel_gal = float(load.get("fuel_gallons") or 0)
        fuel_pct = load.get("fuel_percent")
        capacity_eco = float(load.get("fuel_capacity") or 0)
        if fuel_pct is None and capacity_eco > 0:
            fuel_pct = min(100.0, max(0.0, (fuel_gal / capacity_eco) * 100.0))
        elif fuel_pct is None:
            fuel_pct = 0.0
        fuel_pct = float(fuel_pct)
        level = min(1.0, max(0.0, fuel_pct / 100.0))

        if repair:
            result["fuel_ok"] = True
            result["details"].append("fuel=skipped(repair)")
        else:
            self._custom_fuel = None
            custom = self.has_custom_fuel(str(load.get("sim_title") or ""))
            is_heavy = bool(load.get("is_heavy")) or custom
            sim_now = self._get("FUEL_TOTAL_QUANTITY")
            try:
                sim_now_f = float(sim_now) if sim_now is not None else -1.0
            except (TypeError, ValueError):
                sim_now_f = -1.0
            try:
                fuel_mode = str(STATE.config.fuel_mode or "auto").strip().lower()
            except Exception:
                fuel_mode = "auto"
            if fuel_mode not in ("auto", "direct", "gsx"):
                fuel_mode = "auto"
            gsx_up = self.gsx_running()
            alive = self.engines_or_apu_alive()
            if fuel_mode == "direct":
                use_gsx = False
            elif fuel_mode == "gsx":
                use_gsx = gsx_up and not alive
            else:
                use_gsx = is_heavy and gsx_up and not alive
            result["fuel_mode"] = fuel_mode
            if custom and fuel_mode != "direct":
                # Fenix/PMDG: writing A: tanks is overwritten and trips "fuel changed".
                kg = fuel_gal * (LB_PER_GAL / 2.20462262)
                result["fuel_ok"] = True
                result["custom_fuel"] = True
                result["gsx_refuel"] = bool(use_gsx)
                result["details"].append(
                    f"fuel=custom_efb target_gal={fuel_gal:.1f} target_kg={kg:.0f} "
                    f"sim_gal={sim_now_f:.1f} gsx={'yes' if result['gsx_refuel'] else 'no'} "
                    f"engines={'on' if alive else 'off'} title={self.sim_title()[:48]}"
                )
            elif (
                use_gsx
                and sim_now_f >= 0
                and sim_now_f + 1.5 < fuel_gal
            ):
                result["fuel_ok"] = True
                result["gsx_refuel"] = True
                result["details"].append(
                    f"fuel=gsx_truck target_gal={fuel_gal:.1f} pct={fuel_pct:.1f} "
                    f"sim_gal={sim_now_f:.1f}"
                )
            elif (
                sim_now_f >= 0
                and abs(sim_now_f - fuel_gal) <= max(3.0, fuel_gal * 0.10)
            ):
                # Already at economy quantity — skip tank writes. Rewriting
                # Cessna 152 tanks with the engine off often dumps them to 0.
                why = "engines_running" if alive else "already_close"
                result["fuel_ok"] = True
                result["details"].append(
                    f"fuel=kept({why}) sim_gal={sim_now_f:.1f} "
                    f"target_gal={fuel_gal:.1f}"
                )
            else:
                sim_title = str(load.get("sim_title") or "")
                fuel_set, tank_groups = self._set_absolute_fuel(
                    fuel_gal, sim_title=sim_title
                )
                if fuel_set <= 0:
                    for lvl in ("FUEL_TANK_LEFT_MAIN_LEVEL", "FUEL_TANK_RIGHT_MAIN_LEVEL"):
                        if self._set(lvl, level):
                            fuel_set += 1

                sim_total_f = self._read_fuel_total_gal()
                result["fuel_ok"] = fuel_set > 0 and (
                    sim_total_f < 0 or abs(sim_total_f - fuel_gal) <= max(1.5, fuel_gal * 0.15)
                )
                why = "direct"
                if is_heavy and not use_gsx:
                    why = "direct(no_gsx)"
                result["details"].append(
                    f"fuel_vars={fuel_set} target_gal={fuel_gal:.1f} pct={fuel_pct:.1f} "
                    f"sim_gal={sim_total_f:.1f} tank_groups={tank_groups} "
                    f"order=wings-first via={why}"
                )
                if _main_pair_only_fill(f"{sim_title} {self.sim_title()}"):
                    result["details"].append("fuel=main_pair_only")
                if is_heavy and not use_gsx:
                    result["details"].append(
                        "Großflugzeug ohne GSX: Fuel wie bei GA direkt ins Flugmodell."
                    )

        payload_lb = float(load.get("payload_lb") or 0)
        if payload_lb <= 0 and load.get("payload_kg") is not None:
            payload_lb = float(load["payload_kg"]) * 2.20462262
        crew_kg = float(load.get("crew_kg") or 77)
        crew_lb = crew_kg * 2.20462262

        seats_used = int(load.get("seats_used") or 0)
        passengers = int(load.get("passengers") or 0)
        if passengers <= 0:
            for a in load.get("assignments") or []:
                units = str(a.get("units") or "").lower()
                amt = int(float(a.get("amount") or 0))
                if units.startswith("pass") or units in ("pax", "pax."):
                    passengers += max(0, amt)
        if seats_used <= 1 and passengers > 0:
            seats_used = passengers + 1
        if seats_used <= 1 and payload_lb > crew_lb * 1.5:
            seats_used = max(seats_used, int(round(payload_lb / max(crew_lb, 1.0))))
        if seats_used <= 0:
            seats_used = 1 if payload_lb > 0 else 0
        person_lb = max(crew_lb, 170.0)

        n_st = self._get("PAYLOAD_STATION_COUNT")
        try:
            n_stations = int(float(n_st)) if n_st is not None else 14
        except (TypeError, ValueError):
            n_stations = 14
        n_stations = max(1, min(n_stations, 20))

        pax_lb, cargo_lb = split_pax_cargo_lb(payload_lb, passengers, person_lb)
        weights = distribute_payload_stations(
            n_stations,
            payload_lb,
            passengers,
            person_lb,
        )

        def _read_station(index: int) -> float:
            got = self._get(f"PAYLOAD_STATION_WEIGHT:{index}")
            if got is None:
                got = self._get(f"PAYLOAD STATION WEIGHT:{index}")
            try:
                return float(got) if got is not None else -1.0
            except (TypeError, ValueError):
                return -1.0

        if repair:
            if self.has_custom_fuel() or result.get("custom_fuel"):
                result["payload_ok"] = True
                result["details"].append("payload=skipped(repair,custom_efb)")
                return result
            drifted = 0
            for i, w in enumerate(weights, start=1):
                cur = _read_station(i)
                if cur < 0 or abs(cur - w) > max(8.0, w * 0.15 if w > 0 else 8.0):
                    drifted += 1
            if drifted == 0:
                result["payload_ok"] = True
                result["details"].append("payload=ok(no_drift)")
                return result
            payload_set = 0
            for i, w in enumerate(weights, start=1):
                cur = _read_station(i)
                if cur >= 0 and abs(cur - w) <= max(8.0, w * 0.15 if w > 0 else 8.0):
                    continue
                if self._set_payload_station(i, float(w)):
                    payload_set += 1
            result["payload_ok"] = payload_set > 0
            result["details"].append(f"payload=repair stations_fixed={payload_set}/{drifted}")
            return result

        if result.get("custom_fuel"):
            title = str(load.get("sim_title") or self.sim_title())
            payware = apply_payware_payload(self, load, title=title)
            if payware.get("ok"):
                result["payload_ok"] = True
                result["details"].append(
                    "payload=payware_pfb "
                    + ";".join(str(x) for x in (payware.get("details") or []))
                )
            else:
                result["payload_ok"] = True
                result["details"].append(
                    "payload=efb (Payware-PFB — bitte „Ins EFB laden“ oder PFB manuell; "
                    "Panel überschreibt keine MSFS-Stationen mehr)"
                )
            return result

        station_cleared = 0
        payload_set = 0
        clear_all = bool(load.get("is_heavy"))
        if clear_all:
            # Airliners: wipe leftover station weight, then load the plan.
            for i in range(1, n_stations + 1):
                if self._set_payload_station(i, 0.0):
                    station_cleared += 1
            for i, w in enumerate(weights, start=1):
                if w <= 0:
                    continue
                if self._set_payload_station(i, float(w)):
                    payload_set += 1
        else:
            # Light GA: write each station to its target in one pass.
            # Zero-all-then-reload glitches Cessna 152 electrics / fuel / start.
            n_write = max(n_stations, len(weights))
            for i in range(1, n_write + 1):
                w = float(weights[i - 1]) if i <= len(weights) else 0.0
                if self._set_payload_station(i, w):
                    if w <= 0:
                        station_cleared += 1
                    else:
                        payload_set += 1

        for _delay in (0.45, 1.2):
            time.sleep(_delay)
            for i, w in enumerate(weights, start=1):
                if w > 0:
                    self._set_payload_station(i, float(w))

        time.sleep(0.2)
        verified = 0
        verified_lb = 0.0
        for i, w in enumerate(weights, start=1):
            if w <= 0:
                continue
            got_f = _read_station(i)
            if got_f >= 0 and abs(got_f - w) <= max(5.0, w * 0.12):
                verified += 1
                verified_lb += got_f
        total_w = self._get("TOTAL_WEIGHT")
        try:
            total_w_f = float(total_w) if total_w is not None else -1.0
        except (TypeError, ValueError):
            total_w_f = -1.0

        result["payload_ok"] = payload_set > 0
        result["payload_stations"] = [
            {"index": i + 1, "lb": round(w, 1)} for i, w in enumerate(weights) if w > 0
        ]
        result["payload_verified"] = verified
        result["payload_verified_lb"] = round(verified_lb, 1)
        result["total_weight_lb"] = round(total_w_f, 1) if total_w_f >= 0 else None
        total_part = f" total_lb={total_w_f:.0f}" if total_w_f >= 0 else ""
        result["details"].append(
            f"payload_lb={payload_lb:.1f} seats_used={seats_used} passengers={passengers} "
            f"person_lb={person_lb:.0f} pax_lb={pax_lb:.1f} cargo_lb={cargo_lb:.1f} "
            f"stations={payload_set}/{n_stations} cleared={station_cleared} "
            f"verified={verified}/{payload_set}{total_part}"
        )
        result["details"].append(
            "Hinweis: MSFS-/Payware-Tablet (Sitzplan) aktualisiert oft nicht — "
            "Gewicht im Flugmodell zählt; Panel/Website sind maßgeblich."
        )
        return result

    def _fire_event(self, name: str) -> bool:
        if not self.ae:
            return False
        with self._io:
            try:
                ev = self.ae.find(name)
                if ev:
                    ev()
                    return True
            except Exception as e:
                print(f"Sim event {name} failed:", e)
        return False

    def _set_atc_string(self, name: bytes, value: str, size: int, cache_attr: str) -> bool:
        from SimConnect.Constants import SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_UNUSED
        from SimConnect.Enum import SIMCONNECT_DATATYPE

        dtype = (
            SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING256
            if size >= 256
            else SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING32
        )
        buf = ctypes.create_string_buffer(size)
        raw = (value.encode("ascii", "ignore") + b"\x00" * size)[:size]
        ctypes.memmove(buf, raw, size)
        def_id = getattr(self, cache_attr)
        if def_id is None:
            def_id = self.sm.new_def_id()
            err = self.sm.dll.AddToDataDefinition(
                self.sm.hSimConnect,
                def_id.value,
                name,
                b"",
                dtype,
                0,
                SIMCONNECT_UNUSED,
            )
            if not self.sm.IsHR(err, 0):
                return False
            setattr(self, cache_attr, def_id)
        err = self.sm.dll.SetDataOnSimObject(
            self.sm.hSimConnect,
            def_id.value,
            SIMCONNECT_OBJECT_ID_USER,
            0,
            0,
            size,
            buf,
        )
        return bool(self.sm.IsHR(err, 0))

    def set_atc_id(self, registration: str) -> bool:
        """Write FlyConomy registration into ATC ID (panel + dynamic exterior)."""
        reg = (registration or "").strip().upper()[:10]
        if not reg or not self.connected or not self.sm:
            return False
        try:
            got0 = self._get("ATC_ID")
            if isinstance(got0, (bytes, bytearray)):
                got0 = got0.decode("ascii", "ignore")
            got0_s = str(got0 or "").replace("\x00", "").strip().upper()
            if got0_s and (got0_s == reg or got0_s.startswith(reg[:4])):
                return True
        except Exception:
            pass
        with self._io:
            ok = False
            try:
                req = self.aq.find("ATC_ID") if self.aq else None
                if req is not None:
                    try:
                        req.settable = True
                        req.value = reg
                        ok = True
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                ok = self._set_atc_string(b"ATC ID", reg, 32, "_atc_def") or ok
                ok = self._set_atc_string(b"ATC ID", reg, 256, "_atc_def256") or ok
            except Exception as e:
                print("ATC ID set failed:", e)
            time.sleep(0.08)
            got = self._get("ATC_ID")
            if isinstance(got, (bytes, bytearray)):
                got = got.decode("ascii", "ignore")
            got_s = str(got or "").replace("\x00", "").strip().upper()
            if got_s and (got_s == reg or got_s.startswith(reg[:4])):
                return True
            if got_s and got_s != reg:
                print(f"ATC ID readback mismatch: wanted {reg} got {got_s!r}")
            return ok

    def enforce_equipment_gates(self, has_gps: bool, has_autopilot: bool) -> dict:
        """
        Without purchased/stock GPS or AP, reset SimConnect AP/GPS coupling (FSE-style).
        Poll after toggles — does not permanently patch aircraft systems.
        """
        out = {"ap_blocked": False, "gps_blocked": False}
        if not self.connected:
            return out
        if not has_autopilot:
            master = self._get("AUTOPILOT_MASTER")
            if master is None:
                master = self._get("AUTOPILOT MASTER")
            if master and float(master) > 0.5:
                self._set("AUTOPILOT_MASTER", 0)
                self._set("AUTOPILOT MASTER", 0)
                self._fire_event("AUTOPILOT_OFF")
                out["ap_blocked"] = True
            # Keep common AP modes off
            for var in (
                "AUTOPILOT_HEADING_LOCK",
                "AUTOPILOT_ALTITUDE_LOCK",
                "AUTOPILOT_NAV1_LOCK",
                "AUTOPILOT_VERTICAL_HOLD",
                "AUTOPILOT_FLIGHT_DIRECTOR_ACTIVE",
            ):
                v = self._get(var)
                if v is not None and float(v) > 0.5:
                    self._set(var, 0)
                    out["ap_blocked"] = True
        if not has_gps:
            for var in ("GPS_DRIVES_NAV1", "GPS DRIVES NAV1"):
                v = self._get(var)
                if v is not None and float(v) > 0.5:
                    self._set(var, 0)
                    out["gps_blocked"] = True
            # NAV/GPS switch to NAV if exposed
            for var in ("GPS_DRIVES_NAV1",):
                self._set(var, 0)
        return out

    def _parking_brake_on(self) -> bool:
        """Fenix/FBW/iniBuilds park brake often lives in an LVar, not BRAKE PARKING POSITION."""
        for name in ("BRAKE_PARKING_POSITION", "BRAKE PARKING POSITION"):
            pb = self._get(name)
            if pb is None:
                continue
            try:
                if float(pb) > 0.5:
                    return True
            except (TypeError, ValueError):
                pass
        for name in PARK_BRAKE_LVARS:
            val = self._lvar_get(name)
            if val is not None and val >= 0.5:
                return True
        return False

    def read(self) -> Optional[Dict[str, Any]]:
        if not self.connected or not self.aq:
            return None
        try:
            data: Dict[str, Any] = {}
            lat = self._get("PLANE_LATITUDE")
            lon = self._get("PLANE_LONGITUDE")
            if lat is None or lon is None:
                return None
            data["latitude"] = float(lat)
            data["longitude"] = float(lon)
            for key, simvar in [
                ("altitude", "PLANE_ALTITUDE"),
                ("ground_speed", "GROUND_VELOCITY"),
                ("heading", "PLANE_HEADING_DEGREES_TRUE"),
                ("fuel_total", "FUEL_TOTAL_QUANTITY"),
                ("total_weight", "TOTAL_WEIGHT"),
                ("indicated_airspeed", "AIRSPEED_INDICATED"),
                # Python-SimConnect catalog key is SIMULATION_RATE; the simvar
                # itself is "SIMULATION RATE". A space here always misses.
                ("simulation_rate", "SIMULATION_RATE"),
            ]:
                v = self._get(simvar)
                if v is not None:
                    data[key] = float(v)
            if "simulation_rate" not in data:
                v = self._get_any("SIMULATION_RATE", "SIMULATION RATE")
                data["simulation_rate"] = float(v) if v is not None else 1.0
            slew = self._sim_truthy(self._get_any("IS_SLEW_ACTIVE", "IS SLEW ACTIVE"))
            pause_ind = self._sim_truthy(self._get_any("PAUSE_INDICATOR", "PAUSE INDICATOR"))
            pause_state_v = self._get_any("PAUSE_STATE", "PAUSE STATE")
            try:
                pause_state = int(float(pause_state_v)) if pause_state_v is not None else 0
            except (TypeError, ValueError):
                pause_state = 0
            sim_time_v = self._get_any("SIMULATION TIME", "SIMULATION_TIME", "ABSOLUTE TIME")
            sim_frozen = False
            now_w = time.time()
            try:
                sim_time = float(sim_time_v) if sim_time_v is not None else None
            except (TypeError, ValueError):
                sim_time = None
            if sim_time is not None and self._last_sim_time is not None:
                wall_dt = now_w - float(self._last_sim_time_wall or now_w)
                sim_dt = sim_time - float(self._last_sim_time)
                if wall_dt >= 0.75 and sim_dt < 0.12:
                    sim_frozen = True
            if sim_time is not None:
                self._last_sim_time = sim_time
                self._last_sim_time_wall = now_w
            rate_now = float(data.get("simulation_rate") or 1.0)
            # Active pause / Pause-at-TOD freezes sim time without slew.
            data["is_slew"] = slew
            ex1 = self.pause_ex1_active()
            flagged = ex1 if ex1 is not None else (pause_ind or pause_state >= 1)
            data["paused"] = (not slew) and (bool(flagged) or sim_frozen or rate_now <= 0.01)
            if ex1 is not None:
                data["pause_ex1"] = int(self._pause_ex1 or 0)
            agl_v = self._get_any("PLANE_ALT_ABOVE_GROUND", "PLANE ALT ABOVE GROUND")
            try:
                agl_f = float(agl_v) if agl_v is not None else None
            except (TypeError, ValueError):
                agl_f = None
            if agl_f is not None and (not math.isfinite(agl_f) or agl_f < 0 or agl_f > 5000):
                agl_f = None
            data["alt_agl"] = agl_f
            on_sim = self._sim_truthy(
                self._get_any("SIM_ON_GROUND", "SIM ON GROUND")
            )
            on_gear = self._sim_truthy(
                self._get_any("GEAR_ON_GROUND", "GEAR ON GROUND", "GEAR_IS_ON_GROUND")
            )
            data["on_ground_sim"] = on_sim
            gs = float(data.get("ground_speed") or 0)
            brake = self._parking_brake_on()
            data["parking_brake"] = brake
            parked = gs <= PARK_GS_KT_AIRLINER
            # Weight-on-wheels / SIM_ON_GROUND. Gear-down on approach is not "on ground"
            # unless speed is already in the landing/rollout band.
            on_gnd = on_sim or (on_gear and gs <= AIRBORNE_GS_KT)
            if not on_gnd and parked and (
                brake or agl_f is None or (agl_f is not None and agl_f < PARK_AGL_FT)
            ):
                # Payware (A220/Fenix) often leaves SIM_ON_GROUND false at the gate.
                # Only when nearly stopped — never in the air.
                on_gnd = True
            data["on_ground"] = on_gnd
            # "On the ground" far up in the air is a SimConnect glitch (seen with
            # Fenix): such a sample is discarded by the monitor (D8).
            data["ground_glitch"] = bool(
                on_sim
                and (
                    (agl_f is not None and agl_f > GROUND_GLITCH_AGL_FT)
                    or (agl_f is None and gs > GROUND_GLITCH_GS_KT)
                )
            )
            for key, simvar in (
                ("aircraft_type", "TITLE"),
                ("atc_model", "ATC_MODEL"),
                ("atc_type", "ATC_TYPE"),
                ("atc_id_sim", "ATC_ID"),
            ):
                s = _sim_str(self._get(simvar))
                if s:
                    data[key] = s
            return data
        except Exception as e:
            print("Read error:", e)
            return None


def _retry_after_of(r: Any, body: Any, default: int = RETRY_INTERVAL_SEC) -> int:
    raw = None
    if isinstance(body, dict):
        raw = body.get("retry_after_seconds")
    if raw is None:
        try:
            raw = r.headers.get("Retry-After")
        except Exception:
            raw = None
    try:
        wait = int(float(raw)) if raw is not None else default
    except (TypeError, ValueError):
        wait = default
    return max(5, min(wait, 1800))


class FlyConomyAPI:
    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.token: Optional[str] = None
        self.user: Optional[dict] = None
        # One door for every HTTP call: measures the server clock (D5) and lets
        # tests replace the network with a fake server.
        self.transport = requests.request

    def _req(self, method: str, url: str, **kwargs: Any) -> Any:
        t0 = time.time()
        r = self.transport(method, url, **kwargs)
        t1 = time.time()
        try:
            SERVER_CLOCK.observe((r.headers or {}).get("Date"), t0, t1)
            note_clock_skew()
        except Exception:
            pass
        return r

    def _token(self) -> str:
        tok = (self.token or "").strip()
        if tok:
            return tok
        stored = (self.config.access_token or "").strip()
        if stored and not self.token:
            self.token = stored
        return stored

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": "FlyConomy-Client/2.0"}
        tok = self._token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        return h

    def _store_session(self, token: str, user: Optional[dict] = None) -> None:
        token = (token or "").strip()
        if token.count(".") < 2 or len(token) < 40:
            return
        self.token = token
        if user:
            self.user = user
        name = str((self.user or {}).get("name") or "")
        self.config.set_access_token(token, name)

    def refresh_session(self) -> bool:
        """Pull a fresh JWT. Old/expired signatures still work on the server."""
        if not self._token():
            return False
        try:
            r = self._req("POST",
                f"{self.config.api_url}/auth/refresh",
                headers=self._headers(),
                timeout=12,
            )
            if r.status_code != 200:
                return False
            data = r.json() if r.content else {}
            new = str(data.get("token") or "").strip()
            if new.count(".") >= 2:
                self._store_session(new, data.get("user") if isinstance(data.get("user"), dict) else None)
                return True
            return False
        except Exception:
            return False

    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> tuple[bool, str]:
        """Discord-only web auth (JWT from Discord OAuth / stored session)."""
        token = (password or self.config.access_token or "").strip()
        # Allow old field misuse: password box = token
        if not token and username and len(username) > 40:
            token = username.strip()
        if not token:
            return False, "Bitte mit Discord anmelden (Konto → Mit Discord anmelden)."
        previous = (self.token or self.config.access_token or "").strip()
        try:
            self.token = token
            r = self._req("GET",
                f"{self.config.api_url}/auth/me",
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code != 200:
                self.token = previous or None
                return False, r.json().get("error", r.text[:200])
            data = r.json()
            self.user = data.get("user")
            name = (self.user or {}).get("name") or "pilot"
            fresh = str(data.get("token") or token).strip()
            self._store_session(fresh, self.user)
            try:
                prefs = self.get_preferences()
                loc = str(prefs.get("locale") or "").strip().lower()
                if loc in ("de", "en"):
                    self.config.set_locale(loc)
            except Exception:
                pass
            try:
                after_login_sync()
            except Exception:
                try:
                    ensure_gateway()
                except Exception:
                    pass
            return True, f"Angemeldet als {name} (Discord)"
        except Exception as e:
            self.token = previous or None
            return False, str(e)

    def get_myflight(self) -> dict:
        r = self._req("GET", f"{self.config.api_url}/myflight", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def get_active(self) -> Optional[dict]:
        r = self._req("GET", f"{self.config.api_url}/flights/active", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("flight")

    def get_nearby(self, lat: float, lon: float) -> Optional[dict]:
        try:
            r = self._req("GET",
                f"{self.config.api_url}/airports/search/nearby",
                params={"lat": lat, "lon": lon, "radius": 3, "limit": 1},
                headers=self._headers(),
                timeout=6,
            )
            if r.status_code != 200:
                return None
            rows = (r.json() or {}).get("airports") or []
            if not rows:
                return None
            icao = str(rows[0].get("icao") or "").upper()
            if not icao:
                return None
            return {"icao": icao, "name": str(rows[0].get("name") or "")}
        except Exception:
            return None

    def request_livery(self, payload: dict) -> dict:
        r = self._req("POST",
            f"{self.config.api_url}/liveries/request",
            json=payload,
            headers=self._headers(),
            timeout=15,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text[:200])
        return data

    def get_fuel_sellers(self, icao: str, fuel_type: str) -> dict:
        icao = str(icao or "").strip().upper()
        ft = "jeta" if str(fuel_type).lower() in ("jeta", "1", "jet-a") else "100ll"
        r = self._req("GET",
            f"{self.config.api_url}/fbos/airport/{icao}/fuel-sellers",
            params={"fuel_type": ft},
            headers=self._headers(),
            timeout=12,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text[:200])
        return data

    def refuel(self, payload: dict) -> dict:
        r = self._req("POST",
            f"{self.config.api_url}/myflight/refuel",
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text[:200])
        return data

    def get_hours_remaining(self) -> Optional[float]:
        try:
            r = self._req("GET", f"{self.config.api_url}/users/stats", headers=self._headers(), timeout=8)
            if r.status_code != 200:
                return None
            data = r.json() or {}
            if data.get("hoursRemaining48h") is not None:
                return float(data["hoursRemaining48h"])
            return None
        except Exception:
            return None

    def start_flight(self, sample: dict) -> dict:
        try:
            r = self._req("POST",
                f"{self.config.api_url}/flights/start",
                json=sample,
                headers=self._headers(),
                timeout=20,
            )
        except requests.RequestException as e:
            raise RetryableError(f"Server nicht erreichbar: {e}") from e
        if r.status_code in (429, 502, 503, 504) or r.status_code >= 500:
            try:
                body = r.json()
            except Exception:
                body = {}
            raise RetryableError(
                (body or {}).get("error") or "Server gerade nicht verfügbar",
                _retry_after_of(r, body),
            )
        try:
            data = r.json()
        except Exception as e:
            raise RetryableError("Server antwortete ungültig") from e
        if r.status_code >= 400:
            raise RuntimeError(data.get("error", r.text))
        return data

    def tracking(self, sample: dict) -> dict:
        try:
            r = self._req("POST",
                f"{self.config.api_url}/flights/tracking",
                json=sample,
                headers=self._headers(),
                timeout=15,
            )
        except requests.RequestException as e:
            raise RetryableError(f"Server nicht erreichbar: {e}") from e
        if r.status_code in (429, 502, 503, 504) or r.status_code >= 500:
            raise RetryableError("Server gerade nicht verfügbar")
        if not r.content:
            return {}
        try:
            data = r.json() if r.content else {}
        except Exception:
            raise RetryableError("Server antwortete ungültig")
        if r.status_code >= 400:
            msg = str((data or {}).get("error") or r.text or f"HTTP {r.status_code}")[:400]
            if isinstance(data, dict) and data.get("rejected"):
                # The server has already closed the flight – its verdict, not ours.
                raise ServerRejectedError(msg)
            raise RuntimeError(msg)
        return data

    def end_flight(
        self, sample: dict, track: Optional[list] = None, session_key: Optional[str] = None
    ) -> dict:
        payload = dict(sample)
        if track:
            payload["track"] = track
        if session_key:
            payload["session_key"] = session_key
        return self.submit_end(payload)

    def submit_end(self, payload: dict) -> dict:
        """Posts a prepared end payload. Raises RetryableError when it is worth queueing."""
        try:
            r = self._req("POST",
                f"{self.config.api_url}/flights/end",
                json=payload,
                headers=self._headers(),
                timeout=45,
            )
        except requests.RequestException as e:
            raise RetryableError(f"Server nicht erreichbar: {e}") from e

        if r.status_code in (429, 502, 503, 504) or r.status_code >= 500:
            try:
                body = r.json()
            except Exception:
                body = {}
            raise RetryableError(
                (body or {}).get("error") or "Server gerade nicht verfügbar",
                max(15, _retry_after_of(r, body)),
            )

        try:
            data = r.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if r.status_code == 413:
            raise RuntimeError(
                "Track zu groß (request entity too large). Bitte Client aktualisieren."
            )
        if r.status_code >= 400:
            err = data.get("error") or data.get("validation") or r.text
            if isinstance(err, (dict, list)):
                err = json.dumps(err)[:240]
            code = str(data.get("code") or "")
            err_l = str(err).lower()
            if code in ("restricted", "banned", "tos_required"):
                raise RuntimeError(str(err)[:400] or f"HTTP {r.status_code}")
            # Session codes first: an old server sends SESSION_INVALID as 403 with
            # "Session" in the text, which used to land in the token branch below
            # and was retried forever (C9).
            if code in ("SESSION_INVALID", "SESSION_MISSING"):
                raise SessionInvalidError(
                    str(err)[:400]
                    or "Flug-Session ungültig. Bitte „Abbrechen“ und neu starten.",
                    code,
                )
            if code == "END_BUSY" or (
                r.status_code == 409 and "abrechnung" in err_l and "bereits abgerechnet" not in err_l
            ):
                raise EndBusyError(
                    str(err)[:400] or "Flugende läuft noch – gleich nochmal versuchen.",
                    _retry_after_of(r, data, default=8),
                )
            if code in ("FLIGHT_ALREADY_REJECTED", "FLIGHT_REJECTED", "FLIGHT_CANCELLED"):
                reason = str(data.get("reason") or data.get("rejection_reason") or err)[:400]
                raise FlightClosedError(reason, code)
            if r.status_code == 409 and "bereits abgerechnet" in err_l:
                # Old server: the first /end was booked, only its answer got lost.
                return {
                    "already_logged": True,
                    "message": str(err)[:400],
                    "flight_id": data.get("flight_id"),
                    "new_balance": data.get("new_balance"),
                }
            if "kein aktiver flug" in err_l or "no active flight" in err_l:
                raise NoActiveFlightError(str(err)[:400])
            if "auszahlung gesperrt" in err_l:
                raise FlightClosedError(str(err)[:400], "FLIGHT_REJECTED")
            if r.status_code in (401, 403) and (
                code in ("token_expired", "session_revoked", "token")
                or "token" in err_l
                or "session" in err_l
                or "anmeld" in err_l
            ):
                raise KeepFlyingError(
                    t_ui(
                        "Anmeldung abgelaufen. Im MiniClient-Tray „Mit Discord anmelden“, "
                        "danach „Erneut versuchen“. Der Flug bleibt aktiv — nicht auf Abbrechen drücken.",
                        "Sign-in expired. Use Discord login in the MiniClient tray, then Retry. "
                        "The flight stays active — do not press Cancel.",
                    ),
                    "TOKEN",
                )
            if code == "HEAVY_AIRPORT_TOO_SMALL":
                raise AirportRejectedError(
                    str(err)[:400], str(data.get("airport") or "")
                )
            if code == "CREW_ALREADY_ENDED" or (
                "crewflug" in str(err).lower()
                and ("geloggt" in str(err).lower() or "bereits beendet" in str(err).lower())
            ):
                return {
                    "logged": True,
                    "crew": True,
                    "code": "CREW_ALREADY_ENDED",
                    "message": str(err)[:400] or "Crewflug geloggt.",
                    "total_payment": data.get("total_payment") or 0,
                    "validation": data.get("validation")
                    or {"status": "OK"},
                    "flight": data.get("flight") or {},
                    "flight_id": data.get("flight_id"),
                }
            if code == "NOT_ON_GROUND":
                raise KeepFlyingError(str(err)[:400], "NOT_ON_GROUND")
            if code == "BAD_POSITION":
                raise KeepFlyingError(str(err)[:400], "BAD_POSITION")
            raise RuntimeError(str(err)[:400] or f"HTTP {r.status_code}")
        return data

    def cancel_flight(self, reason: str = "") -> dict:
        r = self._req("POST",
            f"{self.config.api_url}/flights/cancel",
            json={"reason": (reason or "")[:300]},
            headers=self._headers(),
            timeout=15,
        )
        return r.json()

    def get_preferences(self) -> dict:
        r = self._req("GET",
            f"{self.config.api_url}/users/preferences",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def set_preferences(self, share_live_position: bool) -> dict:
        r = self._req("PUT",
            f"{self.config.api_url}/users/preferences",
            json={"shareLivePosition": share_live_position},
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def submit_problem_report(self, payload: dict) -> dict:
        r = self._req("POST",
            f"{self.config.api_url}/users/problem-reports",
            json=payload,
            headers=self._headers(),
            timeout=20,
        )
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {}
        if r.status_code >= 400:
            err = str(data.get("error") or r.text or f"HTTP {r.status_code}")[:400]
            if r.status_code == 401 or "access token" in err.lower() or "token required" in err.lower():
                raise RuntimeError(
                    t_ui(
                        "Nicht angemeldet. Im MiniClient-Tray „Mit Discord anmelden“, dann erneut senden.",
                        "Not signed in. Use Discord login in the MiniClient tray, then send again.",
                    )
                )
            raise RuntimeError(err)
        return data

    def crew_presence(self, body: dict) -> dict:
        r = self._req("POST",
            f"{self.config.api_url}/crew/presence",
            json=body,
            headers=self._headers(),
            timeout=8,
        )
        if r.status_code >= 400:
            return {}
        return r.json() if r.content else {}


class AppState:
    """Shared state for GUI + bridge + monitor thread."""

    def __init__(self) -> None:
        self.config = ConfigManager()
        self.sim = SimReader()
        self.api = FlyConomyAPI(self.config)
        self.lock = threading.RLock()
        self.monitoring = False
        self.crew_guest = False
        self.last_sample: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.last_message: str = "Bereit"
        self.ui_status: str = ""
        self.active_flight_id: Optional[int] = None
        self.session_key: Optional[str] = None
        self.local_track: List[dict] = []
        self.track_seq: int = 0
        self.log_lines: list[str] = []
        self.pending_end: Optional[dict] = None
        self.pending_start: Optional[dict] = None
        self.cmd_queue: "queue.Queue[dict]" = queue.Queue(maxsize=32)
        self.economy_registration: Optional[str] = None
        self.has_gps: bool = True
        self.has_autopilot: bool = True
        self._equip_tick: int = 0
        self.sim_io_pause: bool = False
        self.from_icao: Optional[str] = None
        self.to_icao: Optional[str] = None
        self.flight_started_at: Optional[float] = None
        self.aircraft_label: str = ""
        self.registration: Optional[str] = None
        self.load_info: dict = {}
        self.hours_remaining: Optional[float] = None
        self.current_icao: Optional[str] = None
        self.current_airport_name: str = ""
        self._park_ticks: int = 0
        self._auto_ending: bool = False
        self._auto_end_cooldown_until: float = 0.0
        self._auto_end_popup_shown: bool = False
        self._icao_tick: int = 0
        self._hours_tick: int = 0
        self._save_tick: int = 0
        self._mf_tick: int = 0
        self._payload_reapply_until: float = 0.0
        self._payload_reapply_tick: int = 0
        self._aborting: bool = False
        self._session_miss_ticks: int = 0
        self._sim_gap_since: float = 0.0
        self._sim_gap_hinted: bool = False
        self._sim_gap_retry_at: float = 0.0
        self._resume_ref: Optional[dict] = None
        self._position_mismatch: bool = False
        self._paused_total_sec: float = 0.0
        self._pause_started_at: float = 0.0
        self._pause_logged: bool = False
        self._sim_paused: bool = False
        self._slew_strikes: int = 0
        self._teleport_strikes: int = 0
        self._recent_rates: list = []
        self._simrate_strikes: int = 0
        self._tamper_hits: int = 0
        self._tamper_reason: str = ""
        self._no_server_flight_hits: int = 0
        self._idle_reconnect_at: float = 0.0
        self._prefs_tick: int = 0
        self._last_pos: Optional[dict] = None  # live anti-cheat pos (not only sparse track)
        self._integrity_last: Optional[dict] = None
        self._last_good_fuel: float = 0.0
        self._last_good_weight: float = 0.0
        self._fuel_zero_streak: int = 0
        self._live_last: float = 0.0
        self._gsx_refuel: Optional[dict] = None
        self._refuel_grace_until: float = 0.0
        self._was_airborne: bool = False
        self._max_nm_from_origin: float = 0.0
        self.tk_root: Any = None
        self._log_ui_open: bool = False
        self._gsx_cached: bool = False
        self._custom_fuel_cached: bool = False
        self._myflight: dict = {}
        self.panel_version_disk: Optional[str] = None
        self.panel_version_live: Optional[str] = None
        self.panel_live_at: float = 0.0
        self._panel_disk_at: float = 0.0
        self._panel_latest: str = PANEL_FALLBACK_LATEST
        self._client_latest: str = CLIENT_FALLBACK_LATEST
        self._updater_push: str = UPDATER_PUSH_FALLBACK
        self._updater_url: str = f"{SITE_URL}/downloads/FlyConomyUpdater_Setup.exe"
        self._panel_latest_at: float = 0.0
        self._panel_download: str = f"{SITE_URL}/downloads/FlyConomy-MSFS-Panel.zip"
        self._client_download: str = f"{SITE_URL}/downloads/FlyConomyMiniClient_Setup.exe"
        # 2.11.13: version gate on clientMin/panelMin, update hint on clientLatest.
        self._client_min: str = CLIENT_FALLBACK_MIN
        self._panel_min: str = PANEL_FALLBACK_MIN
        self._versions_ok: bool = False
        self._versions_next_at: float = 0.0
        self._versions_busy: bool = False
        self._server_features: dict = {}
        self._flight_features: dict = {}
        self._download_hashes: dict = {}
        # Flight-scoped values from /start (persisted in the session file).
        self.max_sim_rate: float = MAX_SIM_RATE
        self.fuel_policy: dict = {}
        self._max_gs_seen: float = 0.0
        self._flight_title: str = ""
        self._title_mismatch_ticks: int = 0
        self._fuel_report_due: Optional[dict] = None
        # Gaps, jumps and the restore after a restart (D1/D8/D9).
        self._read_misses: int = 0
        self._gap_reason: str = ""
        self._gap_wall_since: float = 0.0
        self._restored_session: bool = False
        self._mismatch_kind: str = ""
        self._mismatch_ref: Optional[dict] = None
        self._mismatch_consistent: int = 0
        self._mismatch_prompted: bool = False
        self._mismatch_choice: str = ""
        self._mismatch_reported: bool = False
        self._recent_samples: List[dict] = []
        self._ground_glitch_logged: bool = False
        # Review S8: one odd sample after a gap is no proof – a second one must agree.
        self._gap_confirm: int = 0
        self._gap_fuel_confirm: int = 0
        self._mismatch_allowed_nm: float = 0.0
        self._title_change_logged: bool = False
        # Result of the last logged flight for panel / log window (D12).
        self.last_result: Optional[dict] = None
        # Sessions this process saw end (logged, closed or cancelled): session_key →
        # {"at", "flight_id", "result"}. Restore, the retry loop and the log window
        # never bring such a session back (review S8).
        self._ended_sessions: Dict[str, dict] = {}
        self._clock_hinted: bool = False
        self.restore_state: str = "idle"
        self._panel_origin_seen: Optional[str] = None
        self._panel_origin_rejected: Optional[dict] = None
        GROUND.start_def = self.config.hotkey_start
        GROUND.end_def = self.config.hotkey_end
        GROUND.volume = self.config.hotkey_volume
        toggle = (self.config.hotkey_start or self.config.hotkey_end).strip()
        if toggle and self.config.hotkey_start != self.config.hotkey_end:
            self.config.set_hotkey("start", toggle)
            GROUND.start_def = toggle
            GROUND.end_def = toggle

    def set_ui_status(self, de: str, en: str = "") -> None:
        self.ui_status = en if ui_locale() == "en" and en else de
        self.last_message = self.ui_status

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self.log_lines.append(line)
        self.log_lines = self.log_lines[-200:]
        print(line)
        self.last_message = msg

    def log_only(self, msg: str) -> None:
        """Log line that never becomes the status text of panel/app (bridge noise)."""
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self.log_lines.append(line)
        self.log_lines = self.log_lines[-200:]
        print(line)

    def _fuel_pump_fields(self) -> dict:
        job = self._gsx_refuel
        if not job:
            return {"fuel_pump": {"active": False, "pct": 0}}
        frm = job.get("pump_from")
        tgt = float(job.get("target_gal") or 0)
        sample = self.last_sample or {}
        cur = float(sample.get("fuel_total") or (frm if frm is not None else 0) or 0)
        if frm is None:
            frm = cur
        frm = float(frm)
        span = abs(tgt - frm) or 1.0
        pct = max(0.0, min(100.0, 100.0 * abs(cur - frm) / span))
        dur = float(job.get("duration_sec") or 0)
        t0 = float(job.get("pump_at") or job.get("started_at") or 0)
        if dur > 0 and t0 > 0:
            tpct = 100.0 * (time.time() - t0) / dur
            pct = max(pct, min(100.0, tpct))
        return {
            "fuel_pump": {
                "active": True,
                "pct": int(round(pct)),
                "pace": job.get("pace") or "",
                "target_gal": tgt,
            }
        }

    def snapshot(self) -> dict:
        # Never call SimConnect from the UI thread — that froze "Flug beenden".
        if self.sim.connected:
            self.sim.ping_process()
        # Version fields read disk + cached server values – never under STATE.lock
        # (the monitor thread needs that lock every second, D12).
        panel_fields = panel_snapshot_fields()
        pending_start = self.pending_start if isinstance(self.pending_start, dict) else None
        with self.lock:
            sample = dict(self.last_sample) if (self.sim.connected and self.last_sample) else None
            elapsed = flight_elapsed_now()
            title = str((sample or {}).get("aircraft_type") or "").lower()
            mf_ac = (self._myflight or {}).get("aircraft") if isinstance(self._myflight, dict) else None
            if mf_ac is not None and not isinstance(mf_ac, dict):
                mf_ac = None
            # Prefer the live flight registration while airborne — myflight can
            # briefly arrive without an aircraft and made the panel flash "kein AC".
            snap_reg = ""
            if self.active_flight_id:
                snap_reg = str(self.registration or "").strip().upper()
            if not snap_reg and mf_ac:
                snap_reg = str(mf_ac.get("registration") or "").strip().upper()
            snap_label = (
                self.aircraft_label
                if snap_reg
                else (str(mf_ac.get("registration") or "").strip().upper() if mf_ac else "")
            )
            if snap_reg and not snap_label:
                snap_label = snap_reg
            # start/end set sim_io_pause while they arm or close a flight – no flash
            unrecorded = bool(self.active_flight_id and not self.monitoring and not self.sim_io_pause)
            return {
                "ok": True,
                "logged_in": bool(self.api.token),
                "user": self.api.user,
                "access_token": self.api.token or "",
                "client_version": CLIENT_VERSION,
                "sim_connected": bool(self.sim.connected),
                "monitoring": self.monitoring,
                "tracking_paused": tracking_is_paused(),
                "sim_paused": bool(self._sim_paused),
                "tracking_pause_sec": tracking_pause_sec(),
                "position_mismatch": bool(self._position_mismatch),
                "active_flight_id": self.active_flight_id,
                "crew_guest": bool(self.crew_guest),
                "flight_started_at": self.flight_started_at,
                "flight_elapsed_sec": elapsed,
                "from_icao": self.from_icao,
                "current_icao": self.current_icao,
                "current_airport_name": self.current_airport_name,
                "sim_title": (sample or {}).get("aircraft_type") or "",
                "sim_atc_model": (sample or {}).get("atc_model") or "",
                "sim_atc_type": (sample or {}).get("atc_type") or "",
                "registration": snap_reg or "",
                "aircraft_label": snap_label,
                "has_gps": self.has_gps,
                "has_autopilot": self.has_autopilot,
                "load": dict(self.load_info),
                "hours_remaining": self.hours_remaining,
                "last_sample": sample,
                "last_error": self.last_error,
                "last_message": self.ui_status or self.last_message or "",
                "ui_status": self.ui_status or "",
                "gsx": bool(self._gsx_cached),
                "custom_fuel": bool(self._custom_fuel_cached),
                "fuel_mode": self.config.fuel_mode,
                "myflight": dict(self._myflight or {}),
                "addons": {
                    "gsx": bool(self._gsx_cached),
                    "fenix": "fenix" in title or "fnx" in title,
                    "fbw": "a32nx" in title or "flybywire" in title or "fly-by-wire" in title,
                    "pmdg": "pmdg" in title,
                    "custom_fuel": bool(self._custom_fuel_cached),
                },
                "api_url": self.config.api_url,
                "bridge": f"http://{BRIDGE_HOST}:{BRIDGE_PORT}",
                "share_live_position": False,
                "needs_onboarding": False,
                "track_points_local": len(self.local_track) if self.monitoring else 0,
                "mode": "end_of_flight",
                "pending_end": bool(self.pending_end),
                "pending_start": bool(pending_start),
                "pending_start_text": (
                    t_ui("Start wartet – NICHT losfliegen", "Start queued – do NOT take off")
                    if pending_start
                    else ""
                ),
                "mismatch_kind": self._mismatch_kind if self._position_mismatch else "",
                # Review S8: the server has a flight this client does not record
                # (lost /start answer, no session on this PC) – only "Cancel" helps.
                "flight_unrecorded": unrecorded,
                "flight_unrecorded_text": (
                    t_ui(
                        "Flug läuft auf dem Server ohne Aufzeichnung – nur „Abbrechen“ hilft, dann neu starten.",
                        "The server has a flight that is not being recorded – only “Cancel” helps, then start again.",
                    )
                    if unrecorded
                    else ""
                ),
                "title_mismatch": bool(self._title_mismatch_ticks >= TITLE_CHANGE_TICKS),
                "mismatch_choice_open": bool(
                    self._position_mismatch and self._mismatch_kind == "gap" and not self._mismatch_choice
                ),
                "max_sim_rate": self.max_sim_rate,
                "last_result": dict(self.last_result) if self.last_result else None,
                "clock_offset_sec": round(SERVER_CLOCK.raw_ms / 1000.0, 1) if SERVER_CLOCK.known else None,
                "sim_read_ms": dict(self.sim.read_stats),
                "restore_state": self.restore_state,
                "hotkeys": GROUND.snapshot(),
                **self._fuel_pump_fields(),
                **panel_fields,
            }


STATE = AppState()


def ensure_api_token() -> bool:
    """Attach the stored Discord JWT if memory lost it (restart mid-flight)."""
    if STATE.api.token:
        return True
    tok = (STATE.config.access_token or "").strip()
    if not tok:
        return False
    STATE.api.token = tok
    return True
DISCORD_PRESENCE = DiscordPresence()
DISCORD_PRESENCE.bind_logger(lambda m: STATE.log(m))
bind_logger(STATE.log)
bind_payware_logger(STATE.log)


def _aircraft_title_for_lamp_test() -> str:
    if STATE.sim.connected:
        t = STATE.sim.sim_title()
        if t:
            return t
    sample = STATE.last_sample or {}
    return str(sample.get("aircraft_type") or "")


def schedule_cockpit_ack(reason: str) -> None:
    """Hold lamp/annunciator test ~3s after a confirmed server start or log."""
    schedule_lamp_test(
        STATE.sim,
        _aircraft_title_for_lamp_test(),
        hold_sec=STATE.config.cockpit_lamp_test_seconds,
        reason=reason,
        enabled=STATE.config.cockpit_lamp_test_enabled,
    )


def version_tuple(raw: Optional[str]) -> tuple:
    parts = [int(x) for x in re.findall(r"\d+", str(raw or ""))]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _file_version(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, data):
            return None
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


def ensure_updater_install() -> None:
    """Push a fixed updater onto 1.0.2 boxes without using its broken self-update."""
    refresh_versions(blocking=True)
    if not STATE._versions_ok:
        return  # without fresh versions.json we would push blindly – wait
    latest = str(STATE._updater_push or UPDATER_PUSH_FALLBACK).strip()
    exe = Path(os.environ.get("LOCALAPPDATA") or "") / "FlyConomyUpdater" / "FlyConomyUpdater.exe"
    current = _file_version(exe) or "0"
    if version_tuple(current) >= version_tuple(latest):
        return
    url = str(STATE._updater_url or f"{SITE_URL}/downloads/FlyConomyUpdater_Setup.exe").strip()
    setup = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / "FlyConomyUpdater_Setup.exe"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    if len(r.content) < 10_000:
        raise RuntimeError("updater download too small")
    expected = str((STATE._download_hashes or {}).get(url.rsplit("/", 1)[-1]) or "").strip().lower()
    if expected and hashlib.sha256(r.content).hexdigest() != expected:
        raise RuntimeError("updater download: SHA-256 stimmt nicht mit versions.json überein")
    setup.write_bytes(r.content)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(setup), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/FORCECLOSEAPPLICATIONS"],
        close_fds=True,
        creationflags=flags | getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    STATE.log(f"Updater {current} -> {latest} wird im Hintergrund installiert.")


def updater_push_loop() -> None:
    time.sleep(12)
    while True:
        try:
            ensure_updater_install()
        except Exception as exc:
            STATE.log(f"Updater-Push: {exc}")
        time.sleep(30 * 60)


def _read_panel_manifest(folder: Path) -> Optional[str]:
    man = folder / "manifest.json"
    if not man.is_file():
        return None
    try:
        data = json.loads(man.read_text(encoding="utf-8"))
        ver = str(data.get("package_version") or "").strip()
        return ver or None
    except Exception:
        return None


def scan_installed_panel_version() -> Optional[str]:
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
    found: list[str] = []
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
        community = Path(m.group(1)) / "Community" / PANEL_PKG_NAME
        key = str(community).lower()
        if key in seen:
            continue
        seen.add(key)
        ver = _read_panel_manifest(community)
        if ver:
            found.append(ver)
    return max(found, key=version_tuple) if found else None


VERSIONS_URL = f"{SITE_URL}/api/live/versions.json"
VERSIONS_REFRESH_SEC = 600
VERSIONS_RETRY_SEC = 60  # after a failed fetch (D12: was 600)
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}$")


def _version_field(data: dict, *keys: str) -> str:
    for key in keys:
        raw = str((data or {}).get(key) or "").strip()
        if raw and _VERSION_RE.match(raw):
            return raw
    return ""


def apply_versions_payload(data: dict) -> None:
    """versions.json → state. New fields first, old fields as fallback.

    Old servers only send "client"/"panel"; for 2.11.12 those were both the gate
    and the newest version, so they stand in for clientMin/panelMin as well.
    """
    if not isinstance(data, dict):
        return
    panel_latest = _version_field(data, "panelLatest", "panel", "latest")
    client_latest = _version_field(data, "clientLatest", "client")
    client_min = _version_field(data, "clientMin", "client")
    panel_min = _version_field(data, "panelMin", "panel", "latest")
    if panel_latest:
        STATE._panel_latest = panel_latest
    if client_latest:
        STATE._client_latest = client_latest
    if client_min:
        STATE._client_min = client_min
    if panel_min:
        STATE._panel_min = panel_min
    dl = str(data.get("panelUrl") or data.get("url") or "").strip()
    cdl = str(data.get("clientUrl") or "").strip()
    if dl:
        STATE._panel_download = dl
    if cdl:
        STATE._client_download = cdl
    push = _version_field(data, "updaterCurrent", "updater")
    uurl = str(data.get("updaterUrl") or "").strip()
    if push:
        STATE._updater_push = push
    if uurl:
        STATE._updater_url = uurl
    hashes = data.get("hashes")
    if isinstance(hashes, dict):
        STATE._download_hashes = {str(k): str(v) for k, v in hashes.items() if isinstance(v, str)}
    feats = data.get("clientFeatures")
    if isinstance(feats, dict):
        STATE._server_features = dict(feats)


def _refresh_versions_worker() -> None:
    ok = False
    try:
        r = STATE.api._req("GET", VERSIONS_URL, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                apply_versions_payload(data)
                ok = True
    except Exception:
        ok = False
    finally:
        now = time.time()
        STATE._versions_ok = ok or STATE._versions_ok
        STATE._versions_next_at = now + (VERSIONS_REFRESH_SEC if ok else VERSIONS_RETRY_SEC)
        STATE._panel_latest_at = now
        STATE._versions_busy = False


def refresh_versions(blocking: bool = False) -> None:
    """Fetch versions.json at most every 10 min (1 min after an error).

    Not blocking by default: the panel polls /status every 1.2 s and the
    snapshot must never wait for the network.
    """
    if time.time() < STATE._versions_next_at or STATE._versions_busy:
        return
    STATE._versions_busy = True
    if blocking:
        _refresh_versions_worker()
        return
    try:
        threading.Thread(target=_refresh_versions_worker, daemon=True, name="fc-versions").start()
    except Exception:
        STATE._versions_busy = False


def refresh_panel_latest() -> str:
    """Compatibility wrapper (older call sites): background refresh, cached value."""
    refresh_versions()
    return STATE._panel_latest


def note_panel_version(ver: str) -> None:
    v = str(ver or "").strip()
    if not v:
        return
    STATE.panel_version_live = v
    STATE.panel_live_at = time.time()


def panel_status() -> dict:
    """Versions for panel, website and the start gate (D12).

    *_outdated  = below clientMin/panelMin → starting is blocked (old meaning kept,
                  the website and old panels read these two flags).
    *_update_available = below clientLatest/panel latest → only a hint.
    """
    now = time.time()
    if now - STATE._panel_disk_at > 20:
        STATE._panel_disk_at = now
        try:
            STATE.panel_version_disk = scan_installed_panel_version()
        except Exception:
            pass
    refresh_versions()
    latest = STATE._panel_latest
    live_ok = bool(STATE.panel_version_live) and (now - STATE.panel_live_at) < 90
    installed = bool(STATE.panel_version_disk) or live_ok
    current = STATE.panel_version_live if live_ok else STATE.panel_version_disk
    panel_min = STATE._panel_min
    panel_blocked = bool(installed and current and version_tuple(current) < version_tuple(panel_min))
    panel_update = bool(installed and current and version_tuple(current) < version_tuple(latest))
    client_blocked = version_tuple(CLIENT_VERSION) < version_tuple(STATE._client_min)
    client_update = version_tuple(CLIENT_VERSION) < version_tuple(STATE._client_latest)
    return {
        "panel_installed": installed,
        "panel_version": current,
        "panel_latest": latest,
        "panel_min": panel_min,
        "panel_outdated": panel_blocked,
        "panel_update_available": panel_update,
        "panel_download_url": STATE._panel_download,
        "client_version": CLIENT_VERSION,
        "client_latest": STATE._client_latest,
        "client_min": STATE._client_min,
        "client_outdated": client_blocked,
        "client_update_available": client_update,
        "client_download_url": STATE._client_download,
    }


def panel_snapshot_fields() -> dict:
    try:
        return panel_status()
    except Exception:
        return {
            "panel_installed": False,
            "panel_version": None,
            "panel_latest": PANEL_FALLBACK_LATEST,
            "panel_min": PANEL_FALLBACK_MIN,
            "panel_outdated": False,
            "panel_update_available": False,
            "panel_download_url": f"{SITE_URL}/downloads/FlyConomy-MSFS-Panel.zip",
            "client_version": CLIENT_VERSION,
            "client_latest": CLIENT_FALLBACK_LATEST,
            "client_min": CLIENT_FALLBACK_MIN,
            "client_outdated": False,
            "client_update_available": False,
            "client_download_url": f"{SITE_URL}/downloads/FlyConomyMiniClient_Setup.exe",
        }


def version_gate_message(info: dict) -> Optional[str]:
    """None when starting is allowed, else the German/English reason."""
    parts = []
    if info.get("client_outdated"):
        parts.append(
            t_ui(
                f"Client {CLIENT_VERSION} ist zu alt, mindestens {info.get('client_min')} ist nötig.",
                f"Client {CLIENT_VERSION} is too old, at least {info.get('client_min')} is required.",
            )
        )
    if info.get("panel_outdated"):
        parts.append(
            t_ui(
                f"MSFS-Panel {info.get('panel_version')} ist zu alt, mindestens {info.get('panel_min')} ist nötig.",
                f"MSFS panel {info.get('panel_version')} is too old, at least {info.get('panel_min')} is required.",
            )
        )
    if not parts:
        return None
    return " ".join(parts) + " " + t_ui(
        "Bitte zuerst unter Downloads aktualisieren.",
        "Please update via Downloads first.",
    )


def assert_start_allowed() -> None:
    """One gate for bridge, desktop UI, hotkey, gateway and queued starts (D12)."""
    info = panel_snapshot_fields()
    msg = version_gate_message(info)
    if msg:
        raise VersionGateError(msg, info)


FEATURE_DEFAULTS = {
    # Real pause detection via the Pause_EX1 event – off until test flights (D9).
    "pause_ex1": False,
    # Bridge accepts browsers only from the panel (coui://) and its own pages (D11).
    "bridge_origin_check": True,
    # Offer "back to the last position" after a restart/crash (D1).
    "reposition_offer": True,
    # Bridge answers only requests addressed to 127.0.0.1/localhost (DNS rebinding).
    "bridge_host_check": True,
    # Send the far position (suspect + recent_samples) while the "gap" question is
    # open. Off until the server ignores paused samples for its teleport check
    # (C6); until then such pings can make the server end the flight (review S8).
    "suspect_report": False,
    # Pause the recording while another aircraft (TITLE) is loaded. Off until the
    # beta confirmed it (livery changes); 2.11.12 had no such pause.
    "title_pause": False,
}


def feature_enabled(name: str) -> bool:
    """ini override (beta testers) → /start client_features → versions.json → default."""
    try:
        override = STATE.config.feature_override(name)
    except Exception:
        override = None
    if override is not None:
        return override
    for source in (getattr(STATE, "_flight_features", None), getattr(STATE, "_server_features", None)):
        if isinstance(source, dict) and name in source:
            return bool(source.get(name))
    return bool(FEATURE_DEFAULTS.get(name, False))


def note_clock_skew() -> None:
    """Hint once when the PC clock is a minute or more off (D5)."""
    if STATE._clock_hinted or not SERVER_CLOCK.known:
        return
    raw = SERVER_CLOCK.raw_ms
    if abs(raw) < ServerClock.HINT_MS:
        return
    STATE._clock_hinted = True
    mins = abs(raw) / 60000.0
    ahead = raw < 0  # server behind the PC → PC clock runs ahead
    de = (
        f"Die Uhr dieses PCs geht etwa {mins:.0f} min {'vor' if ahead else 'nach'}. "
        "FlyConomy gleicht das beim Aufzeichnen aus. Bitte trotzdem in Windows "
        "„Uhrzeit automatisch festlegen“ einschalten."
    )
    en = (
        f"This PC's clock is about {mins:.0f} min {'ahead' if ahead else 'behind'}. "
        "FlyConomy compensates while recording. Please still enable "
        "“Set time automatically” in Windows."
    )
    STATE.log(t_ui(de, en))
    notify_windows("FlyConomy – PC-Uhr", t_ui(de, en), code="FC-CLOCK", kind="info")


def notify_windows(title: str, message: str, code: str = "", kind: str = "error") -> None:
    """Foreground FlyConomy window (pretty notice). MessageBox only as fallback."""
    try:
        from client_notice import show_notice

        show_notice(title, message, code=code, kind=kind)
        return
    except Exception:
        pass

    def _show() -> None:
        try:
            user32 = ctypes.windll.user32
            flags = 0x00000040 | 0x00001000 | 0x00010000 | 0x00040000
            try:
                user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass
            user32.MessageBoxW(0, str(message), str(title), flags)
        except Exception:
            pass

    threading.Thread(target=_show, daemon=True).start()


def validation_route_icaos(result: Optional[dict]) -> tuple[str, str]:
    """Read route ICAOs from /flights/end (camelCase or snake_case) with STATE fallbacks."""
    result = result or {}
    validation = result.get("validation") or {}
    flight = result.get("flight") or {}
    from_icao = (
        validation.get("from_icao")
        or validation.get("fromIcao")
        or flight.get("from_icao")
        or flight.get("fromIcao")
        or STATE.from_icao
    )
    to_icao = (
        validation.get("to_icao")
        or validation.get("toIcao")
        or flight.get("to_icao")
        or flight.get("toIcao")
        or STATE.current_icao
    )
    return str(from_icao or "?").upper(), str(to_icao or "?").upper()


def _money_num(val: Any) -> Optional[float]:
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _usd(n: float, signed: bool = False) -> str:
    whole = int(round(n))
    txt = f"${abs(whole):,}".replace(",", ".")
    if signed:
        return ("+" if whole >= 0 else "−") + txt
    return ("−" if whole < 0 else "") + txt


def _undelivered_list(result: dict) -> List[dict]:
    raw = (result or {}).get("undelivered_jobs") or (result or {}).get("undelivered_assignments") or []
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for job in raw:
        if isinstance(job, dict):
            out.append(job)
        elif job is not None:
            out.append({"id": job})
    return out


def end_result_summary(result: dict) -> dict:
    """Net result and undelivered jobs (D12). Works with old and new servers:
    net_change / fuel_charged / undelivered_jobs come with C12, otherwise the
    paid amount (already net of airport fees, rent and Sky-Tab) is shown."""
    result = result or {}
    validation = result.get("validation") or {}
    from_icao, to_icao = validation_route_icaos(result)
    paid = _money_num(result.get("total_payment"))
    net = _money_num(result.get("net_change"))
    # Server stage C (fixer): pilot_pay / airport_fees_pilot / rental_pilot are the pilot's OWN
    # amounts. airport_fees / rental_charged are the totals, which the airline, the owner group
    # or the crew may have paid – with own amounts the lines add up to net_change.
    pilot_pay = _money_num(result.get("pilot_pay"))
    own = pilot_pay is not None
    deductions = []
    for key, own_key, de, en in (
        ("airport_fees", "airport_fees_pilot", "Flughafengebühren", "Airport fees"),
        ("rental_charged", "rental_pilot", "Miete", "Rent"),
        ("fuel_charged", None, "Treibstoff", "Fuel"),
        ("sky_tab_garnished", None, "Sky-Tab", "Sky tab"),
    ):
        n = _money_num(result.get(own_key if (own and own_key) else key))
        if n:
            deductions.append({"key": key, "label": t_ui(de, en), "amount": n})
    other = None
    if own and net is not None:
        rest = net - (pilot_pay - sum(abs(d["amount"]) for d in deductions))
        if abs(rest) >= 1:
            other = rest  # e.g. the FBO share of the airport fees for an FBO tenant
    undelivered = []
    for job in _undelivered_list(result)[:20]:
        undelivered.append(
            {
                "id": job.get("id") or job.get("assignment_id"),
                "to": str(job.get("to_icao") or job.get("toIcao") or job.get("destination") or "").upper(),
                "reason": str(job.get("reason_text") or job.get("reason") or "").strip()[:160],
            }
        )
    return {
        "flight_id": result.get("flight_id") or (result.get("flight") or {}).get("id"),
        "from": from_icao,
        "to": to_icao,
        "status": str(validation.get("status") or "OK"),
        "paid": paid,
        "gross": pilot_pay if own else _money_num(result.get("gross_payment")),
        "gross_total": _money_num(result.get("gross_payment")),
        "own_share": own,
        "other": other,
        "net": net,
        "deductions": deductions,
        "undelivered": undelivered,
        "delivered": result.get("delivered_assignments"),
        "already_logged": bool(result.get("already_logged")),
        "crew": bool(result.get("crew")),
        "message": str(result.get("message") or "")[:300],
        "at": int(time.time()),
    }


def end_result_lines(result: dict) -> List[str]:
    s = end_result_summary(result)
    lines = [f"{s['from']} → {s['to']}", t_ui(f"Status: {s['status']}", f"Status: {s['status']}")]
    if s["already_logged"]:
        lines.append(
            t_ui(
                "Der Flug war schon gebucht (die erste Antwort ging unterwegs verloren).",
                "The flight was already booked (the first answer got lost).",
            )
        )
    if s["gross"] is not None and (s["deductions"] or s["other"] is not None):
        if s["own_share"]:
            total = s["gross_total"]
            extra = ""
            if total is not None and int(round(total)) != int(round(s["gross"])):
                extra = t_ui(f" (Auftragslohn gesamt: {_usd(total)})", f" (total job pay: {_usd(total)})")
            lines.append(
                t_ui(f"Dein Lohnanteil: {_usd(s['gross'])}", f"Your pay share: {_usd(s['gross'])}") + extra
            )
        else:
            lines.append(t_ui(f"Auftragslohn: {_usd(s['gross'])}", f"Job pay: {_usd(s['gross'])}"))
        for d in s["deductions"]:
            lines.append(f"  {d['label']}: −{_usd(abs(d['amount']))}")
        if s["other"] is not None:
            lines.append(
                t_ui(f"  Sonstiges (z. B. FBO-Anteil): {_usd(s['other'], True)}", f"  Other (e.g. FBO share): {_usd(s['other'], True)}")
            )
    if s["net"] is not None:
        lines.append(
            t_ui(f"Netto auf deinem Konto: {_usd(s['net'], True)}", f"Net on your account: {_usd(s['net'], True)}")
        )
    elif s["paid"] is not None:
        lines.append(t_ui(f"Ausgezahlt (netto): {_usd(s['paid'])}", f"Paid out (net): {_usd(s['paid'])}"))
    if s["undelivered"]:
        lines.append(
            t_ui(
                f"Nicht geliefert: {len(s['undelivered'])} Auftrag/Aufträge",
                f"Not delivered: {len(s['undelivered'])} job(s)",
            )
        )
        for job in s["undelivered"][:5]:
            label = f"#{job['id']}" if job.get("id") is not None else "?"
            dest = f" → {job['to']}" if job.get("to") else ""
            why = f": {job['reason']}" if job.get("reason") else ""
            lines.append(f"  {label}{dest}{why}")
        if len(s["undelivered"]) > 5:
            lines.append(t_ui(f"  … und {len(s['undelivered']) - 5} weitere", f"  … and {len(s['undelivered']) - 5} more"))
    return lines


def remember_end_result(result: dict) -> None:
    try:
        STATE.last_result = end_result_summary(result)
    except Exception:
        STATE.last_result = None


def notify_flight_logged(result: dict) -> None:
    reg = STATE.economy_registration or STATE.registration or ""
    lines = [t_ui("Flug wurde geloggt.", "Flight logged.")]
    lines.extend(end_result_lines(result))
    if reg:
        lines.append(t_ui(f"Kennung: {reg}", f"Registration: {reg}"))
    lines.append("")
    lines.append(t_ui("OK schliesst dieses Fenster.", "OK closes this window."))
    notify_windows(
        t_ui("FlyConomy – Flug geloggt", "FlyConomy – Flight logged"),
        "\n".join(lines),
        code="",
        kind="ok",
    )


PARK_TICKS_TO_END = 2  # ~2s handbrake before auto-end (was 5 → felt like FSE lag)
# Instant relocate / World-Map jump (nm). Real 16× hitch catch-up can be 80–180 nm
# in one SimConnect tick after the sim stuttered — that is not a map click.
TELEPORT_ABORT_NM = 25.0
TELEPORT_HITCH_FLOOR_NM = 180.0
HITCH_CREDIT_SEC = 90.0

# Missing sim data never cancels a flight any more. Menu, pause, alt-tab, a
# SimConnect hiccup or even an MSFS crash only pause the recording; what decides
# is whether the aircraft is still where it was once data comes back.
SIM_GAP_HINT_SEC = 25.0
SIM_GAP_RECONNECT_SEC = 8.0
IDLE_RECONNECT_SEC = 12.0
SIM_GAP_ABANDON_SEC = 12 * 3600.0
RESUME_TOLERANCE_NM = 3.0
RESUME_SPEED_KT = 620.0  # distance a gap may explain (airliner + margin)
RESUME_FUEL_RISE_GAL = 2.0
# One bad SimConnect sample must never end a flight: every hard verdict needs
# a second, independent confirmation.
TELEPORT_CONFIRM_TICKS = 2
SIMRATE_CONFIRM_TICKS = 3
TAMPER_CONFIRM_HITS = 3
# D8/D9 (2.11.13): position alone never cancels a flight. A gap is assumed only
# after 3 failed reads; a stale link is rebuilt after 20 s without data.
SIM_MISS_GAP_TICKS = 3
SIM_GAP_FORCE_RECONNECT_SEC = 20.0
RESUME_SPEED_FACTOR = 1.35
MAX_PLAUSIBLE_GS_KT = 2000.0
JUMP_CONSISTENT_TICKS = 3  # plausible samples after a jump before recording goes on
MISMATCH_RETURN_NM = RESUME_TOLERANCE_NM
SUSPECT_SAMPLES_KEEP = 20
TITLE_CHANGE_TICKS = 3  # other aircraft / avatar mode: pause like a data gap
GAP_CONFIRM_TICKS = 2  # samples after a gap that must agree before a "gap" question / fuel verdict


def ui_locale() -> str:
    try:
        return STATE.config.locale
    except Exception:
        return "de"


def t_ui(de: str, en: str) -> str:
    return en if ui_locale() == "en" else de


def _flight_destination_icao() -> Optional[str]:
    if STATE.to_icao:
        return STATE.to_icao
    for job in STATE.load_info.get("assignments") or []:
        to = str(job.get("to_icao") or "").strip().upper()
        if to:
            return to
    return None


def discord_presence_snapshot() -> dict:
    """Build Rich Presence fields shown in Discord."""
    user = (
        (STATE.api.user or {}).get("name")
        or (STATE.api.user or {}).get("username")
        or "Pilot"
    )
    tagline = t_ui(
        "Flugsimulator Wirtschaftssimulation",
        "Flight simulator economy",
    )
    start_ts = int(STATE.flight_started_at or time.time())

    if STATE.monitoring and STATE.active_flight_id:
        origin = STATE.from_icao or "?"
        dest = _flight_destination_icao() or STATE.current_icao or "?"
        reg = STATE.registration or ""
        ac = STATE.aircraft_label.replace(reg, "").strip() if reg else STATE.aircraft_label
        route = f"{origin} → {dest}"
        sub = " · ".join(x for x in (reg, ac.strip(), user) if x)
        return {
            "details": route[:128],
            "state": sub[:128] or user,
            "large_image": "logo",
            "large_text": "FlyConomy.de",
            "start": start_ts,
            "buttons": [{"label": "flyconomy.de", "url": "https://flyconomy.de"}],
        }

    if STATE.sim.connected and STATE.current_icao:
        where = STATE.current_icao
        if STATE.current_airport_name:
            where = f"{STATE.current_icao} · {STATE.current_airport_name}"
        return {
            "details": "FlyConomy.de",
            "state": t_ui(f"{tagline} · {where}", f"{tagline} · {where}")[:128],
            "large_image": "logo",
            "large_text": "FlyConomy.de",
            "start": int(time.time()),
            "buttons": [{"label": "flyconomy.de", "url": "https://flyconomy.de"}],
        }

    if STATE.api.token:
        return {
            "details": "FlyConomy.de",
            "state": t_ui(f"{tagline} · {user}", f"{tagline} · {user}")[:128],
            "large_image": "logo",
            "large_text": "FlyConomy.de",
            "start": int(time.time()),
            "buttons": [{"label": "flyconomy.de", "url": "https://flyconomy.de"}],
        }

    return {
        "details": "FlyConomy.de",
        "state": tagline[:128],
        "large_image": "logo",
        "large_text": "FlyConomy.de",
        "start": int(time.time()),
    }


def _clear_flight_runtime() -> None:
    STATE.active_flight_id = None
    STATE.monitoring = False
    STATE.economy_registration = None
    STATE.registration = None
    STATE.has_gps = True
    STATE.has_autopilot = True
    STATE.from_icao = None
    STATE.to_icao = None
    STATE.flight_started_at = None
    STATE.aircraft_label = ""
    STATE.load_info = {}
    STATE._park_ticks = 0
    STATE._auto_ending = False
    STATE._auto_end_cooldown_until = 0.0
    STATE._auto_end_popup_shown = False
    STATE._aborting = False
    STATE._session_miss_ticks = 0
    STATE._sim_gap_since = 0.0
    STATE._sim_gap_hinted = False
    STATE._sim_gap_retry_at = 0.0
    STATE._resume_ref = None
    STATE._position_mismatch = False
    STATE._paused_total_sec = 0.0
    STATE._pause_started_at = 0.0
    STATE._pause_logged = False
    STATE._sim_paused = False
    STATE._slew_strikes = 0
    STATE._teleport_strikes = 0
    STATE._recent_rates = []
    STATE._simrate_strikes = 0
    STATE._tamper_hits = 0
    STATE._tamper_reason = ""
    STATE._no_server_flight_hits = 0
    STATE._last_pos = None
    STATE._integrity_last = None
    STATE._last_good_fuel = 0.0
    STATE._last_good_weight = 0.0
    STATE._fuel_zero_streak = 0
    STATE._live_last = 0.0
    STATE._gsx_refuel = None
    STATE._refuel_grace_until = 0.0
    STATE._was_airborne = False
    STATE._max_nm_from_origin = 0.0
    STATE.max_sim_rate = MAX_SIM_RATE
    STATE.fuel_policy = {}
    STATE._flight_features = {}
    STATE._max_gs_seen = 0.0
    STATE._flight_title = ""
    STATE._title_mismatch_ticks = 0
    STATE._fuel_report_due = None
    STATE._read_misses = 0
    STATE._gap_reason = ""
    STATE._gap_wall_since = 0.0
    STATE._restored_session = False
    STATE._gap_confirm = 0
    STATE._gap_fuel_confirm = 0
    STATE._title_change_logged = False
    _clear_mismatch()
    STATE.ui_status = ""
    with STATE.lock:
        STATE.local_track = []
        STATE.session_key = None
        STATE.track_seq = 0
    remove_file_and_bak(FLIGHT_SESSION_PATH)


def _clear_mismatch() -> None:
    STATE._position_mismatch = False
    STATE._mismatch_kind = ""
    STATE._mismatch_ref = None
    STATE._mismatch_consistent = 0
    STATE._mismatch_prompted = False
    STATE._mismatch_choice = ""
    STATE._mismatch_reported = False
    STATE._mismatch_allowed_nm = 0.0


def flight_elapsed_now() -> int:
    if not STATE.flight_started_at or not STATE.active_flight_id:
        return 0
    paused = float(STATE._paused_total_sec or 0)
    if STATE._pause_started_at:
        paused += max(0.0, time.time() - float(STATE._pause_started_at))
    return max(0, int(time.time() - float(STATE.flight_started_at) - paused))


def tracking_pause_sec() -> int:
    if STATE._pause_started_at:
        return max(0, int(time.time() - float(STATE._pause_started_at)))
    if STATE._sim_gap_since:
        return max(0, int(time.time() - float(STATE._sim_gap_since)))
    return 0


def tracking_is_paused() -> bool:
    return bool(
        STATE.monitoring
        and (
            STATE._pause_started_at
            or STATE._sim_gap_since
            or STATE._position_mismatch
            or STATE._sim_paused
        )
    )


def _push_presence() -> None:
    try:
        GATEWAY.maybe_presence(force=True)
    except Exception:
        pass


def begin_tracking_pause(reason: str = "") -> None:
    if not STATE.monitoring:
        return
    first = not STATE._pause_started_at
    if not STATE._pause_started_at:
        STATE._pause_started_at = time.time()
    if first:
        _push_presence()
    if reason == "sim" and not STATE._pause_logged:
        STATE._pause_logged = True
        STATE.set_ui_status("Pause", "Pause")
        STATE.log(
            t_ui(
                "Simulator pausiert – Track und Zeit stehen. Der Flug läuft weiter.",
                "Simulator paused – track and time are frozen. The flight stays open.",
            )
        )
        _push_presence()


def end_tracking_pause() -> None:
    was = bool(STATE._pause_started_at or STATE._sim_paused)
    if STATE._pause_started_at:
        STATE._paused_total_sec += max(0.0, time.time() - float(STATE._pause_started_at))
        STATE._pause_started_at = 0.0
    STATE._pause_logged = False
    STATE._sim_paused = False
    if was:
        _push_presence()


def local_end_block() -> Optional[tuple[str, str]]:
    """Refuse log locally so the Windows 'logged OK' window never opens."""
    try:
        fresh = STATE.sim.read() if STATE.sim.connected else None
        if fresh:
            STATE.last_sample = fresh
    except Exception:
        pass
    raw = STATE.last_sample or {}
    if bool(raw.get("is_slew")):
        return (
            "FC-SLEW-ACTIVE",
            t_ui(
                "Flug kann nicht geloggt werden: Versetzen-Modus ist aktiv.",
                "Flight cannot be logged: relocate/slew mode is active.",
            ),
        )
    if bool(raw.get("paused")) or STATE._sim_paused:
        return (
            "FC-PAUSED",
            t_ui(
                "Flug kann nicht geloggt werden: Simulator ist pausiert. Pause beenden, dann am Boden loggen.",
                "Flight cannot be logged: simulator is paused. Unpause, then log on the ground.",
            ),
        )
    on_gnd = bool(raw.get("on_ground"))
    on_sim = bool(raw.get("on_ground_sim"))
    try:
        gs = float(raw.get("ground_speed") or 0)
    except (TypeError, ValueError):
        gs = 0.0
    agl = _agl_from(raw.get("alt_agl"))
    parked = gs <= PARK_GS_KT_AIRLINER
    # Cruise / approach / hover: never. Gate with broken SIM_ON_GROUND (A220):
    # parked and AGL under ~80 ft (jet CG at the gate is 15–40).
    clearly_airborne = (agl is not None and agl > PARK_AGL_FT) or gs > GATE_LOADING_GS_KT
    if clearly_airborne:
        return (
            "FC-NOT-ON-GROUND",
            t_ui(
                "Flugende muss am Boden gemeldet werden. Bitte anhalten (Parkbremse) und erneut loggen – der Flug läuft weiter.",
                "End the flight on the ground. Stop (parking brake) and log again – the flight continues.",
            ),
        )
    if parked or on_gnd or on_sim:
        return None
    return (
        "FC-NOT-ON-GROUND",
        t_ui(
            "Flugende muss am Boden gemeldet werden. Bitte anhalten (Parkbremse) und erneut loggen – der Flug läuft weiter.",
            "End the flight on the ground. Stop (parking brake) and log again – the flight continues.",
        ),
    )


def local_end_block_reason() -> Optional[str]:
    hit = local_end_block()
    return hit[1] if hit else None


def _monitoring_active() -> bool:
    return bool(STATE.monitoring and STATE.session_key and STATE.active_flight_id)


def ping_paused_tracking(raw: dict, extra: Optional[dict] = None, *, force: bool = False) -> None:
    """Live ping with paused=True (no track point). The server still runs its own
    checks on it – that is where a suspicious position gets decided (D8)."""
    now_t = time.time()
    if not force and (now_t - float(STATE._live_last or 0)) < LIVE_POS_SEC:
        return
    if not STATE.session_key or not STATE.active_flight_id:
        return
    try:
        sample = make_sample(raw) if raw else None
    except Exception:
        sample = None
    if not sample:
        return
    STATE._live_last = now_t
    body = {
        "flight_id": STATE.active_flight_id,
        "session_key": STATE.session_key,
        "latitude": sample["latitude"],
        "longitude": sample["longitude"],
        "altitude": sample.get("altitude"),
        "ground_speed": sample.get("ground_speed"),
        "heading": sample.get("heading"),
        "fuel_total": sample.get("fuel_total"),
        "total_weight_lb": sample.get("total_weight_lb"),
        "on_ground": sample.get("on_ground"),
        "simulation_rate": sample.get("simulation_rate"),
        "paused": True,
    }
    if extra:
        body.update(extra)
    try:
        STATE.api.tracking(body)
    except ServerRejectedError as e:
        server_ended_flight(str(e))
    except Exception:
        pass


def note_recent_sample(raw: dict) -> None:
    """Last ~20 raw samples – sent to the server with a suspicion (D8)."""
    try:
        STATE._recent_samples.append(
            {
                "t": now_ms(),
                "lat": round(float(raw["latitude"]), 6),
                "lon": round(float(raw["longitude"]), 6),
                "alt": round(_finite(raw.get("altitude")), 1),
                "gs": round(_finite(raw.get("ground_speed")), 1),
                "on_ground": bool(raw.get("on_ground")),
                "rate": round(_finite(raw.get("simulation_rate"), 1.0), 2),
                "paused": bool(raw.get("paused")),
            }
        )
        del STATE._recent_samples[:-SUSPECT_SAMPLES_KEEP]
    except (KeyError, TypeError, ValueError):
        pass


def report_position_suspect(kind: str, raw: dict, extra: Optional[dict] = None) -> None:
    STATE._mismatch_reported = True
    payload = {
        "suspect": {"kind": kind, **(extra or {})},
        "recent_samples": list(STATE._recent_samples[-SUSPECT_SAMPLES_KEEP:]),
    }
    ping_paused_tracking(raw, payload, force=True)


def ping_mismatch_ref(*, force: bool = False) -> None:
    """Heartbeat (paused) at the last good point while the "gap" question is open.

    Keeps the flight alive on the server without sending the far position: the
    deploy-1 heartbeat counts teleport strikes on paused pings as well."""
    ref = STATE._mismatch_ref or {}
    try:
        lat = float(ref["latitude"])
        lon = float(ref["longitude"])
    except (KeyError, TypeError, ValueError):
        return
    now_t = time.time()
    if not force and (now_t - float(STATE._live_last or 0)) < LIVE_POS_SEC:
        return
    if not STATE.session_key or not STATE.active_flight_id:
        return
    STATE._live_last = now_t
    fuel = _finite(ref.get("fuel_total"))
    weight = _finite(ref.get("total_weight_lb"))
    body = {
        "flight_id": STATE.active_flight_id,
        "session_key": STATE.session_key,
        "latitude": lat,
        "longitude": lon,
        "altitude": _finite(ref.get("altitude")),
        "ground_speed": max(0.0, _finite(ref.get("ground_speed"))),
        "heading": _finite(ref.get("heading")),
        "fuel_total": fuel if fuel > 0 else None,
        "total_weight_lb": weight if weight > 0 else None,
        "on_ground": bool(ref.get("on_ground")),
        "simulation_rate": max(1.0, _finite(ref.get("simulation_rate"), 1.0)),
        "paused": True,
        "position_wait": True,
    }
    try:
        STATE.api.tracking(body)
    except ServerRejectedError as e:
        server_ended_flight(str(e))
    except Exception:
        pass


def server_ended_flight(reason: str) -> None:
    """The server closed the flight (its verdict). Never relabel it as a local abort."""
    if not STATE.monitoring and not STATE.active_flight_id:
        return
    reason = str(reason or "").strip() or t_ui("ohne Angabe", "no reason given")
    msg = t_ui(f"Der Server hat den Flug beendet: {reason}", f"The server ended the flight: {reason}")
    _clear_flight_runtime()
    STATE.last_error = msg
    STATE.log(msg)
    notify_windows(
        t_ui("FlyConomy – Flug beendet", "FlyConomy – Flight ended"),
        msg,
        code="FC-SERVER-ENDED",
        kind="abort",
    )


def sample_plausible(sample: dict) -> bool:
    """Reject junk coordinates: SimConnect yields 0/0 while loading or in menus."""
    try:
        lat = float(sample.get("latitude"))
        lon = float(sample.get("longitude"))
    except (TypeError, ValueError):
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    return not (abs(lat) < 0.01 and abs(lon) < 0.01)


_GAP_RANK = {"": 0, "data": 1, "title": 2, "disconnected": 3, "process": 4, "restore": 5}


def note_sim_gap(reason: str = "data") -> None:
    """No usable sim data: pause the recording, reconnect, keep the flight."""
    now = time.time()
    if not STATE._sim_gap_since:
        STATE._sim_gap_since = now
        STATE._sim_gap_retry_at = now + 4.0
    if not STATE._gap_wall_since:
        STATE._gap_wall_since = now
    if _GAP_RANK.get(reason, 1) > _GAP_RANK.get(STATE._gap_reason, 0):
        STATE._gap_reason = reason
    begin_tracking_pause("gap")
    if not STATE._sim_gap_hinted and (now - STATE._gap_wall_since) >= SIM_GAP_HINT_SEC:
        STATE._sim_gap_hinted = True
        STATE.log(
            t_ui(
                "Keine Sim-Daten (Menü, Pause oder Neustart) – Aufzeichnung pausiert. "
                "Der Flug bleibt offen und läuft weiter, sobald der Sim wieder Daten liefert.",
                "No sim data (menu, pause or restart) – recording paused. The flight stays "
                "open and continues as soon as the sim reports data again.",
            )
        )
    if now >= STATE._sim_gap_retry_at:
        STATE._sim_gap_retry_at = now + SIM_GAP_RECONNECT_SEC
        if _msfs_process_running():
            # Connected but silent for 20 s: the link is stale, build it anew (D9).
            stale = (
                STATE.sim.connected
                and reason == "data"
                and (now - STATE._gap_wall_since) >= SIM_GAP_FORCE_RECONNECT_SEC
            )
            if not STATE.sim.connected or stale:
                try:
                    STATE.sim.connect()
                except Exception:
                    pass
    if now - STATE._gap_wall_since >= SIM_GAP_ABANDON_SEC:
        abort_active_flight("abandoned")


def _clamped_sim_rate(*values: object) -> float:
    """Highest plausible rate from the given samples, never above the 16× cap."""
    best = 1.0
    for v in values:
        n = _finite(v, 0.0)
        if n > 0.05:
            best = max(best, min(MAX_SIM_RATE, n))
    return best


def note_sim_rate(rate: object) -> None:
    n = _finite(rate, 0.0)
    if n <= 0.05:
        return
    buf = STATE._recent_rates
    buf.append(min(MAX_SIM_RATE, n))
    if len(buf) > 48:
        del buf[: len(buf) - 48]


def recent_sim_rate() -> float:
    buf = STATE._recent_rates or []
    return max(buf) if buf else 1.0


def allowed_move_nm(dt_sec: float, *rates: object, gs_kt: float = 0.0) -> float:
    """Legal ground during pause, hitch or one SimConnect catch-up tick."""
    rate = _clamped_sim_rate(*rates, recent_sim_rate())
    # Once the pilot is in time compression, a 1.00 glitch sample must not
    # shrink the window. 16× for 20 minutes then a hitch is still legal.
    if rate >= 2.0:
        rate = MAX_SIM_RATE
    dt = max(0.5, float(dt_sec) if dt_sec else 0.5)
    credit = max(dt, HITCH_CREDIT_SEC if rate >= 4.0 else 1.0)
    gs = max(420.0, _finite(gs_kt, 0.0) * 1.35)
    raw = rate * gs * credit / 3600.0 + 25.0
    floor = TELEPORT_HITCH_FLOOR_NM if rate >= 4.0 else TELEPORT_ABORT_NM
    return max(floor, raw)


def note_max_gs(gs: object) -> None:
    n = _finite(gs, 0.0)
    if 0.0 < n <= MAX_PLAUSIBLE_GS_KT and n > float(STATE._max_gs_seen or 0.0):
        STATE._max_gs_seen = n


def resume_speed_kt() -> float:
    """Speed a gap may explain – from the aircraft, not one number for all (D8).

    Flight 1308 (Concorde, ~1,175 kt) was measured against 620 kt × 1.35."""
    best = RESUME_SPEED_KT
    seen = _finite(STATE._max_gs_seen, 0.0)
    if 0.0 < seen <= MAX_PLAUSIBLE_GS_KT:
        best = max(best, seen * RESUME_SPEED_FACTOR)
    load = STATE.load_info or {}
    cruise = _finite(load.get("cruise_speed") or load.get("cruise_kt"), 0.0)
    if 0.0 < cruise <= MAX_PLAUSIBLE_GS_KT:
        best = max(best, cruise * RESUME_SPEED_FACTOR)
    return best


def allowed_resume_nm(dt_sec: float, *rates: object, speed_kt: Optional[float] = None) -> float:
    """How far the aircraft may have flown during a pause or SimConnect gap."""
    speed = float(speed_kt) if speed_kt else resume_speed_kt()
    return max(RESUME_TOLERANCE_NM, allowed_move_nm(dt_sec, *rates, gs_kt=speed))


def _pos_ref(sample: dict, t: Optional[int] = None) -> dict:
    return {
        "latitude": float(sample["latitude"]),
        "longitude": float(sample["longitude"]),
        "altitude": _finite(sample.get("altitude")),
        "heading": _finite(sample.get("heading")),
        "ground_speed": _finite(sample.get("ground_speed")),
        "on_ground": bool(sample.get("on_ground")),
        "fuel_total": _finite(sample.get("fuel_total")),
        "total_weight_lb": _finite(sample.get("total_weight_lb") or sample.get("total_weight")),
        "simulation_rate": _finite(sample.get("simulation_rate"), 1.0),
        "t": int(t if t is not None else now_ms()),
    }


def _close_sim_gap() -> None:
    STATE._sim_gap_since = 0.0
    STATE._sim_gap_hinted = False
    STATE._sim_gap_retry_at = 0.0
    STATE._gap_wall_since = 0.0
    STATE._gap_reason = ""
    STATE._restored_session = False
    STATE._gap_confirm = 0
    STATE._gap_fuel_confirm = 0


def resume_after_gap(sample: dict) -> None:
    """Sim data is back – continue when the aircraft simply stayed where it was.

    A gap can never be used to skip ahead, but since 2.11.13 the position alone
    never cancels the flight either (Q32): the recording stays paused, the pilot
    is asked, and the server gets the samples to decide.
    """
    gap = time.time() - STATE._sim_gap_since if STATE._sim_gap_since else 0.0
    reason = STATE._gap_reason or "data"
    restored = bool(STATE._restored_session)
    ref = STATE._resume_ref or {}
    if gap < 5.0 or not ref:
        _close_sim_gap()
        end_tracking_pause()
        return
    try:
        moved = nm_between(
            float(ref["latitude"]),
            float(ref["longitude"]),
            float(sample["latitude"]),
            float(sample["longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        moved = 0.0
    # Time compression covers real ground: at 16× a 30 s gap is 8 minutes of flying.
    allowed = allowed_resume_nm(gap, ref.get("simulation_rate"), sample.get("simulation_rate"))
    mins = gap / 60.0
    if moved > allowed:
        # Review S8: one stray SimConnect coordinate right after a gap or restore
        # must not park the flight in a question – the next sample has to agree.
        STATE._gap_fuel_confirm = 0
        STATE._gap_confirm += 1
        if STATE._gap_confirm < GAP_CONFIRM_TICKS:
            return  # the gap stays open, nothing is recorded from this sample
        _close_sim_gap()
        enter_position_mismatch(
            "gap",
            sample,
            ref,
            moved_nm=round(moved, 1),
            allowed_nm=round(allowed, 1),
            gap_sec=int(gap),
            gap_reason="restore" if restored else reason,
        )
        return
    STATE._gap_confirm = 0
    ref_fuel = _finite(ref.get("fuel_total"))
    fuel = _finite(sample.get("fuel_total"))
    fuel_rose = (
        ref_fuel > 1.0
        and fuel > ref_fuel + RESUME_FUEL_RISE_GAL
        and not bool(sample.get("on_ground"))
        and not sim_fuel_unreliable()
    )
    if fuel_rose and reason == "data" and not restored:
        # Same sim session, only the data stream was gone: fuel cannot grow. A hard
        # verdict needs a second sample (review S8) – one glitch reading is not enough.
        STATE._gap_fuel_confirm += 1
        if STATE._gap_fuel_confirm < GAP_CONFIRM_TICKS:
            return
        _close_sim_gap()
        abort_active_flight("fuel_tamper")
        return
    _close_sim_gap()
    if fuel_rose:
        # Client or MSFS restarted: the reloaded aircraft has its own fuel. Report it.
        report_position_suspect(
            "fuel_after_restart",
            sample,
            {"fuel_before": round(ref_fuel, 1), "fuel_now": round(fuel, 1), "gap_sec": int(gap)},
        )
    _clear_mismatch()
    now_t = time.time()
    STATE._integrity_last = {
        "fuel": _finite(sample.get("fuel_total")),
        "weight": _finite(sample.get("total_weight_lb") or sample.get("total_weight")),
        "t": now_t,
    }
    STATE._last_pos = {
        "latitude": float(sample["latitude"]),
        "longitude": float(sample["longitude"]),
        "simulation_rate": _finite(sample.get("simulation_rate"), 1.0),
        "t": now_ms(),
    }
    STATE._teleport_strikes = 0
    STATE._simrate_strikes = 0
    STATE._tamper_hits = 0
    end_tracking_pause()
    STATE.log(
        t_ui(
            f"Sim-Daten sind zurück – Flug läuft weiter (Pause {mins:.0f} min).",
            f"Sim data is back – flight continues (paused for {mins:.0f} min).",
        )
    )


def enter_position_mismatch(kind: str, raw: dict, ref: Optional[dict], **extra: Any) -> None:
    """Pause the recording instead of cancelling (D8, Q32).

    kind "gap":  after a restart/crash the aircraft is somewhere else – ask the pilot.
    kind "jump": a jump while data kept coming – report it, go on once the new
                 position is steady; the server decides about the flight.
    """
    first = not STATE._position_mismatch
    STATE._position_mismatch = True
    STATE._mismatch_kind = kind
    if ref:
        STATE._mismatch_ref = dict(ref)
    STATE._mismatch_allowed_nm = _finite(extra.get("allowed_nm"))
    STATE._mismatch_consistent = 0
    STATE._mismatch_choice = ""
    begin_tracking_pause("mismatch")
    try:
        STATE._last_pos = {
            "latitude": float(raw["latitude"]),
            "longitude": float(raw["longitude"]),
            "simulation_rate": _finite(raw.get("simulation_rate"), 1.0),
            "t": now_ms(),
        }
    except (KeyError, TypeError, ValueError):
        pass
    if not first:
        return
    moved = _finite(extra.get("moved_nm"))
    if kind == "gap":
        STATE.log(
            t_ui(
                f"Nach der Unterbrechung steht das Flugzeug {moved:.0f} nm vom letzten Punkt entfernt. "
                "Der Flug ist NICHT abgebrochen – die Aufzeichnung wartet.",
                f"After the interruption the aircraft is {moved:.0f} nm from the last point. "
                "The flight is NOT cancelled – recording is waiting.",
            )
        )
    else:
        STATE.log(
            t_ui(
                f"Ungewöhnlicher Positionssprung ({moved:.0f} nm) – Aufzeichnung kurz pausiert, "
                "der Server prüft. Der Flug läuft weiter.",
                f"Unusual position jump ({moved:.0f} nm) – recording paused briefly, "
                "the server checks it. The flight continues.",
            )
        )
    if kind == "gap" and not feature_enabled("suspect_report"):
        # Review S8: the server checks every ping (paused ones too) and far
        # positions can make it end the flight – before the pilot can answer.
        # While the question is open only the last good point goes out; the jump is
        # reported once the pilot picks "fly on from here".
        ping_mismatch_ref(force=True)
    else:
        report_position_suspect(kind, raw, {k: v for k, v in extra.items() if v is not None})
    if kind == "gap" and STATE._position_mismatch:
        prompt_position_choice()
    _push_presence()


def _resolve_mismatch(raw: dict, message: str) -> None:
    _clear_mismatch()
    try:
        STATE._last_pos = {
            "latitude": float(raw["latitude"]),
            "longitude": float(raw["longitude"]),
            "simulation_rate": _finite(raw.get("simulation_rate"), 1.0),
            "t": now_ms(),
        }
        STATE._resume_ref = _pos_ref(raw)
    except (KeyError, TypeError, ValueError):
        pass
    STATE._integrity_last = None
    STATE._teleport_strikes = 0
    end_tracking_pause()
    STATE.log(message)
    _push_presence()


def prompt_position_choice() -> None:
    """Ask once: back to the last point, fly on from here, or cancel (D1.3)."""
    if STATE._mismatch_prompted:
        return
    STATE._mismatch_prompted = True
    ref = STATE._mismatch_ref or {}
    can_reposition = feature_enabled("reposition_offer") and bool(ref)
    if ref.get("on_ground"):
        where_de, where_en = "am Boden", "on the ground"
    else:
        alt = _finite(ref.get("altitude"))
        where_de, where_en = f"in {alt:,.0f} ft".replace(",", "."), f"at {alt:,.0f} ft"
    msg = t_ui(
        f"Das Flugzeug steht nicht dort, wo die Aufzeichnung aufgehört hat (letzter Punkt {where_de}). "
        "Der Flug ist NICHT abgebrochen – die Aufzeichnung wartet.\n\n"
        + ("„Zur letzten Position“ setzt das Flugzeug dorthin zurück, danach geht es weiter.\n" if can_reposition else "")
        + "„Hier weiterfliegen“ zeichnet ab hier weiter – der Server prüft dann den Sprung.\n"
        "„Flug abbrechen“ beendet den Flug ohne Bezahlung.",
        f"The aircraft is not where recording stopped (last point {where_en}). "
        "The flight is NOT cancelled – recording is waiting.\n\n"
        + ("“Back to last position” puts the aircraft there, then the flight goes on.\n" if can_reposition else "")
        + "“Fly on from here” records from here – the server then checks the jump.\n"
        "“Cancel flight” ends the flight without pay.",
    )
    choices = []
    if can_reposition:
        choices.append(("reposition", t_ui("Zur letzten Position", "Back to last position"), "primary"))
    choices.append(("continue", t_ui("Hier weiterfliegen", "Fly on from here"), "secondary"))
    choices.append(("cancel", t_ui("Flug abbrechen", "Cancel flight"), "danger"))

    def on_choice(key: str) -> None:
        STATE._mismatch_choice = str(key or "")

    try:
        from client_notice import ask_choice

        ask_choice(
            t_ui("FlyConomy – Flug fortsetzen?", "FlyConomy – Continue the flight?"),
            msg,
            choices,
            on_choice,
            code="FC-POSITION",
        )
    except Exception:
        notify_windows(
            t_ui("FlyConomy – Flug fortsetzen?", "FlyConomy – Continue the flight?"),
            msg,
            code="FC-POSITION",
            kind="info",
        )


def apply_mismatch_choice(choice: str, raw: dict) -> bool:
    """Returns True while the recording stays paused."""
    choice = str(choice or "").strip().lower()
    if choice == "reposition":
        ref = STATE._mismatch_ref
        ok = bool(ref) and STATE.sim.reposition(ref)
        if ok:
            STATE.log(
                t_ui(
                    "Flugzeug wird an die letzte Position gesetzt – die Aufzeichnung läuft weiter, "
                    "sobald es dort ist.",
                    "Moving the aircraft to the last position – recording resumes once it is there.",
                )
            )
        else:
            STATE.log(
                t_ui(
                    "Zurücksetzen hat nicht geklappt. Bitte erneut wählen oder hier weiterfliegen.",
                    "Moving back failed. Please choose again or fly on from here.",
                )
            )
            STATE._mismatch_prompted = False
        return True
    if choice == "continue":
        report_position_suspect("gap_continue_here", raw, {"choice": "continue"})
        if _monitoring_active():
            _resolve_mismatch(
                raw,
                t_ui(
                    "Weiterflug ab hier – der Sprung wurde an den Server gemeldet, er prüft den Flug.",
                    "Flying on from here – the jump was reported, the server checks the flight.",
                ),
            )
        return False
    if choice == "cancel":
        try:
            action_cancel()
        except Exception as e:
            STATE.log(f"Abbruch: {e}")
        return False
    return True


def position_mismatch_tick(raw: dict) -> bool:
    """While the position does not fit. Returns True when the recording stays paused."""
    if not STATE._position_mismatch:
        return False
    if STATE._mismatch_choice:
        choice = STATE._mismatch_choice
        STATE._mismatch_choice = ""
        if not apply_mismatch_choice(choice, raw):
            return False
    try:
        lat = float(raw["latitude"])
        lon = float(raw["longitude"])
    except (KeyError, TypeError, ValueError):
        return True
    if STATE._mismatch_kind == "gap":
        ref = STATE._mismatch_ref or {}
        try:
            dist = nm_between(float(ref["latitude"]), float(ref["longitude"]), lat, lon)
        except (KeyError, TypeError, ValueError):
            dist = float("inf")
        # Back at the last point (reposition) – or, after a stray sample, within the
        # distance the gap itself allowed (review S8): go on without an answer.
        if dist <= max(MISMATCH_RETURN_NM, _finite(STATE._mismatch_allowed_nm)):
            _resolve_mismatch(
                raw,
                t_ui(
                    "Flugzeug ist wieder an der letzten Position – Aufzeichnung läuft weiter.",
                    "Aircraft is back at the last position – recording continues.",
                ),
            )
            return False
        if feature_enabled("suspect_report"):
            ping_paused_tracking(raw)
        else:
            ping_mismatch_ref()
        return True
    prev = STATE._last_pos or {}
    tnow = now_ms()
    try:
        dt = max(0.5, (tnow - int(prev.get("t") or tnow)) / 1000.0)
        move = nm_between(float(prev["latitude"]), float(prev["longitude"]), lat, lon)
    except (KeyError, TypeError, ValueError):
        dt, move = 1.0, 0.0
    allowed = allowed_move_nm(
        dt,
        raw.get("simulation_rate"),
        prev.get("simulation_rate"),
        gs_kt=_finite(raw.get("ground_speed"), 0.0),
    )
    STATE._mismatch_consistent = STATE._mismatch_consistent + 1 if move <= allowed else 0
    STATE._last_pos = {
        "latitude": lat,
        "longitude": lon,
        "simulation_rate": _finite(raw.get("simulation_rate"), 1.0),
        "t": tnow,
    }
    if STATE._mismatch_consistent >= JUMP_CONSISTENT_TICKS:
        _resolve_mismatch(
            raw,
            t_ui(
                "Positionssprung an den Server gemeldet – Aufzeichnung läuft weiter. "
                "Der Server prüft den Flug.",
                "Position jump reported – recording continues. The server checks the flight.",
            ),
        )
        return False
    ping_paused_tracking(raw)
    return True


def abort_active_flight(reason: str) -> None:
    """Cancel economy flight + Windows popup (language = FlyConomy UI locale).

    Since 2.11.13 only slew, simrate, fuel/weight edits and 12 h without any sim
    data end a flight locally – never the position alone (Q32).
    """
    if STATE._aborting or STATE._auto_ending:
        return
    if not STATE.monitoring and not STATE.active_flight_id:
        return
    STATE._aborting = True
    messages = {
        "simrate": (
            "Flug abgebrochen: Simrate über der erlaubten Grenze.",
            "Flight cancelled: Simulation rate above the allowed limit.",
        ),
        "slew": (
            "Flug abgebrochen: Versetzen-Modus ist während eines Fluges nicht erlaubt.",
            "Flight cancelled: Relocate/slew mode is not allowed during a flight.",
        ),
        "abandoned": (
            "Flug abgebrochen: Über 12 Stunden keine Sim-Daten mehr.",
            "Flight cancelled: No sim data for more than 12 hours.",
        ),
        "fuel_tamper": (
            "Flug abgebrochen: Treibstoff wurde in den Flugeinstellungen manuell geändert.",
            "Flight cancelled: Fuel was changed manually in the flight settings.",
        ),
        "weight_tamper": (
            "Flug abgebrochen: Flugzeuggewicht wurde in den Flugeinstellungen manuell geändert.",
            "Flight cancelled: Aircraft weight was changed manually in the flight settings.",
        ),
    }
    de, en = messages.get(
        reason,
        (
            "Flug abgebrochen.",
            "Flight cancelled.",
        ),
    )
    msg = t_ui(de, en)
    title = t_ui("FlyConomy – Flug abgebrochen", "FlyConomy – Flight cancelled")
    try:
        STATE.api.cancel_flight(reason)
    except Exception as e:
        STATE.log(f"Cancel API fehlgeschlagen: {e}")
    _clear_flight_runtime()
    STATE.log(msg)
    notify_windows(title, msg, code=f"FC-{str(reason or 'ABORT').upper().replace('_', '-')}", kind="abort")


# ---------------------------------------------------------------------------
# Flight session on disk (D1/D3): written atomically, restored after a restart.
# ---------------------------------------------------------------------------

SESSION_FORMAT = 2
_session_save_warned = False


def _session_payload() -> dict:
    with STATE.lock:
        return {
            "format": SESSION_FORMAT,
            "flight_id": STATE.active_flight_id,
            "session_key": STATE.session_key,
            "track": list(STATE.local_track),
            "track_seq": STATE.track_seq,
            "from_icao": STATE.from_icao,
            "to_icao": STATE.to_icao,
            "started_at": STATE.flight_started_at,
            "registration": STATE.registration,
            "aircraft_label": STATE.aircraft_label,
            "load": STATE.load_info,
            "has_gps": STATE.has_gps,
            "has_autopilot": STATE.has_autopilot,
            "was_airborne": STATE._was_airborne,
            "max_nm_from_origin": STATE._max_nm_from_origin,
            "paused_total_sec": STATE._paused_total_sec,
            "max_sim_rate": STATE.max_sim_rate,
            "fuel_policy": STATE.fuel_policy,
            "client_features": STATE._flight_features,
            "max_gs_kt": STATE._max_gs_seen,
            "flight_title": STATE._flight_title,
            "saved_at": time.time(),
            "saved_at_server_ms": now_ms(),
            "client_version": CLIENT_VERSION,
        }


def save_flight_session() -> None:
    global _session_save_warned
    if not STATE.session_key or not STATE.active_flight_id:
        return
    try:
        atomic_write_json(FLIGHT_SESSION_PATH, _session_payload())
        _session_save_warned = False
    except Exception as e:
        if not _session_save_warned:
            _session_save_warned = True
            STATE.log(f"WARNUNG: Flug-Sitzung konnte nicht gespeichert werden: {e}")


def read_flight_session_file() -> Optional[dict]:
    data = read_json_file(FLIGHT_SESSION_PATH)
    if not isinstance(data, dict) or not data.get("session_key") or not data.get("flight_id"):
        return None
    return data


def session_file_present() -> bool:
    return read_flight_session_file() is not None


def apply_flight_session(data: dict, *, restored: bool = True) -> None:
    """Put a stored session into STATE (only after its flight id was checked)."""
    STATE.active_flight_id = data.get("flight_id")
    with STATE.lock:
        STATE.session_key = data.get("session_key")
        STATE.local_track = list(data.get("track") or [])
        STATE.track_seq = int(data.get("track_seq") or len(STATE.local_track))
    STATE.from_icao = data.get("from_icao")
    STATE.to_icao = data.get("to_icao")
    STATE.flight_started_at = data.get("started_at")
    STATE.registration = data.get("registration")
    STATE.economy_registration = STATE.registration
    STATE.aircraft_label = data.get("aircraft_label") or ""
    STATE.load_info = data.get("load") or {}
    STATE.has_gps = True if "has_gps" not in data else bool(data.get("has_gps"))
    STATE.has_autopilot = True if "has_autopilot" not in data else bool(data.get("has_autopilot"))
    STATE._was_airborne = bool(data.get("was_airborne"))
    STATE._max_nm_from_origin = float(data.get("max_nm_from_origin") or 0)
    STATE._paused_total_sec = float(data.get("paused_total_sec") or 0)
    msr = _finite(data.get("max_sim_rate"), 0.0)
    STATE.max_sim_rate = msr if msr > 0 else MAX_SIM_RATE
    STATE.fuel_policy = data.get("fuel_policy") if isinstance(data.get("fuel_policy"), dict) else {}
    STATE._flight_features = (
        data.get("client_features") if isinstance(data.get("client_features"), dict) else {}
    )
    STATE._max_gs_seen = _finite(data.get("max_gs_kt"), 0.0)
    STATE._flight_title = str(data.get("flight_title") or "")
    origin = STATE.local_track[0] if STATE.local_track else None
    for p in STATE.local_track:
        note_max_gs(p.get("ground_speed"))
        if origin:
            try:
                nm = nm_between(
                    float(origin["latitude"]),
                    float(origin["longitude"]),
                    float(p["latitude"]),
                    float(p["longitude"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if nm > STATE._max_nm_from_origin:
                STATE._max_nm_from_origin = nm
        if float(p.get("ground_speed") or 0) >= AIRBORNE_GS_KT:
            STATE._was_airborne = True
    STATE._integrity_last = None
    STATE._last_good_fuel = 0.0
    STATE._last_good_weight = 0.0
    STATE._fuel_zero_streak = 0
    STATE._gap_confirm = 0
    STATE._gap_fuel_confirm = 0
    _clear_mismatch()
    last_pt = STATE.local_track[-1] if STATE.local_track else None
    if last_pt:
        # Continuity reference for the restart case: the aircraft has to show up
        # near the last recorded point again, otherwise the recording waits.
        try:
            STATE._resume_ref = _pos_ref(last_pt, int(last_pt.get("t") or now_ms()))
        except (KeyError, TypeError, ValueError):
            STATE._resume_ref = None
        note_last_signed_t(last_pt.get("t"))
        last_t = float(last_pt.get("t") or 0) / 1000.0
        # track times are server clock – back to the PC clock for the gap length
        last_local = last_t - SERVER_CLOCK.offset_ms / 1000.0 if last_t > 0 else 0.0
        STATE._sim_gap_since = last_local if last_local > 0 else time.time()
        STATE._gap_wall_since = time.time()
        STATE._gap_reason = "restore" if restored else STATE._gap_reason
        STATE._restored_session = restored
    STATE.monitoring = True


def load_flight_session() -> bool:
    data = read_flight_session_file()
    if not data:
        return False
    apply_flight_session(data, restored=True)
    return True


def nm_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = lon2 - lon1
    while dlmb > 180.0:
        dlmb -= 360.0
    while dlmb < -180.0:
        dlmb += 360.0
    dlmb = math.radians(dlmb)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _parse_started_at(val: Any) -> Optional[float]:
    """Server timestamp → seconds on the PC clock (the server clock offset removed)."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        ts = float(val)
        ts = ts / 1000.0 if ts > 1e12 else ts
    else:
        try:
            s = str(val).replace("Z", "+00:00")
            ts = datetime.fromisoformat(s).timestamp()
        except Exception:
            return None
    return ts - SERVER_CLOCK.offset_ms / 1000.0


def apply_flight_meta(result: dict, started_at: Optional[float] = None, *, keep_load: bool = False) -> None:
    ac = result.get("aircraft") or {}
    load = result.get("load") or {}
    flight = result.get("flight") or {}
    make = str(ac.get("make") or flight.get("make") or "").strip()
    model = str(ac.get("model") or flight.get("model") or "").strip()
    reg = str(ac.get("registration") or flight.get("registration") or "").strip().upper()
    keep = bool(keep_load and STATE.load_info)
    if reg or not keep:
        STATE.registration = reg or None
        STATE.economy_registration = STATE.registration
        STATE.aircraft_label = " ".join(x for x in (reg, make, model) if x)
    from_icao = str(result.get("from_icao") or flight.get("from_icao") or "").upper() or None
    if from_icao or not keep:
        STATE.from_icao = from_icao
    to_raw = (
        result.get("to_icao")
        or flight.get("to_icao")
        or ""
    )
    if not to_raw:
        for job in load.get("assignments") or []:
            to_raw = job.get("to_icao")
            if to_raw:
                break
    to_icao = str(to_raw or "").strip().upper() or None
    if to_icao or not keep:
        STATE.to_icao = to_icao
    STATE.flight_started_at = started_at or STATE.flight_started_at or time.time()
    msr = _finite(result.get("max_sim_rate"), 0.0)
    if msr > 0:
        STATE.max_sim_rate = msr
    if isinstance(result.get("fuel_policy"), dict):
        STATE.fuel_policy = dict(result["fuel_policy"])
    if isinstance(result.get("client_features"), dict):
        STATE._flight_features = dict(result["client_features"])
    if not keep:
        STATE.has_gps = bool(ac.get("has_gps") if "has_gps" in ac else flight.get("has_gps", True))
        STATE.has_autopilot = bool(
            ac.get("has_autopilot") if "has_autopilot" in ac else flight.get("has_autopilot", True)
        )
        STATE.load_info = {
            "pax": int(load.get("seats_used") or 0),
            "seats": int(ac.get("seats") or flight.get("seats") or load.get("seats_total") or 0),
            "seats_used": int(load.get("seats_used") or 0),
            "seats_total": int(load.get("seats_total") or ac.get("seats") or 0),
            "passengers": int(load.get("passengers") or 0),
            "cargo_kg": float(load.get("cargo_kg") or 0),
            "payload_kg": float(load.get("payload_kg") or 0),
            "payload_lb": float(load.get("payload_lb") or 0)
            or (float(load.get("payload_kg") or 0) * 2.20462262),
            "crew_kg": float(load.get("crew_kg") or 77),
            "fuel_gal": float(load.get("fuel_gallons") or 0),
            "fuel_pct": float(load.get("fuel_percent") or 0),
            "fuel_capacity": float(load.get("fuel_capacity") or ac.get("fuel_capacity") or 0),
            "is_heavy": bool(load.get("is_heavy") or ac.get("is_heavy")),
            "custom_fuel": bool(
                load.get("custom_fuel")
                or (result.get("sim_load") or {}).get("custom_fuel")
            ),
            "sim_title": str(load.get("sim_title") or ""),
            "weight_kg": float(load.get("total_weight_kg") or 0),
            "jobs": len(load.get("assignments") or []),
            "assignments": list(load.get("assignments") or []),
            "cruise_speed": _finite(ac.get("cruise_speed") or load.get("cruise_speed"), 0.0),
        }
    try:
        hrs = STATE.api.get_hours_remaining()
        if hrs is not None:
            STATE.hours_remaining = hrs
    except Exception:
        pass
    save_flight_session()


# ---------------------------------------------------------------------------
# Flight end on disk (D6): written BEFORE sending, resent until the server
# confirms. Safe because the server answers a repeated /end (C1) – an old server
# answers "Flug bereits abgerechnet" / "Kein aktiver Flug", handled below.
# ---------------------------------------------------------------------------

PENDING_END_FORMAT = 2


def persist_pending_end(payload: dict, flight_id: Any, reason: str = "sending") -> dict:
    payload = dict(payload or {})
    if isinstance(payload.get("track"), list):
        payload["track"] = compact_track(payload["track"], MAX_TRACK_POINTS_SEND)
    record = {
        "format": PENDING_END_FORMAT,
        "queued_at": time.time(),
        "reason": reason,
        "flight_id": flight_id,
        "payload": payload,
        "attempts": 0,
        "next_try_at": 0.0,
        "last_error": "",
    }
    try:
        atomic_write_json(PENDING_END_PATH, record)
    except Exception as e:
        STATE.log(f"WARNUNG: Flugende konnte nicht gespeichert werden: {e}")
    STATE.pending_end = record
    return record


def note_session_ended(session_key: Any, flight_id: Any = None, result: Optional[dict] = None) -> None:
    """The server logged/closed this session's flight, or the pilot cancelled it.

    Remembered for this process (review S8) so that a restore which read the
    server's answer a moment earlier, a pending end written back by a parallel send,
    or the log window never revive it. A session file of that flight is removed."""
    key = str(session_key or "")
    if not key:
        return
    STATE._ended_sessions[key] = {
        "at": time.time(),
        "flight_id": flight_id,
        "result": dict(result) if isinstance(result, dict) else None,
    }
    if len(STATE._ended_sessions) > 20:
        oldest = sorted(STATE._ended_sessions.items(), key=lambda kv: kv[1].get("at") or 0)
        for k, _v in oldest[: len(STATE._ended_sessions) - 20]:
            STATE._ended_sessions.pop(k, None)
    with STATE.lock:
        in_memory = STATE.session_key == key
    if not in_memory:
        data = read_flight_session_file()
        if data and str(data.get("session_key")) == key:
            remove_file_and_bak(FLIGHT_SESSION_PATH)


def session_ended(session_key: Any) -> bool:
    return bool(session_key) and str(session_key) in STATE._ended_sessions


def flight_ended_locally(flight_id: Any) -> bool:
    if flight_id is None:
        return False
    return any(str(v.get("flight_id")) == str(flight_id) for v in STATE._ended_sessions.values())


def ended_result_for(session_key: Any) -> Optional[dict]:
    """The server's /end answer when this session was logged in this process."""
    hit = STATE._ended_sessions.get(str(session_key or ""))
    res = (hit or {}).get("result")
    return dict(res) if isinstance(res, dict) else None


def current_end_session_key() -> str:
    """Session key of the flight an end would be sent for: memory, file, pending end."""
    with STATE.lock:
        key = STATE.session_key
    if key:
        return str(key)
    data = read_flight_session_file()
    if data and data.get("session_key"):
        return str(data["session_key"])
    rec = STATE.pending_end or load_pending_end()
    return str(((rec or {}).get("payload") or {}).get("session_key") or "")


def update_pending_end(record: dict, **changes: Any) -> None:
    record.update(changes)
    key = (record.get("payload") or {}).get("session_key")
    if session_ended(key):
        return  # cancelled or logged meanwhile – never write it back (review S8)
    if STATE.pending_end is None and not file_or_bak_exists(PENDING_END_PATH):
        return  # cleared by another thread while this send was running
    try:
        atomic_write_json(PENDING_END_PATH, record)
    except Exception:
        pass
    STATE.pending_end = record


def queue_pending_end(payload: dict, reason: str) -> None:
    """Kept for older call sites: store an unsent end for the retry loop."""
    persist_pending_end(payload, STATE.active_flight_id, reason)
    STATE.log(f"Flugende zwischengespeichert – wird automatisch nachgesendet ({reason})")


def load_pending_end() -> Optional[dict]:
    data = read_json_file(PENDING_END_PATH)
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        return data
    return None


def clear_pending_end() -> None:
    STATE.pending_end = None
    remove_file_and_bak(PENDING_END_PATH)


def pending_end_for(flight_id: Any, session_key: Optional[str]) -> Optional[dict]:
    record = STATE.pending_end or load_pending_end()
    if not record:
        return None
    payload = record.get("payload") or {}
    if session_key and payload.get("session_key") != session_key:
        return None
    fid = record.get("flight_id")
    if fid is not None and flight_id is not None and str(fid) != str(flight_id):
        return None
    return record


def _pending_is_current_flight(record: dict) -> bool:
    payload = record.get("payload") or {}
    if not STATE.session_key or payload.get("session_key") != STATE.session_key:
        return False
    fid = record.get("flight_id")
    return fid is None or STATE.active_flight_id is None or str(fid) == str(STATE.active_flight_id)


def _send_pending_end(record: dict, *, notify: bool = True) -> Optional[dict]:
    payload = dict(record.get("payload") or {})
    key = payload.get("session_key")
    now = time.time()
    if session_ended(key):
        clear_pending_end()  # cancelled or logged in this process already
        return None
    try:
        result = STATE.api.submit_end(payload)
    except RetryableError as e:
        update_pending_end(
            record,
            attempts=int(record.get("attempts") or 0) + 1,
            last_error=str(e),
            next_try_at=now + max(5, int(getattr(e, "retry_after", 15) or 15)),
        )
        STATE.last_error = str(e)
        return None
    except SessionInvalidError as e:
        STATE.log(f"Nachgesendetes Flugende passt nicht mehr zur Sitzung – verworfen: {e}")
        clear_pending_end()
        return None
    except KeepFlyingError as e:
        if getattr(e, "code", "") == "TOKEN":
            update_pending_end(record, last_error=str(e), next_try_at=now + 60)
            STATE.last_error = str(e)
            return None
        # Stored at the parking position; NOT_ON_GROUND etc. cannot get better.
        STATE.log(f"Nachgesendetes Flugende abgelehnt, Flug läuft weiter: {e}")
        clear_pending_end()
        return None
    except AirportRejectedError as e:
        STATE.log(f"Nachgesendetes Flugende: {e} – Flug läuft weiter.")
        clear_pending_end()
        return None
    except FlightClosedError as e:
        clear_pending_end()
        # Review S8: "current" only now – a restore may have applied this very
        # session while the request was on its way.
        current = _pending_is_current_flight(record)
        note_session_ended(key, record.get("flight_id"))
        if current:
            server_ended_flight(str(e))
        else:
            STATE.log(f"Nachgesendetes Flugende: Flug vom Server beendet ({e}).")
        return None
    except NoActiveFlightError as e:
        clear_pending_end()
        gone = True
        try:
            gone = not STATE.api.get_active()
        except Exception:
            gone = False
        msg = t_ui(
            "Der Flug ist auf dem Server nicht mehr aktiv – vermutlich wurde er schon geloggt. "
            "Bitte auf der Website unter „Flüge“ nachsehen.",
            "The flight is no longer active on the server – it was probably logged already. "
            "Please check “Flights” on the website.",
        )
        STATE.log(f"{msg} ({e})")
        current = _pending_is_current_flight(record)
        if gone:
            note_session_ended(key, record.get("flight_id"))
        if current and gone:
            _clear_flight_runtime()
            if notify:
                notify_windows(t_ui("FlyConomy – Flugende", "FlyConomy – Flight end"), msg, code="FC-END-UNKNOWN", kind="info")
        return None
    except Exception as e:
        # The server rejected it for good – keeping it queued would loop forever
        STATE.log(f"Nachgesendetes Flugende abgelehnt: {e}")
        clear_pending_end()
        return None

    clear_pending_end()
    # Review S8: decide AFTER the answer whether this is the flight in memory – the
    # restore thread may have applied the session while the POST was running.
    note_session_ended(key, record.get("flight_id") or result.get("flight_id"), result)
    if _pending_is_current_flight(record):
        _clear_flight_runtime()
        STATE.set_ui_status("Flug beendet", "Flight ended")
    remember_end_result(result)
    STATE.log(
        f"Flugende akzeptiert – Status "
        f"{(result.get('validation') or {}).get('status') or ('bereits geloggt' if result.get('already_logged') else '?')} "
        f"· Pay ${result.get('total_payment', 0)}"
    )
    if notify:
        notify_flight_logged(result)
        schedule_cockpit_ack("log")
    return result


def flush_pending_end() -> Optional[dict]:
    """Retries a stored flight end (retry loop). Returns the server result on success."""
    record = STATE.pending_end or load_pending_end()
    if not record or not ensure_api_token():
        return None
    if time.time() < float(record.get("next_try_at") or 0):
        return None
    if _log_window_sending() and _pending_is_current_flight(record):
        return None  # the log window retries this very end itself (review S8)
    if not _END_LOCK.acquire(blocking=False):
        return None  # the log window is sending this very end right now
    try:
        record = load_pending_end() or STATE.pending_end
        if not record:
            return None
        return _send_pending_end(record, notify=True)
    finally:
        _END_LOCK.release()


def _log_window_sending() -> bool:
    """True while the log window's worker runs (also between its retries)."""
    mod = sys.modules.get("flight_log_ui")
    return bool(getattr(mod, "_busy", False)) if mod is not None else False


# ---------------------------------------------------------------------------
# Start queue (D7): one record, its first queued_at kept, one notice only.
# ---------------------------------------------------------------------------


def write_pending_start(record: dict) -> None:
    try:
        atomic_write_json(PENDING_START_PATH, record)
    except Exception as e:
        STATE.log(f"WARNUNG: Start konnte nicht zwischengespeichert werden: {e}")
    STATE.pending_start = record


def queue_pending_start(reason: str, record: Optional[dict] = None) -> dict:
    record = dict(record or {})
    first = not record.get("notified")
    record.setdefault("queued_at", time.time())
    record["reason"] = reason
    record["state"] = "queued"
    record["notified"] = True
    write_pending_start(record)
    msg = t_ui(
        "Server kurz weg — Start wird automatisch nachgeholt. NICHT losfliegen, im Sim bleiben.",
        "Server briefly down — start will retry automatically. Do NOT take off, stay in the sim.",
    )
    STATE.set_ui_status(msg)
    if first:
        STATE.log(msg)
        notify_windows(
            t_ui("FlyConomy – Start wartet", "FlyConomy – Start queued"),
            msg,
            code="FC-START-QUEUED",
            kind="info",
        )
    return record


def load_pending_start() -> Optional[dict]:
    data = read_json_file(PENDING_START_PATH)
    return data if isinstance(data, dict) else None


def clear_pending_start() -> None:
    STATE.pending_start = None
    remove_file_and_bak(PENDING_START_PATH)


def pending_start_moved(record: dict, sample: dict) -> Optional[str]:
    """Why a queued start must not be sent any more, or None (D7)."""
    pos = record.get("pos") or {}
    if not bool(sample.get("on_ground")):
        return t_ui("das Flugzeug ist in der Luft", "the aircraft is airborne")
    if _finite(sample.get("ground_speed")) > PENDING_START_MAX_GS_KT:
        return t_ui("das Flugzeug rollt", "the aircraft is moving")
    try:
        moved = nm_between(
            float(pos["latitude"]),
            float(pos["longitude"]),
            float(sample["latitude"]),
            float(sample["longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if moved > PENDING_START_MAX_MOVE_NM:
        return t_ui(
            f"das Flugzeug steht {moved:.1f} nm weiter weg",
            f"the aircraft moved {moved:.1f} nm",
        )
    return None


def flush_pending_start() -> Optional[dict]:
    record = STATE.pending_start or load_pending_start()
    if not record or not ensure_api_token():
        return None
    now = time.time()
    queued_at = float(record.get("queued_at") or 0)
    if not queued_at or now - queued_at > PENDING_START_MAX_SEC:
        clear_pending_start()
        msg = t_ui(
            "Start-Warteschlange abgelaufen – bitte erneut starten.",
            "Queued start expired – please start again.",
        )
        STATE.last_error = msg
        STATE.log(msg)
        notify_windows(t_ui("FlyConomy – Start", "FlyConomy – Start"), msg, code="FC-START-EXPIRED", kind="info")
        return None
    if now < float(record.get("next_try_at") or 0):
        return None
    if STATE.monitoring and STATE.session_key:
        clear_pending_start()  # a flight is already being recorded
        return None
    if not STATE.sim.connected and not _msfs_process_running():
        # Review S8: without MSFS no /start can be prepared – no HTTP every 5 s.
        _delay_pending_start(record, 15)
        return None
    try:
        return action_start(from_queue=record, wait=False)
    except RetryableError as e:
        _delay_pending_start(record, max(15, int(getattr(e, "retry_after", 15) or 15)))
        return None
    except Exception as e:
        # action_start cleared the record and told the pilot (_start_failed); keep
        # the reason visible in any case.
        STATE.last_error = str(e)
        return None


def _delay_pending_start(record: dict, sec: float) -> None:
    """Next try of a waiting start not before now+sec (never earlier than planned)."""
    current = STATE.pending_start if isinstance(STATE.pending_start, dict) else load_pending_start()
    if not isinstance(current, dict):
        return  # cleared meanwhile
    if current.get("client_session_key") != record.get("client_session_key"):
        return
    due = time.time() + float(sec)
    if float(current.get("next_try_at") or 0) >= due:
        return
    current["next_try_at"] = due
    write_pending_start(current)


def reset_queue_timers() -> None:
    """A freshly started client tries stored start/end records right away (the
    waiting time belonged to the previous process)."""
    STATE.pending_end = STATE.pending_end or load_pending_end()
    STATE.pending_start = STATE.pending_start or load_pending_start()
    for rec in (STATE.pending_end, STATE.pending_start):
        if isinstance(rec, dict):
            rec["next_try_at"] = 0.0


def retry_loop() -> None:
    """Background worker that drains pending start/end queues."""
    while True:
        try:
            if (STATE.pending_end or file_or_bak_exists(PENDING_END_PATH)) and STATE.api.token:
                flush_pending_end()
            if (STATE.pending_start or file_or_bak_exists(PENDING_START_PATH)) and STATE.api.token:
                flush_pending_start()
        except Exception as e:
            STATE.last_error = str(e)
        time.sleep(5)


def _finite(val: Any, default: float = 0.0) -> float:
    """JSON-safe float — Fenix often yields NaN for fuel/weight until the EFB opens."""
    try:
        x = float(val)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _agl_from(val: Any) -> Optional[float]:
    """Radio/AGL in feet. Missing or sentinel values are None — never 9999."""
    if val is None:
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x < 0 or x > 5000:
        return None
    return x


def _heading_deg(val: Any) -> float:
    """True heading in degrees. Python-SimConnect often yields radians for this simvar."""
    h = _finite(val)
    if 0.0 <= h <= math.tau + 0.05:
        h = math.degrees(h)
    return (h + 360.0) % 360.0


def make_sample(raw: dict) -> dict:
    rate = _finite(raw.get("simulation_rate"), 1.0)
    if rate <= 0:
        rate = 1.0
    fuel_raw = raw.get("fuel_total")
    weight_raw = raw.get("total_weight")
    sample = {
        "latitude": _finite(raw["latitude"]),
        "longitude": _finite(raw["longitude"]),
        "altitude": _finite(raw.get("altitude")),
        "ground_speed": max(0.0, _finite(raw.get("ground_speed"))),
        "heading": _heading_deg(raw.get("heading")),
        "fuel_total": max(0.0, _finite(fuel_raw)) if fuel_raw is not None else None,
        "total_weight_lb": max(0.0, _finite(weight_raw)) if weight_raw is not None else None,
        "on_ground": bool(raw.get("on_ground", False)),
        "simulation_rate": rate,
        "parking_brake": bool(raw.get("parking_brake", False)),
        "alt_agl": _agl_from(raw.get("alt_agl")),
        "on_ground_sim": bool(raw.get("on_ground_sim", raw.get("on_ground", False))),
    }
    return stabilize_load(sample)


def stabilize_load(sample: dict) -> dict:
    """Keep last good fuel/weight when SimConnect spikes to 0 (alt-tab, pause, engine off)."""
    gs = float(sample.get("ground_speed") or 0)
    parked = gs < PARKED_IDLE_GS_KT
    last_f = float(STATE._last_good_fuel or 0)
    last_w = float(STATE._last_good_weight or 0)
    fuel = sample.get("fuel_total")
    weight = sample.get("total_weight_lb")
    now_t = time.time()
    refuel_ok = bool(getattr(STATE, "_gsx_refuel", False)) or now_t < float(
        getattr(STATE, "_refuel_grace_until", 0) or 0
    )
    try:
        fuel_f = float(fuel) if fuel is not None else None
    except (TypeError, ValueError):
        fuel_f = None
    try:
        weight_f = float(weight) if weight is not None else None
    except (TypeError, ValueError):
        weight_f = None

    if last_f > FUEL_GLITCH_MIN_GAL:
        missing = fuel_f is None or fuel_f < FUEL_ZERO_SPIKE_GAL
        parked_drop = (
            parked
            and fuel_f is not None
            and last_f - fuel_f > PARKED_FUEL_DROP_HOLD_GAL
        )
        if parked and (missing or parked_drop):
            sample["fuel_total"] = last_f
            STATE._fuel_zero_streak = 0
        elif (not parked) and missing:
            STATE._fuel_zero_streak = int(STATE._fuel_zero_streak or 0) + 1
            if STATE._fuel_zero_streak < 4:
                sample["fuel_total"] = last_f
            else:
                sample["fuel_total"] = 0.0
        else:
            STATE._fuel_zero_streak = 0
            if fuel_f is not None and fuel_f >= FUEL_ZERO_SPIKE_GAL:
                parked_spike = (
                    parked
                    and fuel_f > last_f + FUEL_INCREASE_ABORT_GAL
                    and not refuel_ok
                )
                if parked_spike:
                    sample["fuel_total"] = last_f
                else:
                    STATE._last_good_fuel = fuel_f
                    sample["fuel_total"] = fuel_f
    elif fuel_f is not None and fuel_f >= FUEL_ZERO_SPIKE_GAL:
        STATE._last_good_fuel = fuel_f
        sample["fuel_total"] = fuel_f
        STATE._fuel_zero_streak = 0
    else:
        sample["fuel_total"] = float(fuel_f or 0.0)

    if last_w > 80 and parked and (weight_f is None or weight_f < 20):
        sample["total_weight_lb"] = last_w
    elif weight_f is not None and weight_f > 80:
        STATE._last_good_weight = weight_f
        sample["total_weight_lb"] = weight_f
    else:
        sample["total_weight_lb"] = float(weight_f or last_w or 0.0)
    return sample


def _policy_markers(*keys: str) -> tuple:
    """Marker lists the server sends in /start → fuel_policy (E7)."""
    pol = STATE.fuel_policy if isinstance(STATE.fuel_policy, dict) else {}
    out: list = []
    for key in keys:
        vals = pol.get(key)
        if isinstance(vals, (list, tuple)):
            out.extend(str(v).strip().lower() for v in vals if str(v).strip())
    return tuple(out)


def _blob_has_unreliable_fuel(blob: str) -> bool:
    b = (blob or "").lower()
    markers = _UNRELIABLE_FUEL_MARKERS + _policy_markers("unreliable_markers", "markers")
    return any(m in b for m in markers)


def fuel_policy_says_unreliable() -> bool:
    pol = STATE.fuel_policy if isinstance(STATE.fuel_policy, dict) else {}
    for key in ("fuel_unreliable", "unreliable", "skip_load_integrity", "custom_fuel"):
        if pol.get(key) is True:
            return True
    return False


def sim_fuel_unreliable() -> bool:
    """True when MSFS A: fuel/weight is not the aircraft's real tanks (Fenix/PMDG/airliners).

    Cheats on these types are caught by teleport, simrate and impossible GS — not by
    FUEL_TOTAL_QUANTITY, which jumps on gear, flaps, GSX and EFB without Tab.
    Also true for Asobo Cessna 408 SkyCourier: tanks stay writable, but the simvar
    leaps on cargo-empty / EFB / taxi and used to abort honest flights.
    """
    load = STATE.load_info or {}
    if load.get("is_heavy") or load.get("custom_fuel"):
        return True
    # Same marker list as the server (E7): the DC-3 used to be cancelled here
    # although the server would have accepted the flight.
    if fuel_policy_says_unreliable():
        return True
    cap = float(load.get("fuel_capacity") or 0)
    if cap >= 1500.0:
        return True
    extra = f"{STATE.aircraft_label or ''} {load.get('sim_title') or ''}"
    custom_markers = _policy_markers("custom_fuel_markers")
    if custom_markers and any(m in extra.lower() for m in custom_markers):
        return True
    try:
        extra = f"{extra} {STATE.sim.sim_title()}"
    except Exception:
        pass
    if _blob_has_unreliable_fuel(extra):
        return True
    try:
        if STATE.sim.has_custom_fuel(extra):
            return True
    except Exception:
        pass
    return False


def park_gs_limit() -> float:
    return PARK_GS_KT_AIRLINER if sim_fuel_unreliable() else PARK_GS_KT


def left_origin() -> bool:
    """True after a real departure (not just a long taxi at the start airport)."""
    if STATE._was_airborne:
        return True
    return float(STATE._max_nm_from_origin or 0) >= 8.0


def note_flight_progress(sample: dict) -> None:
    gs = float(sample.get("ground_speed") or 0)
    agl = _agl_from(sample.get("alt_agl"))
    if gs >= AIRBORNE_GS_KT or (
        not bool(sample.get("on_ground_sim"))
        and gs >= 25.0
        and agl is not None
        and agl > 80.0
    ):
        STATE._was_airborne = True
    origin = STATE.local_track[0] if STATE.local_track else None
    if not origin:
        return
    try:
        nm = nm_between(
            float(origin["latitude"]),
            float(origin["longitude"]),
            sample["latitude"],
            sample["longitude"],
        )
    except (KeyError, TypeError, ValueError):
        return
    if nm > float(STATE._max_nm_from_origin or 0):
        STATE._max_nm_from_origin = nm


def fuel_weight_tamper_reason(
    prev: dict,
    sample: dict,
    dt_sec: float,
    fuel_capacity: float,
    simrate: float,
    *,
    allow_ground_uplift: bool = False,
    planned_fuel: float = 0.0,
) -> Optional[str]:
    """Detect manual fuel/weight edits. Allows normal burn with a buffer."""
    prev_fuel = float(prev.get("fuel") or 0)
    fuel = float(sample.get("fuel_total") or 0)
    dt_hours = max(dt_sec, 1.0) / 3600.0
    rate = max(1.0, float(simrate or 1.0))
    cap = max(0.0, float(fuel_capacity or 0.0))
    gs = float(sample.get("ground_speed") or 0)
    parked = gs < PARKED_IDLE_GS_KT
    ground_uplift = (
        allow_ground_uplift
        and bool(sample.get("on_ground"))
        and fuel > prev_fuel
        and planned_fuel > 0
        and fuel <= planned_fuel + max(2.0, planned_fuel * 0.02)
    )
    if fuel < 0.5 and prev_fuel > 8.0:
        # SimConnect zero-tick (Cessna 152 / pause) — not a dump.
        return None
    if fuel > prev_fuel + FUEL_INCREASE_ABORT_GAL and not ground_uplift:
        return "fuel_tamper"
    max_drop = 10.0 + (cap * 0.12 if cap > 0 else 250.0) * rate * dt_hours
    if (not parked) and prev_fuel - fuel > max_drop + 8.0:
        return "fuel_tamper"
    prev_w = float(prev.get("weight") or 0)
    weight = float(sample.get("total_weight_lb") or 0)
    if prev_w > 50 and weight > 50:
        expected = (fuel - prev_fuel) * LB_PER_GAL
        actual = weight - prev_w
        if abs(actual - expected) > WEIGHT_UNEXPLAINED_LB:
            return "weight_tamper"
    return None


_SIGN_LOCK = threading.Lock()
_LAST_SIGNED_T = {"t": 0}


def track_sign_payload(seq: int, t: int, sample: dict) -> str:
    """Byte for byte backend/src/utils/flightSession.ts::signTrackPoint (toFixed)."""
    rate = float(sample.get("simulation_rate") or 1.0)
    return "|".join(
        [
            str(int(seq)),
            str(int(t)),
            js_to_fixed(float(sample["latitude"]), 6),
            js_to_fixed(float(sample["longitude"]), 6),
            js_to_fixed(float(sample.get("altitude") or 0), 1),
            js_to_fixed(float(sample.get("ground_speed") or 0), 1),
            js_to_fixed(float(sample.get("fuel_total") or 0), 2),
            "1" if sample.get("on_ground") else "0",
            js_to_fixed(rate, 2),
        ]
    )


def note_last_signed_t(t: Any) -> None:
    try:
        _LAST_SIGNED_T["t"] = int(t or 0)
    except (TypeError, ValueError):
        _LAST_SIGNED_T["t"] = 0


def sign_point(session_key: str, seq: int, sample: dict, *, t: Optional[int] = None) -> dict:
    with _SIGN_LOCK:
        t_ms = int(t) if t is not None else now_ms()  # server clock (D5)
        if seq > 0 and t_ms <= _LAST_SIGNED_T["t"]:
            t_ms = _LAST_SIGNED_T["t"] + 1  # the server rejects times that run backwards
        _LAST_SIGNED_T["t"] = t_ms
    rate = float(sample.get("simulation_rate") or 1.0)
    payload = track_sign_payload(seq, t_ms, sample)
    sig = hmac.new(session_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    out = dict(sample)
    out.update({"seq": seq, "t": t_ms, "sig": sig, "simulation_rate": rate})
    return out


def compact_track(track: List[dict], max_points: int = MAX_TRACK_POINTS_SEND) -> List[dict]:
    """Keep first/last and evenly spaced signed points (seq stays monotonic)."""
    if max_points < 2 or len(track) <= max_points:
        return list(track)
    if len(track) == 2:
        return list(track)
    out: List[dict] = [track[0]]
    mid = track[1:-1]
    need = max_points - 2
    if need <= 0:
        return [track[0], track[-1]]
    if len(mid) <= need:
        out.extend(mid)
    else:
        step = (len(mid) - 1) / max(1, need - 1) if need > 1 else 0
        seen = set()
        for i in range(need):
            idx = int(round(i * step)) if need > 1 else 0
            idx = max(0, min(len(mid) - 1, idx))
            if idx in seen:
                continue
            seen.add(idx)
            out.append(mid[idx])
    out.append(track[-1])
    # Drop accidental duplicates by identity/seq
    deduped: List[dict] = []
    last_seq = None
    for p in out:
        seq = p.get("seq")
        if seq is not None and seq == last_seq:
            continue
        deduped.append(p)
        last_seq = seq
    if deduped[-1] is not track[-1] and deduped[-1].get("seq") != track[-1].get("seq"):
        deduped.append(track[-1])
    return deduped


def adopt_server_flight(flight: dict) -> None:
    """Take over a flight the captain already started (shared cockpit)."""
    session_key = flight.get("session_key")
    if not session_key or not flight.get("id"):
        return
    raw = STATE.sim.read() if STATE.sim.connected else None
    sample = make_sample(raw) if raw else None
    STATE.active_flight_id = flight.get("id")
    with STATE.lock:
        STATE.session_key = session_key
        STATE.track_seq = 0
        STATE.local_track = [sign_point(session_key, 0, sample)] if sample else []
        STATE.track_seq = 1 if sample else 0
    load = flight.get("load") or {}
    if not load:
        try:
            mf = STATE.api.get_myflight()
            load = (mf or {}).get("load_state") or {}
        except Exception:
            load = {}
    result = {
        "flight": flight,
        "load": load,
        "from_icao": flight.get("from_icao"),
        "aircraft": {
            "registration": flight.get("registration"),
            "make": flight.get("make"),
            "model": flight.get("model"),
            "seats": flight.get("seats"),
            "has_gps": flight.get("has_gps"),
            "has_autopilot": flight.get("has_autopilot"),
        },
    }
    if STATE.sim.connected and load:
        try:
            STATE.sim.apply_economy_load(load)
        except Exception as e:
            STATE.log(f"Crew-Load: {e}")
    apply_flight_meta(result)
    STATE.monitoring = True
    STATE._integrity_last = None
    STATE._last_good_fuel = 0.0
    STATE._last_good_weight = 0.0
    STATE._fuel_zero_streak = 0
    STATE.last_message = "Crewflug übernommen"
    STATE.log(
        f"Crewflug übernommen · {flight.get('registration') or ''} · "
        f"Start {flight.get('from_icao') or ''}"
    )


_START_LOCK = threading.Lock()
_END_LOCK = threading.Lock()


def action_start(from_queue: Optional[dict] = None, *, wait: bool = True) -> dict:
    """Start a flight. Every way in (bridge, website gateway, hotkey, desktop UI,
    start queue) goes through here – and through the version gate (D12)."""
    if not _START_LOCK.acquire(blocking=wait):
        raise RetryableError(t_ui("Start läuft bereits", "Start already running"), 5)
    try:
        try:
            assert_start_allowed()
        except VersionGateError as e:
            if from_queue is not None:
                clear_pending_start()
            STATE.last_error = str(e)
            notify_windows(
                t_ui("FlyConomy – Update nötig", "FlyConomy – Update required"),
                str(e),
                code="FC-OUTDATED",
                kind="error",
            )
            raise
        return _action_start(from_queue)
    finally:
        _START_LOCK.release()


def _start_failed(msg: str, from_queue: Optional[dict], *, code: str = "FC-START", plain: bool = False) -> None:
    STATE.last_error = msg
    if from_queue is not None:
        clear_pending_start()
        if not plain:
            msg = t_ui(f"Nachgeholter Start abgelehnt: {msg}", f"Queued start rejected: {msg}")
        # the queue's "Start wird nachgeholt – NICHT losfliegen" must not stay up
        STATE.set_ui_status(msg, msg)
    STATE.log(msg)
    notify_windows(
        t_ui("FlyConomy – Start fehlgeschlagen", "FlyConomy – Start failed"),
        msg,
        code=code,
        kind="error",
    )


def stranded_start_message() -> str:
    return t_ui(
        "Auf dem Server läuft bereits ein Flug, den dieser Client nicht aufzeichnet "
        "(z. B. ging die Antwort auf „Flug starten“ unterwegs verloren). Ohne Aufzeichnung kann "
        "er nicht geloggt werden – bitte im Panel „Abbrechen“ drücken und neu starten.",
        "The server already has a flight that this client is not recording (e.g. the answer to "
        "“Start flight” got lost). Without a recording it cannot be logged – please press "
        "“Cancel” in the panel and start again.",
    )


def _usable_pending_start() -> Optional[dict]:
    """The waiting start record, if it is still within its 10 minutes (D7)."""
    rec = STATE.pending_start if isinstance(STATE.pending_start, dict) else load_pending_start()
    if not isinstance(rec, dict) or not rec.get("client_session_key"):
        return None
    queued_at = float(rec.get("queued_at") or 0)
    if not queued_at or time.time() - queued_at > PENDING_START_MAX_SEC:
        return None
    return rec


def _action_start(from_queue: Optional[dict] = None) -> dict:
    try:
        STATE.api.refresh_session()
    except Exception:
        pass
    if not STATE.api.token:
        raise RuntimeError("Nicht angemeldet")
    try:
        mf = STATE.api.get_myflight()
        role = str((mf or {}).get("crew_role") or "")
        STATE.crew_guest = role == "crew"
        if STATE.crew_guest:
            msg = "Crewflug: nur der Captain startet den Flug."
            if from_queue is not None:
                _start_failed(msg, from_queue)
            raise RuntimeError(msg)
    except RuntimeError:
        raise
    except Exception:
        pass
    STATE.sim_io_pause = True
    try:
        # D7: a manual start while a start waits in the queue uses the SAME
        # client_session_key – never a second one (review S8).
        pending = from_queue if from_queue is not None else _usable_pending_start()
        resend_key = ""
        existing = None
        try:
            existing = STATE.api.get_active()
        except Exception:
            existing = None
        if existing and existing.get("id"):
            # A running flight that we are not recording is the worst state to be
            # in: the leg gets rejected later for missing track points. Take it
            # over whenever we still hold its session key, either in memory or on
            # disk, instead of sending the pilot away.
            if not STATE.monitoring:
                adopted = adopt_running_flight(existing)
                if adopted:
                    if pending is not None:
                        clear_pending_start()
                    return {"ok": True, "adopted": True, "flight": existing}
                # Review S8 (C8/D7): our /start may have reached the server while the
                # answer got lost. Send it again with the SAME client_session_key – a
                # server with C8 answers with that flight and its session key; an old
                # server says "bereits ein Flug" and the pilot is told what to do.
                resend_key = str((pending or {}).get("client_session_key") or "")
            if not resend_key:
                if pending is not None:
                    clear_pending_start()
                STATE.active_flight_id = existing.get("id")
                if not STATE.monitoring:
                    # not recorded here and not adoptable: the parking brake can
                    # never log it – say so (review S8)
                    msg = stranded_start_message()
                    STATE.last_error = msg
                    STATE.set_ui_status(msg, msg)
                    raise RuntimeError(msg)
                raise RuntimeError(
                    t_ui(
                        "Es läuft bereits ein Flug. Ende: Handbremse am Ziel oder „Abbrechen“.",
                        "A flight is already running. End: parking brake at the destination or “Cancel”.",
                    )
                )
        if not STATE.sim.connected:
            ok, msg = STATE.sim.connect()
            if not ok:
                if from_queue is not None:
                    raise RetryableError(msg, 15)
                raise RuntimeError(msg)
        raw = None
        for _ in range(8):
            raw = STATE.sim.read()
            if raw:
                probe = make_sample(raw)
                if sample_plausible(probe):
                    break
            time.sleep(0.3)
        if not raw:
            if from_queue is not None:
                raise RetryableError("Keine Sim-Daten", 15)
            raise RuntimeError("Keine Sim-Daten – in MSFS sitzen und erneut verbinden.")
        sample = make_sample(raw)
        for k in ("aircraft_type", "atc_model", "atc_type"):
            if raw.get(k):
                sample[k] = raw[k]
        if from_queue is not None and not resend_key:
            why = pending_start_moved(from_queue, sample)
            if why:
                clear_pending_start()
                msg = t_ui(
                    f"Wartender Start verworfen: {why}. Bitte am Boden neu starten.",
                    f"Queued start dropped: {why}. Please start again on the ground.",
                )
                STATE.last_error = msg
                STATE.set_ui_status(msg, msg)
                STATE.log(msg)
                notify_windows(
                    t_ui("FlyConomy – Start verworfen", "FlyConomy – Start dropped"),
                    msg,
                    code="FC-START-DROPPED",
                    kind="error",
                )
                return {"ok": False, "dropped": True, "message": msg}
        # D7: our own key for this start, on disk before the request leaves. A
        # retried /start (queue, crash) carries the same key; a server with C8
        # then answers with the flight it already has instead of a second one.
        record = dict(pending or {})
        if not record.get("client_session_key"):
            record["client_session_key"] = secrets.token_hex(32)
        record.setdefault("queued_at", time.time())
        here = {
            "latitude": sample["latitude"],
            "longitude": sample["longitude"],
            "on_ground": bool(sample.get("on_ground")),
            "ground_speed": sample.get("ground_speed"),
        }
        if from_queue is None:
            record["pos"] = here  # the pilot pressed Start again – here
        else:
            record.setdefault("pos", here)
        record.setdefault("state", "sending")
        record["attempts"] = int(record.get("attempts") or 0) + 1
        write_pending_start(record)
        fuel_before: dict = {}
        try:
            fuel_before = STATE.sim.fuel_system_info()
        except Exception:
            fuel_before = {}
        body = dict(sample)
        body["client_session_key"] = record["client_session_key"]
        body["client_version"] = CLIENT_VERSION
        if fuel_before:
            body["fuel_report"] = {"stage": "before_load", **fuel_before}
        try:
            result = STATE.api.start_flight(body)
            STATE.last_error = None
        except RetryableError as e:
            record["last_error"] = str(e)
            record["next_try_at"] = time.time() + max(10, min(int(e.retry_after or 15), 60))
            queue_pending_start(str(e), record)
            if from_queue is not None:
                raise
            return {
                "ok": True,
                "queued": True,
                "message": t_ui(
                    "Server kurz weg — Start wird automatisch nachgeholt. NICHT losfliegen, im Sim bleiben.",
                    "Server briefly down — start will retry automatically. Do NOT take off, stay in the sim.",
                ),
            }
        except RuntimeError as e:
            msg = str(e)
            clear_pending_start()
            already = "bereits ein flug" in msg.lower() or "already" in msg.lower()
            if already:
                try:
                    again = STATE.api.get_active()
                    if again and again.get("id"):
                        STATE.active_flight_id = again.get("id")
                except Exception:
                    pass
            if already and resend_key:
                # Old server without C8: the flight exists, we cannot record it.
                msg = stranded_start_message()
                _start_failed(msg, from_queue, code="FC-START-STRANDED", plain=True)
                if from_queue is None:
                    STATE.set_ui_status(msg, msg)
                raise RuntimeError(msg) from e
            _start_failed(msg, from_queue if from_queue is not None else None)
            raise
        STATE.active_flight_id = (result.get("flight") or {}).get("id")
        session_key = result.get("session_key")
        if not session_key:
            clear_pending_start()
            raise RuntimeError("Server lieferte keinen session_key – Backend aktualisieren.")
        with STATE.lock:
            STATE.session_key = session_key
            STATE.track_seq = 0
            STATE.local_track = [sign_point(session_key, 0, sample)]
            STATE.track_seq = 1
        STATE.flight_started_at = time.time()
        STATE._flight_title = str(sample.get("aircraft_type") or "")
        STATE._max_gs_seen = 0.0
        note_max_gs(sample.get("ground_speed"))
        msr = _finite(result.get("max_sim_rate"), 0.0)
        STATE.max_sim_rate = msr if msr > 0 else MAX_SIM_RATE
        STATE.from_icao = str(result.get("from_icao") or "").upper() or None
        STATE.to_icao = None
        _clear_mismatch()
        # D3: the session is on disk before anything else can go wrong – and only
        # then the queue record goes (a crash in between keeps one of the two).
        save_flight_session()
        clear_pending_start()
        if resend_key:
            STATE.log(
                t_ui(
                    "Server hat den bereits angelegten Flug bestätigt – Aufzeichnung läuft.",
                    "The server confirmed the flight it had already created – recording runs.",
                )
            )

        load = result.get("load") or {}
        load["sim_title"] = sample.get("aircraft_type") or raw.get("aircraft_type") or ""
        # Enrich from My Flight if start payload under-reports passengers.
        try:
            mf = STATE.api.get_myflight()
            ls = (mf or {}).get("load_state") or (mf or {}).get("load_preview") or {}
            if int(ls.get("seats_used") or 0) > int(load.get("seats_used") or 0):
                load = {
                    **load,
                    "seats_used": ls.get("seats_used"),
                    "seats_total": ls.get("seats_total") or load.get("seats_total"),
                    "passengers": ls.get("passengers") or load.get("passengers"),
                    "payload_kg": ls.get("payload_kg") or load.get("payload_kg"),
                    "payload_lb": (float(ls.get("payload_kg") or 0) * 2.20462262)
                    if ls.get("payload_kg")
                    else load.get("payload_lb"),
                    "cargo_kg": ls.get("cargo_kg") or load.get("cargo_kg"),
                }
                result["load"] = load
                STATE.log(
                    f"Beladung aus Mein Flug nachgezogen: seats_used={load.get('seats_used')} "
                    f"passengers={load.get('passengers')} payload={load.get('payload_kg')} kg"
                )
        except Exception as e:
            STATE.log(f"Mein-Flug-Beladung nachziehen: {e}")

        # Arm recording NOW. The server already has the flight; load/fuel writes
        # into MSFS can take seconds or throw. Yesterday the clock and live map
        # only came up after that work finished — if it threw (cargo_lb), they
        # never came up at all.
        apply_flight_meta(result)
        STATE.monitoring = True
        STATE._integrity_last = None
        STATE._last_good_fuel = 0.0
        STATE._last_good_weight = 0.0
        STATE._fuel_zero_streak = 0
        save_flight_session()

        applied: Dict[str, Any] = {"details": []}
        atc_ok = False
        try:
            applied = STATE.sim.apply_economy_load(load)
            # Once: verify; if not accepted, exactly one reload — then never touch load again.
            time.sleep(0.7)
            verify = STATE.sim.apply_economy_load(load, repair=True)
            vdet = ";".join(str(x) for x in (verify.get("details") or []))
            if applied.get("custom_fuel") or "custom_efb" in vdet:
                applied = {
                    **applied,
                    "details": list(applied.get("details") or [])
                    + list(verify.get("details") or []),
                }
            elif "no_drift" in vdet:
                applied = {
                    **applied,
                    "details": list(applied.get("details") or [])
                    + list(verify.get("details") or []),
                }
            elif "stations_fixed" in vdet:
                # Soft reload already done by repair — count as the one Nachladen.
                STATE.log("Beladung korrigiert (ein Nachladen)")
                applied = verify
            else:
                STATE.log("Beladung nicht übernommen – einmal nachladen")
                # Never stack a second full fuel write — that doubled ATR tanks (main + aux).
                applied = STATE.sim.apply_economy_load(load, repair=bool(applied.get("fuel_ok")))
                applied["details"] = list(applied.get("details") or []) + ["reload_once"]
            result["sim_load"] = applied
            # No further fuel/payload writes until flight end.
            STATE._payload_reapply_until = 0.0
            STATE._payload_reapply_tick = 0
            STATE._equip_tick = 0
            if STATE.economy_registration:
                atc_ok = STATE.sim.set_atc_id(STATE.economy_registration)
                time.sleep(0.2)
                if not atc_ok:
                    atc_ok = STATE.sim.set_atc_id(STATE.economy_registration)
            STATE.sim.enforce_equipment_gates(STATE.has_gps, STATE.has_autopilot)
        except Exception as e:
            STATE.log(f"Beladung/Ausrüstung: {e}")
        n_jobs = len(load.get("assignments") or [])
        details = "; ".join(str(x) for x in (applied.get("details") or []))
        STATE.log(
            f"Flug gestartet von {result.get('from_icao')} · "
            f"{STATE.aircraft_label or 'Flugzeug'} · "
            f"{n_jobs} Auftrag/Aufträge · "
            f"Fuel {load.get('fuel_gallons', '?')} gal · Payload {load.get('payload_kg', '?')} kg · "
            f"Sim fuel={'OK' if applied.get('fuel_ok') else 'FAIL'} "
            f"payload={'OK' if applied.get('payload_ok') else 'FAIL'} · "
            f"ATC ID {'OK' if atc_ok else 'Panel setzt Kennzeichen'} · "
            f"GPS={'ja' if STATE.has_gps else 'nein'} AP={'ja' if STATE.has_autopilot else 'nein'}"
        )
        if details:
            STATE.log(f"Sim-Load: {details}")
        if any("fuel=kept" in str(x) or "engines_running" in str(x) for x in (applied.get("details") or [])):
            STATE.log(
                "Triebwerk/APU liefen beim Start — Tanks nicht auf 0 gesetzt, Motor bleibt an."
            )
        if sim_fuel_unreliable():
            STATE.log(
                "Anti-Cheat: SimConnect-Tankwerte unzuverlässig — Treibstoff-Check aus "
                "(EFB/Cargo dürfen springen). Teleport, Simrate und Speed bleiben aktiv."
            )
        STATE.set_ui_status("Flug gestartet", "Flight started")
        if applied.get("gsx_refuel"):
            STATE._gsx_refuel = {
                "target_gal": float(load.get("fuel_gallons") or 0),
                "started_at": time.time(),
                "menu_at": 0.0,
                "menu_tries": 0,
                "pumping": False,
                "custom": bool(applied.get("custom_fuel")),
                "last_log": 0.0,
            }
            kg = float(load.get("fuel_gallons") or 0) * (LB_PER_GAL / 2.20462262)
            if applied.get("custom_fuel"):
                STATE.log(
                    f"Fenix/Payware: Fuel nicht direkt gesetzt ({kg:.0f} kg / "
                    f"{load.get('fuel_gallons')} gal). GSX-Tankwagen wird gerufen — "
                    "im EFB Mass & Balance Sollmenge eintragen, bei Schlauch "
                    "„Load Aircraft → GSX“ drücken."
                )
                STATE.set_ui_status("GSX tankt", "GSX is refueling")
            else:
                STATE.log(
                    "GSX-Tankwagen wird gerufen – Fuel kommt über den Schlauch "
                    "(Toolbar-Icon GSX einmal öffnen, falls der Wagen nicht kommt)."
                )
                STATE.set_ui_status("GSX tankt", "GSX is refueling")
        elif applied.get("custom_fuel"):
            kg = float(load.get("fuel_gallons") or 0) * (LB_PER_GAL / 2.20462262)
            STATE.log(
                f"Fenix/Payware: Fuel nicht direkt gesetzt (Soll {kg:.0f} kg). "
                "Im EFB Mass & Balance laden — am Gate zählt das nicht als Cheat."
            )
        # E7: fuel before/after the load, the sim's fuel system and capacity go to
        # the server with the next tracking ping (an old server ignores the field).
        try:
            after = STATE.sim.fuel_system_info()
        except Exception:
            after = {}
        STATE._fuel_report_due = {
            "stage": "after_load",
            "fuel_before_load_gal": (fuel_before or {}).get("fuel_total_gal"),
            "fuel_after_load_gal": (after or {}).get("fuel_total_gal"),
            "planned_gal": _finite(load.get("fuel_gallons"), 0.0),
            **{k: v for k, v in (after or {}).items() if k != "fuel_total_gal"},
        }
        save_flight_session()
        schedule_cockpit_ack("start")
        return result
    finally:
        STATE.sim_io_pause = False


def adopt_running_flight(existing: dict) -> bool:
    """'Flug starten' while the server already runs a flight: take it over from
    memory or from the session file – never with data of another flight (D1.7)."""
    fid = existing.get("id")
    if STATE.session_key and STATE.active_flight_id is not None and str(STATE.active_flight_id) == str(fid):
        pass  # the session is in memory already
    else:
        data = read_flight_session_file()
        if not data or str(data.get("flight_id")) != str(fid):
            if existing.get("session_key"):
                adopt_server_flight(existing)
                return True
            return False
        apply_flight_session(data, restored=True)
    STATE.active_flight_id = fid
    if not STATE.flight_started_at:
        STATE.flight_started_at = _parse_started_at(existing.get("started_at"))
    if not STATE.aircraft_label:
        # keep the stored load/fuel data – /flights/active carries none (D1.7)
        apply_flight_meta({"flight": existing, "aircraft": existing}, STATE.flight_started_at, keep_load=True)
    STATE.monitoring = True
    save_flight_session()
    STATE.restore_state = "restored"
    STATE.log(
        t_ui(
            "Laufender Flug übernommen – Aufzeichnung läuft weiter. Ende: Handbremse am Ziel.",
            "Running flight taken over – recording continues. End: parking brake at the destination.",
        )
    )
    STATE.set_ui_status("Flug übernommen", "Flight adopted")
    return True


def _ensure_end_session() -> str:
    """D1.5: after a restart the session may only be on disk – load it first."""
    with STATE.lock:
        key = STATE.session_key
    if key:
        return key
    data = read_flight_session_file()
    if data and (
        STATE.active_flight_id is None or str(data.get("flight_id")) == str(STATE.active_flight_id)
    ):
        apply_flight_session(data, restored=True)
        with STATE.lock:
            return str(STATE.session_key or "")
    if STATE.active_flight_id is not None and not STATE.monitoring:
        # the server flight is known, but neither memory nor disk has its session
        # – "Flug starten" cannot take it over either (review S8)
        raise RuntimeError(stranded_start_message())
    raise RuntimeError(
        t_ui(
            "Keine lokale Flug-Session. Bitte „Flug starten“ drücken (übernimmt den laufenden Flug), "
            "danach erneut loggen.",
            "No local flight session. Press “Start flight” (takes over the running flight), "
            "then log again.",
        )
    )


def _build_end_payload(session_key: str) -> tuple:
    raw = STATE.sim.read() if STATE.sim.connected else None
    if not raw:
        with STATE.lock:
            raw = dict(STATE.last_sample) if STATE.last_sample else None
    if not raw:
        raise RuntimeError(
            "Keine Sim-Daten – MSFS verbinden. Flugende: Handbremse, oder „Abbrechen“."
        )
    sample = make_sample(raw)
    gs_end = float(sample.get("ground_speed") or 0)
    agl_end = _agl_from(sample.get("alt_agl"))
    # Only when actually parked (not 25 kt on short final). Airliners with
    # custom tanks used to be forced on_ground up to 25 kt — that logged airborne.
    if gs_end <= PARK_GS_KT_AIRLINER and (
        bool(sample.get("parking_brake"))
        or bool(sample.get("on_ground_sim"))
        or agl_end is None
        or agl_end < PARK_AGL_FT
    ):
        sample["on_ground"] = True
    with STATE.lock:
        track = list(STATE.local_track)
        seq = STATE.track_seq
    if not sample_plausible(sample):
        prev = None
        with STATE.lock:
            prev = dict(STATE.last_sample) if STATE.last_sample else None
        if prev and sample_plausible(prev):
            sample = make_sample(prev)
        else:
            raise KeepFlyingError(
                t_ui(
                    "Position ungültig (Simulator lädt oder Menü). Nicht loggen – der Flug läuft weiter.",
                    "Invalid position (sim loading or menu). Do not log – the flight continues.",
                ),
                "BAD_POSITION",
            )
    track.append(sign_point(session_key, seq, sample))
    seq += 1
    while len(track) < MIN_TRACK_POINTS:
        time.sleep(0.02)
        track.append(sign_point(session_key, seq, sample))
        seq += 1
    before = len(track)
    track = compact_track(track, MAX_TRACK_POINTS_SEND)
    if before != len(track):
        STATE.log(f"Track gekürzt für Upload: {before} → {len(track)} Punkte")
    end_sample = {
        "latitude": track[-1]["latitude"],
        "longitude": track[-1]["longitude"],
        "altitude": track[-1].get("altitude"),
        "ground_speed": track[-1].get("ground_speed"),
        "heading": track[-1].get("heading"),
        "fuel_total": track[-1].get("fuel_total"),
        "on_ground": track[-1].get("on_ground", True),
        "simulation_rate": track[-1].get("simulation_rate", 1),
        "alt_agl": sample.get("alt_agl"),
    }
    payload = dict(end_sample)
    payload["track"] = track
    payload["session_key"] = session_key
    return payload, track, seq


def action_end(*, notify: bool = True, persist_retry: bool = False) -> dict:
    """Send the flight end. Only one at a time (D2); the payload is on disk before
    it is sent and is resent unchanged until the server confirms (D6).

    persist_retry=True only changes the answer on a temporary error ("queued" for
    the retry loop instead of "retry"); the session always stays until the server
    has decided (review S8: it used to be dropped here)."""
    if not _END_LOCK.acquire(timeout=0.2):
        return {
            "retry": True,
            "busy": True,
            "message": t_ui("Flugende wird gerade gesendet…", "Flight end is being sent…"),
            "retry_after_seconds": 4,
        }
    try:
        return _action_end(notify=notify, persist_retry=persist_retry)
    finally:
        _END_LOCK.release()


def _action_end(*, notify: bool, persist_retry: bool) -> dict:
    try:
        STATE.api.refresh_session()
    except Exception:
        pass
    if not STATE.api.token:
        raise RuntimeError("Nicht angemeldet")
    STATE.sim_io_pause = True
    try:
        session_key = _ensure_end_session()
        flight_id = STATE.active_flight_id
        record = pending_end_for(flight_id, session_key)
        track_extended = False
        if record:
            # Same end as before (sent already, answer lost or server down):
            # resend it unchanged – position and time of the real landing.
            payload = dict(record.get("payload") or {})
            track: list = list(payload.get("track") or [])
            seq = STATE.track_seq
        else:
            payload, track, seq = _build_end_payload(session_key)
            track_extended = True
            record = persist_pending_end(payload, flight_id)

        def keep_track() -> None:
            if track_extended:
                with STATE.lock:
                    STATE.local_track = track
                    STATE.track_seq = seq
                save_flight_session()

        try:
            result = STATE.api.submit_end(payload)
        except RetryableError as e:
            busy = isinstance(e, EndBusyError)
            update_pending_end(
                record,
                attempts=int(record.get("attempts") or 0) + 1,
                last_error=str(e),
                next_try_at=time.time() + max(5, int(e.retry_after or 15)),
            )
            if not persist_retry:
                return {
                    "retry": True,
                    "busy": busy,
                    "message": str(e),
                    "retry_after_seconds": 6 if busy else e.retry_after,
                }
            return {
                "queued": True,
                "message": (
                    "Server gerade nicht erreichbar – der Flug liegt lokal in der Warteschlange "
                    f"und wird automatisch nachgesendet ({e})."
                ),
                "retry_after_seconds": e.retry_after,
            }
        except AirportRejectedError as e:
            # Flight keeps running: restore the local track, tell the pilot, then
            # let them take off again toward a big airport.
            clear_pending_end()
            keep_track()
            STATE._park_ticks = 0
            STATE._auto_end_cooldown_until = time.time() + HEAVY_AIRPORT_COOLDOWN_SEC
            STATE.last_error = str(e)
            STATE.log(f"Landeplatz zu klein ({e.airport or '—'}) – Flug läuft weiter.")
            if notify:
                notify_windows(
                    t_ui(
                        "FlyConomy – Flughafen zu klein",
                        "FlyConomy – Airport too small",
                    ),
                    str(e),
                    code="FC-AIRPORT-TOO-SMALL",
                    kind="error",
                )
            return {"rejected": True, "keep_flying": True, "message": str(e), "airport": e.airport}
        except KeepFlyingError as e:
            code = getattr(e, "code", None) or "KEEP_FLYING"
            if code != "TOKEN":
                clear_pending_end()  # this payload cannot succeed; the next try builds anew
            keep_track()
            STATE.last_error = str(e)
            STATE.log(str(e))
            STATE._park_ticks = 0
            STATE._auto_end_cooldown_until = time.time() + AUTO_END_FAIL_COOLDOWN_SEC
            if notify:
                notify_windows(
                    t_ui("FlyConomy – Flug läuft weiter", "FlyConomy – Flight continues"),
                    str(e),
                    code=code,
                    kind="error",
                )
            return {"keep_flying": True, "message": str(e), "code": code}
        except FlightClosedError as e:
            clear_pending_end()
            msg = str(e)
            note_session_ended(session_key, flight_id)
            _clear_flight_runtime()
            STATE.last_error = msg
            STATE.log(f"Flug beendet (Server): {msg}")
            if notify:
                notify_windows(
                    t_ui("FlyConomy – Flug beendet", "FlyConomy – Flight ended"),
                    msg,
                    code="FC-END-REJECTED",
                    kind="error",
                )
            return {"rejected": True, "message": msg, "code": getattr(e, "code", "")}
        except NoActiveFlightError as e:
            clear_pending_end()
            gone = True
            try:
                gone = not STATE.api.get_active()
            except Exception:
                gone = False
            msg = t_ui(
                "Der Flug ist auf dem Server nicht mehr aktiv – vermutlich wurde er schon geloggt. "
                "Bitte auf der Website unter „Flüge“ nachsehen.",
                "The flight is no longer active on the server – it was probably logged already. "
                "Please check “Flights” on the website.",
            )
            STATE.last_error = msg
            STATE.log(f"{msg} ({e})")
            if gone:
                note_session_ended(session_key, flight_id)
                _clear_flight_runtime()
                return {"rejected": True, "unknown": True, "message": msg}
            keep_track()
            return {"keep_flying": True, "message": msg, "code": "FC-END-UNKNOWN"}
        except Exception as e:
            clear_pending_end()
            msg = str(e)
            low = msg.lower()
            permanent = any(
                x in low
                for x in (
                    "auszahlung gesperrt",
                    "signatur",
                    "rejected",
                    "lande-airport",
                )
            )
            still_active = None
            try:
                still_active = STATE.api.get_active()
            except Exception:
                still_active = True  # unknown – keep local if not clearly permanent
            if permanent or not still_active:
                note_session_ended(session_key, flight_id)
                _clear_flight_runtime()
                STATE.last_error = msg
                STATE.log(f"Flug beendet (Server): {msg}")
                if notify:
                    notify_windows(
                        t_ui("FlyConomy – Flug beendet", "FlyConomy – Flight ended"),
                        msg,
                        code="FC-END-REJECTED",
                        kind="error",
                    )
                return {"rejected": True, "message": msg}
            keep_track()
            raise

        clear_pending_end()
        note_session_ended(session_key, flight_id or result.get("flight_id"), result)
        _clear_flight_runtime()
        remember_end_result(result)
        STATE.set_ui_status("Flug beendet", "Flight ended")
        status = (result.get("validation") or {}).get("status") or (
            "bereits geloggt" if result.get("already_logged") else "?"
        )
        STATE.log(
            f"Flug beendet – Status {status} "
            f"Pay ${result.get('total_payment', 0)} · Track {len(track)} Punkte (signiert)"
        )
        if notify and not result.get("queued"):
            notify_flight_logged(result)
            schedule_cockpit_ack("log")
        return result
    finally:
        STATE.sim_io_pause = False


def action_cancel() -> dict:
    result: dict = {}
    try:
        result = STATE.api.cancel_flight() or {}
    except Exception as e:
        result = {"error": str(e)}
    if isinstance(result, dict) and str(result.get("code") or "") == "END_BUSY":
        # Server stage C2: an /end of this flight is being processed right now, so the flight
        # was NOT cancelled. Keep the queued end and the session – the end goes on (or the
        # queued end is resent later); never show "Flug abgebrochen" for a flight that may be paid.
        msg = t_ui(
            "Das Flugende wird gerade verarbeitet – Abbrechen ist im Moment nicht möglich. "
            "Bitte kurz warten.",
            "The flight end is being processed right now – cancelling is not possible at the moment. "
            "Please wait a moment.",
        )
        STATE.log(msg)
        return {"ok": False, "code": "END_BUSY", "error": msg, "flight_id": result.get("flight_id")}
    key = current_end_session_key()
    record = pending_end_for(STATE.active_flight_id, key or STATE.session_key)
    # Review S8: remembered first, so a flush that is sending right now can never
    # write this end back to disk (update_pending_end checks it).
    note_session_ended(key, STATE.active_flight_id)
    if record:
        note_session_ended((record.get("payload") or {}).get("session_key"), record.get("flight_id"))
        clear_pending_end()  # a cancelled flight has no end to resend
    if STATE.pending_start or file_or_bak_exists(PENDING_START_PATH):
        clear_pending_start()  # "Abbrechen" also stops a start that is still waiting
    _clear_flight_runtime()
    STATE.set_ui_status("Bereit", "Ready")
    STATE.log("Flug abgebrochen / Client-Session bereinigt")
    if result.get("error") and "no" in str(result.get("error")).lower():
        return {"ok": True, "message": "Client-Session bereinigt (kein Server-Flug)"}
    return result or {"ok": True, "message": "Flug abgebrochen"}


def action_request_livery() -> dict:
    if not STATE.api.token:
        raise RuntimeError("Nicht angemeldet")
    raw = dict(STATE.last_sample) if STATE.last_sample else {}
    title = str(raw.get("aircraft_type") or "").strip()
    if not title and STATE.sim.connected:
        fresh = STATE.sim.read()
        if fresh:
            raw = fresh
            title = str(raw.get("aircraft_type") or "").strip()
    if not title:
        raise RuntimeError(
            "Kein Livery-Name aus MSFS (TITLE). Bitte im Flugzeug sitzen und MSFS verbinden."
        )
    payload = {
        "title": title,
        "atc_model": raw.get("atc_model") or "",
        "atc_type": raw.get("atc_type") or "",
        "atc_id": raw.get("atc_id_sim") or STATE.economy_registration or "",
        "icao": STATE.current_icao or "",
        "lat": raw.get("latitude"),
        "lon": raw.get("longitude"),
    }
    result = STATE.api.request_livery(payload)
    STATE.log(result.get("message") or "Livery-Anfrage gesendet")
    return result


def _economy_fuel_gal() -> float:
    load = STATE.load_info or {}
    try:
        gal = float(load.get("fuel_gallons") or 0)
    except (TypeError, ValueError):
        gal = 0.0
    if gal > 0.05:
        return gal
    try:
        mf = STATE.api.get_myflight()
        ac = ((mf or {}).get("aircraft") or [None])[0] or {}
        preview = (mf or {}).get("load_preview") or (mf or {}).get("load_state") or {}
        return float(preview.get("fuel_gallons") or ac.get("fuel_gallons") or 0)
    except Exception:
        return 0.0


def _economy_load_dict() -> dict:
    load = dict(STATE.load_info or {})
    if load.get("fuel_gallons"):
        return load
    try:
        mf = STATE.api.get_myflight()
        preview = (mf or {}).get("load_preview") or (mf or {}).get("load_state") or {}
        ac = ((mf or {}).get("aircraft") or [None])[0] or {}
        if preview:
            load = {**load, **preview}
        if ac and not load.get("sim_title"):
            load["sim_title"] = f"{ac.get('make') or ''} {ac.get('model') or ''}".strip()
        if ac and not load.get("fuel_gallons"):
            load["fuel_gallons"] = ac.get("fuel_gallons")
            load["fuel_capacity"] = ac.get("fuel_capacity")
    except Exception:
        pass
    return load


def ingest_myflight(mf: dict) -> dict:
    """Compact My Flight snapshot for the in-sim panel."""
    rows = (mf or {}).get("aircraft") or []
    ac = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    loaded = (mf or {}).get("loaded_assignments") or []
    locked = (mf or {}).get("locked_assignments") or []
    assignments = (mf or {}).get("assignments") or []
    preview = (mf or {}).get("load_preview") or (mf or {}).get("load_state") or {}
    ft_n = int((ac or {}).get("fuel_type") or 0) if ac else 0
    summary = {
        "aircraft": None,
        "jobs_loaded": len(loaded),
        "jobs_locked": len(locked) if locked else max(0, len(assignments) - len(loaded)),
        "pax": int(preview.get("passengers") or 0),
        "seats_used": int(preview.get("seats_used") or 0),
        "seats": int(preview.get("seats_total") or (ac or {}).get("seats") or 0),
        "cargo_kg": float(preview.get("cargo_kg") or 0),
    }
    if ac:
        summary["aircraft"] = {
            "id": ac.get("id"),
            "registration": ac.get("registration"),
            "make": ac.get("make"),
            "model": ac.get("model"),
            "location": ac.get("location"),
            "fuel_gallons": float(ac.get("fuel_gallons") or 0),
            "fuel_capacity": float(ac.get("fuel_capacity") or 0),
            "fuel_type": "jeta" if ft_n == 1 else "100ll",
            "fuel_type_n": ft_n,
        }
        reg = str(ac.get("registration") or "").strip().upper()
        if reg:
            STATE.registration = reg
            STATE.economy_registration = reg
            make = str(ac.get("make") or "").strip()
            model = str(ac.get("model") or "").strip()
            STATE.aircraft_label = " ".join(x for x in (reg, make, model) if x)
    else:
        if not STATE.active_flight_id:
            STATE.registration = None
            STATE.economy_registration = None
            STATE.aircraft_label = ""
    STATE._myflight = summary
    STATE.crew_guest = str((mf or {}).get("crew_role") or "") == "crew"
    return summary


def hotkey_fire(_slot: str = "start") -> None:
    """Start or log the flight from a bound key – runs without the panel open."""
    if STATE.sim_io_pause:
        return
    try:
        if STATE.active_flight_id:
            from flight_log_ui import start_flight_log_ui

            blocked = local_end_block()
            if blocked:
                code, msg = blocked
                STATE.last_error = msg
                STATE.log(f"Hotkey: {msg}")
                notify_windows(
                    t_ui("FlyConomy – Flug läuft weiter", "FlyConomy – Flight continues"),
                    msg,
                    code=code,
                    kind="error",
                )
                return
            start_flight_log_ui()
        else:
            action_start()
    except Exception as e:
        STATE.last_error = str(e)
        STATE.log(f"Hotkey: {e}")
        notify_windows(
            t_ui("FlyConomy – Fehler", "FlyConomy – Error"),
            str(e),
            code="FC-HOTKEY",
            kind="error",
        )


def process_ground_queue(allow_fire: bool = True) -> None:
    try:
        GROUND.tick_cleanup()
        if STATE.sim.connected and STATE.sim.sm:
            GROUND.attach(STATE.sim.sm)
        for item in GROUND.drain():
            kind = item[0]
            if kind == "captured":
                slot, defn = item[1], item[2]
                GROUND.set_bind(slot, defn)
                STATE.config.set_hotkey(slot, defn)
                GROUND.play_chime()
                from sim_ground import label_for_definition

                lab = label_for_definition(defn)
                STATE.log(f"Taste für Start/Loggen: {lab}")
                STATE.set_ui_status(f"Start/Loggen: {lab}", f"Start/Log: {lab}")
            elif kind == "fire" and allow_fire:
                hotkey_fire(item[1] if len(item) > 1 else "start")
    except Exception as e:
        print("ground queue", e)


GROUND.fire_hook = hotkey_fire
GROUND.start_watch()


def action_hotkey(body: dict) -> dict:
    action = str(body.get("action") or "").strip().lower()
    slot = "end" if str(body.get("slot") or "") == "end" else "start"
    if STATE.sim.connected and STATE.sim.sm:
        GROUND.attach(STATE.sim.sm)
    GROUND.start_def = STATE.config.hotkey_start
    GROUND.end_def = STATE.config.hotkey_end
    GROUND.volume = STATE.config.hotkey_volume
    if action == "listen":
        GROUND.begin_listen(slot)
        msg = t_ui(
            "Taste drücken (oder halten und zweite Taste)",
            "Press a key (or hold and press a second key)",
        )
        STATE.set_ui_status(msg, msg)
        return {"ok": True, "message": msg, "hotkeys": GROUND.snapshot()}
    if action == "cancel":
        GROUND.cancel_listen()
        return {"ok": True, "hotkeys": GROUND.snapshot()}
    if action == "clear":
        GROUND.clear_bind(slot)
        STATE.config.set_hotkey(slot, "")
        GROUND.apply_hotkeys()
        return {"ok": True, "hotkeys": GROUND.snapshot()}
    if action == "volume":
        vol = body.get("volume")
        if vol is None:
            vol = body.get("pct")
        pct = float(vol if vol is not None else 35)
        if pct > 1.5:
            pct = pct / 100.0
        GROUND.volume = max(0.0, min(1.0, pct))
        STATE.config.set_hotkey_volume(GROUND.volume)
        GROUND.play_chime()
        return {"ok": True, "hotkeys": GROUND.snapshot()}
    raise RuntimeError(t_ui("Unbekannte Hotkey-Aktion", "Unknown hotkey action"))


def _ground_pose() -> tuple[float, float, float]:
    sample = dict(STATE.last_sample or {})
    lat = float(sample.get("latitude") or 0)
    lon = float(sample.get("longitude") or 0)
    if abs(lat) < 0.01 and abs(lon) < 0.01 and STATE.sim.connected:
        raw = STATE.sim.read()
        if raw:
            sample = raw
            lat = float(raw.get("latitude") or 0)
            lon = float(raw.get("longitude") or 0)
    if abs(lat) < 0.01 and abs(lon) < 0.01:
        raise RuntimeError(t_ui("Keine Position", "No position"))
    return lat, lon, heading_deg(sample.get("heading"))


def action_spawn(kind: str) -> dict:
    key = str(kind or "").strip().lower()
    if not STATE.sim.connected or not STATE.sim.sm:
        raise RuntimeError(t_ui("MSFS nicht verbunden", "MSFS not connected"))
    GROUND.attach(STATE.sim.sm)
    if key in ("clear", "remove", "despawn"):
        n = GROUND.clear_all()
        msg = t_ui(f"{n} Objekte entfernt", f"Removed {n} objects")
        STATE.set_ui_status(msg, msg)
        STATE.log(msg)
        return {"ok": True, "message": msg, "removed": n}
    if key not in SPAWN_CATALOG:
        raise RuntimeError(t_ui("Unbekanntes Objekt", "Unknown object"))
    lat, lon, hdg = _ground_pose()
    result = GROUND.spawn(key, lat, lon, hdg, 0.0)
    label = result.get("label") or key
    msg = t_ui(f"{label} unterwegs", f"{label} spawned")
    STATE.set_ui_status(msg, msg)
    STATE.log(msg)
    return {"ok": True, "message": msg, **result}


def action_gsx(action: str) -> dict:
    if not STATE.sim.connected:
        raise RuntimeError(t_ui("MSFS nicht verbunden", "MSFS not connected"))
    if not STATE.sim.gsx_running():
        raise RuntimeError(t_ui("GSX läuft nicht", "GSX is not running"))
    key = str(action or "").strip().lower()
    if key in ("call_truck", "fuel_truck"):
        ok = STATE.sim.gsx_call_fuel_truck()
        msg = t_ui("Tankwagen unterwegs", "Fuel truck on the way")
        STATE.set_ui_status(msg, msg)
        STATE.log("GSX: Tankwagen")
        return {"ok": bool(ok), "message": msg}
    if key not in GSX_MENU_INDEX:
        raise RuntimeError(t_ui("Unbekannte GSX-Aktion", "Unknown GSX action"))
    ok = STATE.sim.gsx_menu_choice(GSX_MENU_INDEX[key])
    msg = t_ui(f"GSX: {key}", f"GSX: {key}")
    STATE.set_ui_status(msg, msg)
    STATE.log(f"GSX-Menü {key}")
    return {"ok": bool(ok), "message": msg}


def action_fuel_desk() -> dict:
    if not STATE.api.token:
        raise RuntimeError(t_ui("Nicht angemeldet", "Not signed in"))
    mf = STATE.api.get_myflight()
    summary = ingest_myflight(mf)
    ac = summary.get("aircraft") or {}
    if not ac.get("id"):
        raise RuntimeError(t_ui("Kein Flugzeug gelockt", "No aircraft locked"))
    icao = str(ac.get("location") or STATE.current_icao or "").upper()
    if not icao:
        raise RuntimeError(t_ui("Kein ICAO", "No ICAO"))
    sellers = STATE.api.get_fuel_sellers(icao, ac.get("fuel_type") or "100ll")
    return {
        "ok": True,
        "icao": icao,
        "aircraft": ac,
        "jobs_loaded": summary.get("jobs_loaded") or 0,
        "pax": summary.get("pax") or 0,
        "seats": summary.get("seats") or 0,
        "gsx": bool(STATE.sim.gsx_running()) if STATE.sim.connected else False,
        **sellers,
    }


def assert_refuel_stationary() -> None:
    """Refuel is allowed in an active flight, but the aircraft must be stopped."""
    if not STATE.sim.connected:
        return
    sample = STATE.last_sample or {}
    gs = float(sample.get("ground_speed") or 0)
    if gs > REFUEL_STATIONARY_GS_KT:
        raise RuntimeError(
            t_ui(
                "Flugzeug muss stehen (max. 6 kt), um zu tanken",
                "Aircraft must be stopped (max 6 kt) to refuel",
            )
        )


def slow_fill_duration_sec(capacity_gal: float, delta_gal: float) -> float:
    """Half-realistic hose time: Caravan 0–100% = 3 min, A320 ≈ 7.5 min, cap 10 min."""
    cap = max(1.0, float(capacity_gal or 0) or 1.0)
    amt = min(cap, abs(float(delta_gal) or 0))
    if cap <= CARAVAN_CAP_GAL:
        t_full = max(90.0, CARAVAN_FULL_SEC * (cap / CARAVAN_CAP_GAL))
    elif cap <= A320_CAP_GAL:
        f = (cap - CARAVAN_CAP_GAL) / (A320_CAP_GAL - CARAVAN_CAP_GAL)
        t_full = CARAVAN_FULL_SEC + f * (A320_FULL_SEC - CARAVAN_FULL_SEC)
    else:
        extra = math.log(max(1.01, cap / A320_CAP_GAL)) / math.log(4.0)
        t_full = min(
            MAX_FULL_FILL_SEC,
            A320_FULL_SEC + extra * (MAX_FULL_FILL_SEC - A320_FULL_SEC),
        )
    return max(8.0, t_full * (amt / cap))


def _mark_refuel_grace() -> None:
    STATE._refuel_grace_until = time.time() + 12.0


def action_economy_refuel(body: dict) -> dict:
    """Charge FlyConomy, then load the new fuel into the sim (GSX or direct)."""
    assert_refuel_stationary()
    if not STATE.api.token:
        raise RuntimeError(t_ui("Nicht angemeldet", "Not signed in"))
    gallons = float(body.get("gallons") or 0)
    if gallons <= 0.05:
        raise RuntimeError(t_ui("Menge ungültig", "Invalid amount"))
    aircraft_id = body.get("aircraft_id")
    fuel_type = str(body.get("fuel_type") or "jeta").lower()
    operation = str(body.get("operation") or "refuel").lower()
    if operation not in ("refuel", "defuel"):
        operation = "refuel"
    payload: Dict[str, Any] = {
        "aircraft_id": int(aircraft_id),
        "fuel_type": "jeta" if fuel_type in ("jeta", "1", "jet-a") else "100ll",
        "gallons": gallons,
        "operation": operation,
    }
    if body.get("fbo_id") and operation == "refuel":
        payload["fbo_id"] = int(body["fbo_id"])
    result = STATE.api.refuel(payload)
    if operation == "defuel":
        refund = result.get("refund")
        who = result.get("airline_name") or result.get("credited_account") or "pilot"
        if refund is not None:
            STATE.log(f"Defuel-Rückkauf ${float(refund):.0f} → {who}")
    new_level = float(result.get("new_fuel_level") or 0)
    sim_mode = str(body.get("sim_mode") or STATE.config.fuel_mode or "auto").lower()
    pump_pace = str(body.get("pump_pace") or "").strip().lower()
    gsx_up = bool(STATE.sim.connected and STATE.sim.gsx_running())
    use_gsx = False
    if sim_mode == "gsx":
        use_gsx = gsx_up
    elif sim_mode == "direct":
        use_gsx = False
    else:
        use_gsx = gsx_up
    sim_applied = False
    if STATE.sim.connected and new_level >= 0:
        current = 0.0
        try:
            raw = STATE.sim._get("FUEL_TOTAL_QUANTITY")
            current = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            current = float((STATE.last_sample or {}).get("fuel_total") or 0)
        cap = float(
            (STATE.load_info or {}).get("fuel_capacity")
            or ((STATE._myflight or {}).get("aircraft") or {}).get("fuel_capacity")
            or 0
        )
        _mark_refuel_grace()
        if use_gsx:
            try:
                STATE.sim.gsx_call_fuel_truck()
            except Exception as e:
                STATE.log(f"GSX-Menü: {e}")
            STATE._gsx_refuel = {
                "target_gal": new_level,
                "started_at": time.time(),
                "menu_at": time.time() if gsx_up else 0.0,
                "menu_tries": 1 if gsx_up else 0,
                "pumping": not gsx_up,
                "pump_at": time.time(),
                "pump_from": current,
                "custom": False,
                "last_log": 0.0,
                "rate": 40.0,
                "economy": True,
            }
            sim_applied = True
        elif sim_mode == "direct" and pump_pace != "fast":
            duration = slow_fill_duration_sec(cap, new_level - current)
            STATE._gsx_refuel = {
                "target_gal": new_level,
                "started_at": time.time(),
                "menu_at": 0.0,
                "menu_tries": 0,
                "pumping": True,
                "pump_at": time.time(),
                "pump_from": current,
                "custom": False,
                "last_log": 0.0,
                "pace": "slow",
                "duration_sec": duration,
                "economy": True,
                "truck_id": None,
                "truck_eta": 0.0,
            }
            sim_applied = True
            STATE.log(
                f"Langsames Tanken {duration:.0f}s → {new_level:.1f} gal"
            )
        else:
            STATE.sim._set_absolute_fuel(new_level)
            sim_applied = True
        try:
            mf = STATE.api.get_myflight()
            ingest_myflight(mf)
        except Exception:
            pass
    paid = result.get("paid_by") or "pilot"
    STATE.log(
        f"Economy-Tanken {operation} {gallons:.1f} gal → {new_level:.1f} gal "
        f"(zahler={paid}, sim={'gsx' if use_gsx else 'direct'})"
    )
    msg = result.get("message") or t_ui("Tankvorgang erfolgreich", "Refuel ok")
    STATE.set_ui_status(msg, msg)
    return {
        "ok": True,
        "message": msg,
        "result": result,
        "new_fuel_level": new_level,
        "sim_mode": "gsx" if use_gsx else "direct",
        "sim_applied": sim_applied,
        "gsx": gsx_up,
        "paid_by": paid,
    }


def apply_load_blocked_reason() -> Optional[str]:
    """D11: no tank/payload writes once a flight has left the gate.

    At the gate (flight started, still parked) only the payload may be pushed
    again – never fuel; after departure nothing at all."""
    if not (STATE.monitoring or STATE.active_flight_id):
        return None
    sample = STATE.last_sample or {}
    gs = _finite(sample.get("ground_speed"))
    if (
        STATE._was_airborne
        or float(STATE._max_nm_from_origin or 0) >= 1.0
        or not bool(sample.get("on_ground", True))
        or gs > REFUEL_STATIONARY_GS_KT
    ):
        return t_ui(
            "„Ins EFB laden“ ist während des Flugs gesperrt.",
            "“Load to EFB” is locked during the flight.",
        )
    return None


def action_apply_load() -> dict:
    """Push FlyConomy pax/fuel/payload into the sim (Fenix EFB / SimConnect)."""
    if not STATE.sim.connected:
        raise RuntimeError(t_ui("MSFS nicht verbunden", "MSFS not connected"))
    blocked = apply_load_blocked_reason()
    if blocked:
        raise RuntimeError(blocked)
    load = _economy_load_dict()
    if not load:
        raise RuntimeError(t_ui("Keine Beladung", "No load data"))
    in_flight = bool(STATE.monitoring or STATE.active_flight_id)
    custom = False
    try:
        custom = STATE.sim.has_custom_fuel(str(load.get("sim_title") or ""))
    except Exception:
        custom = False
    # Started but still at the gate: payload only (custom-fuel types never get
    # tank writes anyway; for all others repair=True skips the fuel).
    applied = STATE.sim.apply_economy_load(load, repair=bool(in_flight and not custom))
    if STATE.economy_registration:
        STATE.sim.set_atc_id(STATE.economy_registration)
    STATE.set_ui_status("Beladung ins Flugzeug", "Load applied to aircraft")
    STATE.log("FlyConomy-Daten ins Flugzeug/EFB geschrieben")
    return {"ok": True, "result": applied, "message": t_ui("Daten übernommen", "Data applied")}


def action_fuel(kind: str) -> dict:
    """Manual fuel tools from My Flight (GSX truck, slow/fast, direct, match)."""
    if not STATE.sim.connected:
        raise RuntimeError(t_ui("MSFS nicht verbunden", "MSFS not connected"))
    kind = str(kind or "").strip().lower()
    target = _economy_fuel_gal()
    if kind == "call_truck":
        if not STATE.sim.gsx_running():
            raise RuntimeError(t_ui("GSX läuft nicht", "GSX is not running"))
        ok = STATE.sim.gsx_call_fuel_truck()
        STATE.set_ui_status("Tankwagen unterwegs", "Fuel truck on the way")
        STATE.log("GSX: Tankwagen manuell angefordert")
        return {
            "ok": bool(ok),
            "message": t_ui("Tankwagen unterwegs", "Fuel truck on the way"),
        }
    if kind == "direct":
        if target <= 0.05:
            raise RuntimeError(t_ui("Keine Sollmenge", "No target fuel"))
        STATE.sim._set_absolute_fuel(target)
        STATE.set_ui_status("Tank gesetzt", "Fuel set")
        STATE.log(f"Direkt getankt: {target:.1f} gal")
        return {"ok": True, "message": t_ui("Tank gesetzt", "Fuel set")}
    if kind == "match":
        load = _economy_load_dict()
        if not load:
            raise RuntimeError(t_ui("Keine Beladung", "No load data"))
        applied = STATE.sim.apply_economy_load(load)
        STATE.set_ui_status("Tank angepasst", "Fuel matched")
        STATE.log("Tank an FlyConomy-Sollmenge angepasst")
        return {
            "ok": bool(applied.get("fuel_ok")),
            "message": t_ui("Tank angepasst", "Fuel matched"),
            "result": applied,
        }
    if kind in ("slow", "fast"):
        if target <= 0.05:
            raise RuntimeError(t_ui("Keine Sollmenge", "No target fuel"))
        rate = 8.0 if kind == "slow" else 90.0
        use_gsx = STATE.sim.gsx_running()
        if use_gsx:
            try:
                STATE.sim.gsx_call_fuel_truck()
            except Exception as e:
                STATE.log(f"GSX-Menü: {e}")
        STATE._gsx_refuel = {
            "target_gal": target,
            "started_at": time.time(),
            "menu_at": time.time() if use_gsx else 0.0,
            "menu_tries": 1 if use_gsx else 0,
            "pumping": not use_gsx,
            "pump_at": time.time(),
            "custom": False,
            "last_log": 0.0,
            "rate": rate,
            "pace": "slow" if kind == "slow" else "fast",
            "truck_id": None,
            "truck_eta": 0.0,
        }
        msg = t_ui("Tanken läuft", "Refueling")
        STATE.set_ui_status(msg, msg)
        STATE.log(f"Manuelles Tanken ({kind}) Ziel {target:.1f} gal")
        return {"ok": True, "message": msg}
    raise RuntimeError(t_ui("Unbekannte Tank-Aktion", "Unknown fuel action"))


def _finish_gsx_refuel(reason: str) -> None:
    job = STATE._gsx_refuel
    if not job:
        return
    tid = job.get("truck_id")
    if tid:
        try:
            plat, plon, phdg = _ground_pose()
            GROUND.send_away(int(tid), plat, plon, phdg, 0.0)
        except Exception as e:
            STATE.log(f"Tankwagen Abfahrt: {e}")
    target = float(job.get("target_gal") or 0)
    custom = bool(job.get("custom"))
    if not custom:
        try:
            STATE.sim._fill_fuel_wings_first(target, zero_first=False)
        except Exception as e:
            STATE.log(f"GSX-Fuel Abschluss fehlgeschlagen ({reason}): {e}")
            STATE._gsx_refuel = None
            return
        STATE.log(f"GSX-Betankung fertig ({reason}) – {target:.0f} gal")
        STATE.set_ui_status("Tank voll", "Fuel complete")
    else:
        kg = target * (LB_PER_GAL / 2.20462262)
        STATE.log(
            f"GSX/Fenix: nicht weiter gepumpt ({reason}). "
            f"Sollmenge {kg:.0f} kg — EFB „Load Aircraft“ wenn noch nicht geladen."
        )
    _mark_refuel_grace()
    STATE._gsx_refuel = None
    if reason in ("target", "timeout", "no_progress"):
        STATE.set_ui_status("", "")


def tick_gsx_refuel(sample: Optional[dict]) -> None:
    """Call the GSX fuel truck on heavies, then pump economy fuel through the hose."""
    job = STATE._gsx_refuel
    if not job or not STATE.sim.connected:
        return
    now = time.time()
    target = float(job.get("target_gal") or 0)
    dur = float(job.get("duration_sec") or 0)
    if job.get("pace") == "slow" and dur > 0:
        t0 = float(job.get("pump_at") or job.get("started_at") or now)
        if (now - t0) >= dur:
            _finish_gsx_refuel("target")
            return
    gs = float((sample or {}).get("ground_speed") or 0)
    airborne = not bool((sample or {}).get("on_ground", True))
    gs_limit = (
        REFUEL_STATIONARY_GS_KT
        if job.get("economy") or job.get("pace")
        else GATE_LOADING_GS_KT
    )
    if gs > gs_limit:
        _finish_gsx_refuel("taxi")
        return
    if airborne and gs > max(8.0, gs_limit):
        _finish_gsx_refuel("taxi")
        return
    if job.get("custom"):
        timeout = LOADING_WINDOW_SEC
    elif job.get("pace") == "slow":
        extra = 45.0 if job.get("truck_id") else 0.0
        timeout = min(
            MAX_FULL_FILL_SEC + 90.0,
            max(90.0, float(job.get("duration_sec") or 0) + 60.0 + extra),
        )
    else:
        timeout = GSX_REFUEL_TIMEOUT_SEC
    if now - float(job.get("started_at") or now) > timeout:
        _finish_gsx_refuel("timeout")
        return

    current = float((sample or {}).get("fuel_total") or 0)
    if current < 0:
        try:
            raw = STATE.sim._get("FUEL_TOTAL_QUANTITY")
            current = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            current = 0.0
    if abs(current - target) <= 1.0:
        _finish_gsx_refuel("target")
        return

    if job.get("truck_id") and not job.get("pumping"):
        if now < float(job.get("truck_eta") or 0):
            return
        job["pumping"] = True
        job["pump_at"] = now
        job["pump_from"] = current
        STATE.log("Tankwagen steht – Tanken beginnt")

    if not job.get("pumping"):
        last_menu = float(job.get("menu_at") or 0)
        tries = int(job.get("menu_tries") or 0)
        if STATE.sim.gsx_refuel_ready():
            job["pumping"] = True
            job["pump_at"] = now
            job["pump_from"] = current
            if job.get("custom"):
                kg = target * (LB_PER_GAL / 2.20462262)
                STATE.log(
                    f"GSX-Schlauch verbunden — Fenix-EFB: Load Aircraft → GSX "
                    f"(Soll {kg:.0f} kg)."
                )
            else:
                STATE.log("GSX-Schlauch verbunden – betanke auf Sollmenge…")
        elif last_menu == 0 or (tries < 3 and now - last_menu >= 18):
            try:
                STATE.sim.gsx_call_fuel_truck()
                job["menu_at"] = now
                job["menu_tries"] = tries + 1
                if tries == 0:
                    STATE.log("GSX: Tankwagen angefordert.")
            except Exception as e:
                STATE.log(f"GSX-Menü: {e}")
        return

    if job.get("custom"):
        if now - float(job.get("last_log") or 0) >= 20:
            job["last_log"] = now
            kg = target * (LB_PER_GAL / 2.20462262)
            STATE.log(
                f"Warte auf Fenix-EFB-Uplift… sim {current:.0f} gal / Soll {target:.0f} gal "
                f"({kg:.0f} kg)"
            )
        return

    if job.get("pace") == "slow" and float(job.get("duration_sec") or 0) > 0:
        dur = float(job["duration_sec"])
        t0 = float(job.get("pump_at") or job.get("started_at") or now)
        frm = float(job["pump_from"]) if job.get("pump_from") is not None else current
        frac = min(1.0, max(0.0, (now - t0) / max(0.5, dur)))
        nxt = frm + (target - frm) * frac
        if frac >= 1.0 or abs(nxt - target) < 0.4:
            _finish_gsx_refuel("target")
            return
        try:
            STATE.sim._fill_fuel_wings_first(nxt, zero_first=False)
        except Exception as e:
            STATE.log(f"Pumpe: {e}")
            return
        if now - float(job.get("last_log") or 0) >= 8:
            job["last_log"] = now
            STATE.log(f"Tanke {int(round(frac * 100))}% … {nxt:.0f}/{target:.0f} gal")
        return

    if (
        now - float(job.get("pump_at") or now) >= 28
        and abs(current - float(job.get("pump_from") or current)) < 2.0
    ):
        STATE.log(
            "GSX: Sim-Tanks übernehmen die Pumpe nicht (typisch Fenix/PMDG-EFB) – "
            "setze Sollmenge direkt."
        )
        _finish_gsx_refuel("no_progress")
        return

    remain = target - current
    rate = float(job.get("rate") or 0) or min(80.0, max(18.0, abs(remain) / 40.0))
    if remain >= 0:
        nxt = min(target, current + rate)
    else:
        nxt = max(target, current - rate)
    try:
        STATE.sim._fill_fuel_wings_first(nxt, zero_first=False)
    except Exception as e:
        STATE.log(f"GSX-Pumpe: {e}")
        return
    if now - float(job.get("last_log") or 0) >= 12:
        job["last_log"] = now
        pct = (nxt / target * 100.0) if target > 0 else 100.0
        STATE.log(f"GSX tankt… {nxt:.0f}/{target:.0f} gal ({pct:.0f}%)")


def enqueue_gateway_command(msg: dict) -> None:
    item = {
        "id": str(msg.get("id") or ""),
        "action": str(msg.get("action") or ""),
        "payload": msg.get("payload") if isinstance(msg.get("payload"), dict) else {},
    }
    try:
        STATE.cmd_queue.put_nowait(item)
    except queue.Full:
        GATEWAY.send_result(
            str(item["id"]),
            ok=False,
            error="queue_full",
            data={
                "code": "QUEUE_FULL",
                "message": t_ui("Client ist beschäftigt", "Client is busy"),
            },
        )


def execute_gateway_command(cmd: dict, start_result: Optional[tuple] = None) -> Optional[tuple]:
    cmd_id = str(cmd.get("id") or "")
    action = str(cmd.get("action") or "")
    payload = cmd.get("payload") if isinstance(cmd.get("payload"), dict) else {}
    try:
        if action == "start":
            if start_result is not None:
                ok, data, err = start_result
                GATEWAY.send_result(cmd_id, ok=ok, data=data, error=err)
                return start_result
            data = action_start()
            queued = bool(isinstance(data, dict) and data.get("queued"))
            GATEWAY.send_result(
                cmd_id,
                ok=not queued,
                data=data,
                error=None if not queued else str((data or {}).get("message") or "pending_start"),
            )
            return (not queued, data, None if not queued else str((data or {}).get("message") or "pending_start"))
        if action == "end":
            from flight_log_ui import start_flight_log_ui

            blocked = local_end_block()
            if blocked:
                code, msg = blocked
                STATE.last_error = msg
                notify_windows(
                    t_ui("FlyConomy – Flug läuft weiter", "FlyConomy – Flight continues"),
                    msg,
                    code=code,
                    kind="error",
                )
                GATEWAY.send_result(
                    cmd_id,
                    ok=False,
                    error=msg,
                    data={"keep_flying": True, "code": code, "message": msg},
                )
                return start_result
            data = start_flight_log_ui()
            GATEWAY.send_result(cmd_id, ok=True, data=data)
            return start_result
        if action == "cancel":
            data = action_cancel()
            GATEWAY.send_result(cmd_id, ok=True, data=data)
            return start_result
        if action == "connect_sim":
            ok, msg = STATE.sim.connect()
            GATEWAY.send_result(cmd_id, ok=ok, data={"ok": ok, "message": msg}, error=None if ok else msg)
            return start_result
        if action == "fuel_desk":
            data = action_fuel_desk()
            GATEWAY.send_result(cmd_id, ok=True, data=data)
            return start_result
        if action == "logs":
            GATEWAY.send_result(cmd_id, ok=True, data={"logs": STATE.log_lines[-50:]})
            return start_result
        if action == "locale":
            loc = str(payload.get("locale") or "").strip().lower()
            if loc not in ("de", "en"):
                raise RuntimeError("locale must be de or en")
            STATE.config.set_locale(loc)
            GATEWAY.send_result(cmd_id, ok=True, data={"locale": loc})
            return start_result
        if action == "fuel_mode":
            mode = str(payload.get("mode") or payload.get("fuel_mode") or "auto")
            STATE.config.set_fuel_mode(mode)
            GATEWAY.send_result(
                cmd_id,
                ok=True,
                data={
                    "ok": True,
                    "fuel_mode": STATE.config.fuel_mode,
                    "message": t_ui("Tanken gespeichert", "Fuel mode saved"),
                },
            )
            return start_result
        if action == "fuel_action":
            data = action_fuel(str(payload.get("action") or payload.get("kind") or ""))
            GATEWAY.send_result(cmd_id, ok=True, data=data)
            return start_result
        raise RuntimeError(f"unknown action {action}")
    except VersionGateError as e:
        # The website opens its update dialog from these flags (like bridge /start 409).
        data = {"ok": False, "code": "FC-OUTDATED", "message": str(e), **(e.info or {})}
        GATEWAY.send_result(cmd_id, ok=False, error=str(e), data=data)
        if action == "start":
            return (False, data, str(e))
        return start_result
    except Exception as e:
        GATEWAY.send_result(cmd_id, ok=False, error=str(e))
        if action == "start":
            return (False, None, str(e))
        return start_result


def drain_gateway_commands() -> None:
    items: list[dict] = []
    while True:
        try:
            items.append(STATE.cmd_queue.get_nowait())
        except queue.Empty:
            break
    if not items:
        return
    starts = [x for x in items if str(x.get("action") or "") == "start"]
    rest = [x for x in items if str(x.get("action") or "") != "start"]
    dropped = starts[:-1] if starts else []
    if starts:
        rest.append(starts[-1])
    start_result = None
    for cmd in rest:
        start_result = execute_gateway_command(cmd, start_result)
    for cmd in dropped:
        if start_result is not None:
            ok, data, err = start_result
            GATEWAY.send_result(str(cmd.get("id") or ""), ok=ok, data=data, error=err)
        else:
            GATEWAY.send_result(str(cmd.get("id") or ""), ok=True, data={"ok": True, "coalesced": True})


def gateway_presence() -> dict:
    snap = STATE.snapshot()
    sample = snap.get("last_sample") or {}
    compact = None
    if isinstance(sample, dict):
        compact = {
            "aircraft_type": sample.get("aircraft_type"),
            "on_ground": sample.get("on_ground"),
            "ground_speed": sample.get("ground_speed"),
            "altitude": sample.get("altitude"),
        }
    return {
        "sim_connected": bool(snap.get("sim_connected")),
        "icao": snap.get("current_icao") or "",
        "current_icao": snap.get("current_icao") or "",
        "current_airport_name": snap.get("current_airport_name") or "",
        "registration": snap.get("registration") or "",
        "aircraft_label": snap.get("aircraft_label") or "",
        "version": CLIENT_VERSION,
        "client_version": CLIENT_VERSION,
        "logged_in": bool(snap.get("logged_in")),
        "monitoring": bool(snap.get("monitoring")),
        "active_flight_id": snap.get("active_flight_id"),
        "flight_elapsed_sec": snap.get("flight_elapsed_sec") or 0,
        "sim_title": snap.get("sim_title") or "",
        "last_error": snap.get("last_error"),
        "ui_status": snap.get("ui_status") or "",
        "last_message": snap.get("last_message") or "",
        "gsx": bool(snap.get("gsx")),
        "custom_fuel": bool(snap.get("custom_fuel")),
        "fuel_mode": snap.get("fuel_mode") or "auto",
        "addons": snap.get("addons") or {},
        "last_sample": compact,
        "panel_installed": bool(snap.get("panel_installed")),
        "panel_version": snap.get("panel_version"),
        "client_outdated": bool(snap.get("client_outdated")),
        "panel_outdated": bool(snap.get("panel_outdated")),
        "pending_start": bool(snap.get("pending_start")),
        "pending_end": bool(snap.get("pending_end")),
        "tracking_paused": bool(snap.get("tracking_paused")),
        "sim_paused": bool(snap.get("sim_paused")),
        "ts": int(time.time() * 1000),
    }


def ensure_gateway() -> None:
    GATEWAY.token_provider = lambda: STATE.api.token or STATE.config.access_token
    GATEWAY.device_id_provider = lambda: STATE.config.device_id
    GATEWAY.version_provider = lambda: CLIENT_VERSION
    GATEWAY.presence_provider = gateway_presence
    GATEWAY.on_command = enqueue_gateway_command
    GATEWAY.log = STATE.log
    GATEWAY.start()


def after_login_sync() -> None:
    try:
        mf = STATE.api.get_myflight()
        ingest_myflight(mf)
    except Exception as e:
        STATE.log(f"MyFlight nach Login: {e}")
    ensure_gateway()


def start_discord_claim_login() -> dict:
    nonce = secrets.token_hex(32)
    begin_url = f"{DEFAULT_API_URL}/client/claim/begin"
    try:
        r = requests.put(begin_url, json={"nonce": nonce}, timeout=10)
        if r.status_code == 409:
            nonce = secrets.token_hex(32)
            r = requests.put(begin_url, json={"nonce": nonce}, timeout=10)
        if r.status_code == 503:
            raise RuntimeError("Login-Gateway nicht erreichbar – bestehendes Token bleibt.")
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("error") if r.content else r.text[:200])
    except Exception as e:
        msg = str(e)
        STATE.log(f"Claim-Login: {msg}")
        notify_windows(t_ui("FlyConomy – Login", "FlyConomy – Login"), msg, code="FC-LOGIN", kind="error")
        return {"ok": False, "error": msg}
    url = f"{SITE_URL}/api/auth/discord/start?next=/client-login&fc_nonce={nonce}"
    webbrowser.open(url)
    STATE.log("Website-Login geöffnet – Discord bestätigt den MiniClient.")
    threading.Thread(target=wait_claim_token, args=(nonce,), daemon=True, name="fc-claim").start()
    return {"ok": True, "url": url, "message": "Website geöffnet. Nach Discord-Login verbindet sich der Client."}


def claim_wait_pause(status: int, retry_after: Any = None) -> float:
    """Seconds to wait before the next long-poll (0 = ask again right away).

    408/204 are the server's own 20 s long-poll answers. Everything else (429 rate
    limit, 400, 503, 200 without token) used to loop without any pause (§9.6)."""
    if status in (408, 204):
        return 0.0
    if status == 429:
        try:
            return max(2.0, min(float(retry_after), 30.0)) if retry_after is not None else 5.0
        except (TypeError, ValueError):
            return 5.0
    return 3.0


def wait_claim_token(nonce: str, *, deadline_sec: float = 120.0) -> None:
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{DEFAULT_API_URL}/client/claim/wait",
                params={"nonce": nonce},
                timeout=25,
            )
            if r.status_code == 200:
                token = ""
                try:
                    token = str((r.json() or {}).get("token") or "")
                except Exception:
                    token = ""
                if token:
                    ok, msg = STATE.api.login(None, token)
                    STATE.log(msg)
                    return
            if r.status_code == 410:
                STATE.log("Login-Code bereits verwendet oder abgelaufen.")
                return
            pause = claim_wait_pause(r.status_code, (r.headers or {}).get("Retry-After"))
            if pause > 0:
                time.sleep(pause)
        except Exception as e:
            STATE.log(f"Claim-Wait: {e}")
            time.sleep(2)
    msg = t_ui(
        "Browser-Login nicht fertig. Bitte im MiniClient erneut auf Anmelden klicken.",
        "Browser login did not finish. Please sign in again from the MiniClient.",
    )
    STATE.log(msg)
    notify_windows(t_ui("FlyConomy – Login", "FlyConomy – Login"), msg, code="FC-LOGIN", kind="error")


def _title_changed(raw: dict) -> bool:
    """Other aircraft or the 2024 avatar: the sample is not our aircraft (E7)."""
    title = str(raw.get("aircraft_type") or "").strip()
    expected = str(STATE._flight_title or "").strip()
    if not title or not expected:
        if title and not expected:
            STATE._flight_title = title
        STATE._title_mismatch_ticks = 0
        return False
    if title == expected:
        STATE._title_mismatch_ticks = 0
        return False
    if not feature_enabled("title_pause"):
        # Review S8: no pause without the switch (2.11.12 behaviour) – a persistent
        # livery/title change would otherwise stop recording and auto-end for good.
        STATE._title_mismatch_ticks = 0
        if not STATE._title_change_logged:
            STATE._title_change_logged = True
            STATE.log_only(
                t_ui(
                    f"Hinweis: anderer Flugzeugname im Sim („{title[:40]}“, Start: „{expected[:40]}“) – "
                    "die Aufzeichnung läuft weiter.",
                    f"Note: different aircraft name in the sim (“{title[:40]}”, start: “{expected[:40]}”) – "
                    "recording continues.",
                )
            )
        return False
    STATE._title_mismatch_ticks += 1
    if STATE._title_mismatch_ticks == TITLE_CHANGE_TICKS:
        msg = t_ui(
            f"Anderes Flugzeug im Sim („{title[:40]}“) – Aufzeichnung pausiert, bis das "
            f"Flugzeug des Flugs wieder geladen ist („{expected[:40]}“). Der Flug ist nicht abgebrochen.",
            f"Another aircraft in the sim (“{title[:40]}”) – recording paused until the "
            f"flight's aircraft is loaded again (“{expected[:40]}”). The flight is not cancelled.",
        )
        STATE.log(msg)
        STATE.set_ui_status(msg, msg)
        notify_windows(
            t_ui("FlyConomy – anderes Flugzeug", "FlyConomy – other aircraft"),
            msg,
            code="FC-AIRCRAFT-CHANGED",
            kind="info",
        )
        try:
            STATE.sim._custom_fuel = None  # re-check custom tanks after a reload
        except Exception:
            pass
    return STATE._title_mismatch_ticks >= TITLE_CHANGE_TICKS


def _ground_glitch(raw: dict) -> bool:
    if not raw.get("ground_glitch"):
        return False
    if not STATE._ground_glitch_logged:
        STATE._ground_glitch_logged = True
        STATE.log(
            t_ui(
                "SimConnect meldet „am Boden“ in großer Höhe – diese Proben werden verworfen.",
                "SimConnect reports “on ground” high up – those samples are discarded.",
            )
        )
    return True


def monitor_tick() -> None:
    """One second of the monitor: read the sim, record, check, ping, auto-end."""
    drain_gateway_commands()
    process_ground_queue(allow_fire=not STATE.sim_io_pause)
    if STATE.sim_io_pause:
        return

    monitoring = _monitoring_active()

    if monitoring and not _msfs_process_running():
        note_sim_gap("process")
        return

    if not STATE.sim.connected:
        STATE._gsx_cached = False
        STATE._custom_fuel_cached = False
        if monitoring:
            note_sim_gap("disconnected")
        else:
            # Without a flight the link used to stay dead until someone
            # clicked "connect" – reconnect on our own instead.
            now_t = time.time()
            if now_t >= STATE._idle_reconnect_at and _msfs_process_running():
                STATE._idle_reconnect_at = now_t + IDLE_RECONNECT_SEC
                try:
                    ok, msg = STATE.sim.connect()
                except Exception:
                    ok, msg = False, ""
                if ok:
                    STATE.log(msg)
        return

    t_read = time.time()
    raw = STATE.sim.read()
    try:
        STATE.sim.note_read_ms((time.time() - t_read) * 1000.0)
    except Exception:
        pass
    if _monitoring_active() != monitoring:
        # Review S8: restore, start or end changed the flight in another thread while
        # SimConnect was read – this sample belongs to the old state; the next tick
        # runs the gap/position checks for the new one.
        return
    if raw and monitoring and not sample_plausible(raw):
        raw = None
    if not raw:
        # D9: one failed read is no gap – only three in a row (or one already open).
        STATE._read_misses += 1
        if monitoring and (STATE._read_misses >= SIM_MISS_GAP_TICKS or STATE._sim_gap_since):
            note_sim_gap("data")
        return
    STATE._read_misses = 0

    if monitoring:
        if _title_changed(raw):
            note_sim_gap("title")
            with STATE.lock:
                STATE.last_sample = raw
            return
        if _ground_glitch(raw):
            return  # D8: "on ground" at altitude – neither recorded nor checked
        note_recent_sample(raw)

    if monitoring and STATE._sim_gap_since:
        resume_after_gap(raw)
        monitoring = _monitoring_active()
        if monitoring and STATE._sim_gap_since:
            # one odd sample after the gap – wait for the next one (GAP_CONFIRM_TICKS)
            with STATE.lock:
                STATE.last_sample = raw
            return

    slew_now = bool(raw.get("is_slew"))
    sim_paused_now = bool(raw.get("paused"))
    if monitoring and slew_now:
        STATE._slew_strikes += 1
        if STATE._slew_strikes >= TELEPORT_CONFIRM_TICKS:
            abort_active_flight("slew")
        with STATE.lock:
            STATE.last_sample = raw
        return
    STATE._slew_strikes = 0
    if monitoring and sim_paused_now:
        begin_tracking_pause("sim")
        STATE._sim_paused = True
        with STATE.lock:
            STATE.last_sample = raw
        if (
            STATE._position_mismatch
            and STATE._mismatch_kind == "gap"
            and not feature_enabled("suspect_report")
        ):
            # Final check S8: the "gap" question is open and MSFS is paused (Esc
            # menu, "Ready to Fly" screen after a reload). The far position must not
            # go out here either – the server checks every ping, paused ones too,
            # and far positions can make it end the flight.
            ping_mismatch_ref()
        else:
            ping_paused_tracking(raw)
        return
    if monitoring and STATE._sim_paused:
        # Active pause / Pause-at-TOD just ended. Raising simrate often
        # hitch-pauses MSFS; the aircraft keeps flying in sim time, so
        # a hard 3 nm cut treated honest 16× as a teleport.
        moved = 0.0
        prev_pos = STATE._last_pos or STATE._resume_ref or {}
        try:
            moved = nm_between(
                float(prev_pos["latitude"]),
                float(prev_pos["longitude"]),
                float(raw["latitude"]),
                float(raw["longitude"]),
            )
        except (KeyError, TypeError, ValueError):
            moved = 0.0
        pause_sec = (
            time.time() - float(STATE._pause_started_at)
            if STATE._pause_started_at
            else 2.0
        )
        if prev_pos and prev_pos.get("t"):
            pause_sec = max(pause_sec, (now_ms() - float(prev_pos["t"])) / 1000.0)
        note_sim_rate(raw.get("simulation_rate"))
        allowed = allowed_resume_nm(
            pause_sec,
            (prev_pos or {}).get("simulation_rate"),
            raw.get("simulation_rate"),
        )
        if moved > allowed:
            # Q32: never cancel for the position – pause, report, let the server decide.
            STATE._sim_paused = False
            enter_position_mismatch(
                "jump",
                raw,
                prev_pos or None,
                moved_nm=round(moved, 1),
                allowed_nm=round(allowed, 1),
                pause_sec=int(pause_sec),
                after="pause",
            )
            with STATE.lock:
                STATE.last_sample = raw
            return
        end_tracking_pause()
        STATE.log(
            t_ui(
                "Pause beendet – Track und Zeit laufen weiter.",
                "Pause ended – track and time continue.",
            )
        )
    if monitoring and STATE._position_mismatch:
        still = position_mismatch_tick(raw)
        with STATE.lock:
            STATE.last_sample = raw
        if still or not _monitoring_active():
            return
    try:
        STATE._gsx_cached = bool(STATE.sim.gsx_running())
        STATE._custom_fuel_cached = bool(STATE.sim.has_custom_fuel())
    except Exception:
        pass
    sample = None
    do_atc = False
    parked_ready = False
    with STATE.lock:
        STATE.last_sample = raw
        # `monitoring` (this tick went through the gap/position checks) AND the
        # current state – a session restored mid-tick is never recorded unchecked.
        if monitoring and STATE.monitoring and STATE.session_key:
            sample = make_sample(raw)
            note_sim_rate(sample.get("simulation_rate"))
            note_max_gs(sample.get("ground_speed"))
            tnow = now_ms()
            prev_pos = STATE._last_pos
            jumped = False
            if prev_pos:
                dt_sec = max(0.5, (tnow - int(prev_pos.get("t") or tnow)) / 1000.0)
                jump_nm = nm_between(
                    float(prev_pos["latitude"]),
                    float(prev_pos["longitude"]),
                    sample["latitude"],
                    sample["longitude"],
                )
                max_nm = allowed_move_nm(
                    dt_sec,
                    sample.get("simulation_rate"),
                    prev_pos.get("simulation_rate"),
                    gs_kt=_finite(sample.get("ground_speed"), 0.0),
                )
                if jump_nm > max_nm:
                    # A single stray coordinate is a SimConnect glitch, not a
                    # teleport: keep the old reference and wait for the next
                    # sample to confirm the aircraft really moved.
                    jumped = True
                    STATE._teleport_strikes += 1
                    need = 2 if jump_nm > 400 else 3
                    if STATE._teleport_strikes >= need:
                        sample = {
                            **sample,
                            "_jump_suspect": True,
                            "_jump_nm": jump_nm,
                            "_allowed_nm": max_nm,
                            "_ref": dict(prev_pos),
                        }
                else:
                    STATE._teleport_strikes = 0
            if not jumped:
                STATE._last_pos = {
                    "latitude": sample["latitude"],
                    "longitude": sample["longitude"],
                    "simulation_rate": _finite(sample.get("simulation_rate"), 1.0),
                    "t": tnow,
                }
                STATE._resume_ref = _pos_ref(sample, tnow)
            last = STATE.local_track[-1] if STATE.local_track else None
            last_t = int(last.get("t") or 0) if last else 0
            elapsed_ms = flight_elapsed_now() * 1000
            interval_ms = (
                TRACK_INTERVAL_SHORT_MS
                if elapsed_ms < TRACK_DENSE_FOR_MS
                else TRACK_INTERVAL_MS
            )
            due = not last or (tnow - last_t) >= interval_ms
            rate_now = _finite(sample.get("simulation_rate"), 1.0)
            # D10: a point that falls due above the allowed simrate waits.
            rate_ok = rate_now <= float(STATE.max_sim_rate or MAX_SIM_RATE) + 0.05
            if not jumped and due and rate_ok:
                STATE.local_track.append(
                    sign_point(STATE.session_key, STATE.track_seq, sample)
                )
                STATE.track_seq += 1
                if len(STATE.local_track) > MAX_TRACK_POINTS_LOCAL:
                    STATE.local_track = compact_track(
                        STATE.local_track, MAX_TRACK_POINTS_SEND
                    )
            STATE._equip_tick += 1
            do_atc = STATE._equip_tick % 8 == 0
            STATE._save_tick += 1
            note_flight_progress(sample)
            gs_now = float(sample.get("ground_speed") or 0)
            parked = (
                bool(sample.get("parking_brake"))
                and bool(sample.get("on_ground"))
                and gs_now <= park_gs_limit()
            )
            if parked:
                STATE._park_ticks += 1
            else:
                STATE._park_ticks = 0
            parked_ready = (
                STATE._park_ticks >= PARK_TICKS_TO_END
                and not STATE._auto_ending
                and not STATE._log_ui_open
                and time.time() >= float(STATE._auto_end_cooldown_until or 0)
            )

    if sample and sample.get("_jump_suspect"):
        STATE._teleport_strikes = 0
        enter_position_mismatch(
            "jump",
            raw,
            sample.get("_ref"),
            moved_nm=round(float(sample.get("_jump_nm") or 0), 1),
            allowed_nm=round(float(sample.get("_allowed_nm") or 0), 1),
            after="live",
        )
        return
    if sample:
        limit = float(STATE.max_sim_rate or MAX_SIM_RATE)
        if sample.get("simulation_rate", 1) > limit + 0.05:
            STATE._simrate_strikes += 1
            if STATE._simrate_strikes >= SIMRATE_CONFIRM_TICKS:
                abort_active_flight("simrate")
                return
        else:
            STATE._simrate_strikes = 0

    if STATE._gsx_refuel:
        refuel_sample = sample
        if not refuel_sample and raw:
            try:
                refuel_sample = make_sample(raw)
            except Exception:
                refuel_sample = {
                    "fuel_total": float(raw.get("fuel_total") or 0),
                    "ground_speed": float(raw.get("ground_speed") or 0),
                    "on_ground": bool(raw.get("on_ground", True)),
                }
        if refuel_sample:
            tick_gsx_refuel(refuel_sample)

    integrity_abort = None
    integrity_ping = None
    gate_loading = False
    unreliable = False
    if sample and STATE.monitoring and STATE.session_key and STATE.active_flight_id:
        now_t = time.time()
        started = float(STATE.flight_started_at or now_t)
        gs_now = float(sample.get("ground_speed") or 0)
        elapsed = now_t - started
        unreliable = sim_fuel_unreliable()
        gate_loading = gs_now < PARKED_IDLE_GS_KT or (
            elapsed < LOADING_WINDOW_SEC and gs_now < GATE_LOADING_GS_KT
        )
        if STATE._gsx_refuel and gs_now <= REFUEL_STATIONARY_GS_KT:
            gate_loading = True
        last_i = STATE._integrity_last
        integ_due = elapsed >= INTEGRITY_GRACE_SEC and (
            (not last_i)
            or (now_t - float(last_i.get("t") or 0)) >= INTEGRITY_INTERVAL_SEC
        )
        live_due = (now_t - float(STATE._live_last or 0)) >= LIVE_POS_SEC
        pumping_now = bool(STATE._gsx_refuel) or now_t < float(STATE._refuel_grace_until or 0)
        if integ_due:
            if last_i and not gate_loading and not unreliable and not pumping_now:
                cap = float((STATE.load_info or {}).get("fuel_capacity") or 0)
                planned = float((STATE.load_info or {}).get("fuel_gal") or 0)
                found = fuel_weight_tamper_reason(
                    last_i,
                    sample,
                    now_t - float(last_i.get("t") or now_t),
                    cap,
                    float(sample.get("simulation_rate") or 1),
                    allow_ground_uplift=bool(STATE._gsx_refuel),
                    planned_fuel=planned,
                )
                # A single odd reading (EFB sync, pause, unfocused sim) must
                # not end a flight – only a repeated finding counts.
                if found:
                    if STATE._tamper_reason == found:
                        STATE._tamper_hits += 1
                    else:
                        STATE._tamper_reason = found
                        STATE._tamper_hits = 1
                    if STATE._tamper_hits >= TAMPER_CONFIRM_HITS:
                        integrity_abort = found
                    else:
                        STATE.log(
                            t_ui(
                                "Auffällige Tank-/Gewichtsänderung – wird beim nächsten "
                                "Check überprüft, der Flug läuft weiter.",
                                "Unusual fuel/weight change – re-checked on the next pass, "
                                "the flight continues.",
                            )
                        )
                else:
                    STATE._tamper_hits = 0
                    STATE._tamper_reason = ""
            STATE._integrity_last = {
                "fuel": _finite(sample.get("fuel_total")),
                "weight": _finite(sample.get("total_weight_lb")),
                "t": now_t,
            }
        if not integrity_abort and (integ_due or live_due):
            integrity_ping = {
                "flight_id": STATE.active_flight_id,
                "session_key": STATE.session_key,
                "latitude": sample["latitude"],
                "longitude": sample["longitude"],
                "altitude": sample.get("altitude"),
                "ground_speed": sample.get("ground_speed"),
                "heading": sample.get("heading"),
                "fuel_total": sample.get("fuel_total"),
                "total_weight_lb": sample.get("total_weight_lb"),
                "on_ground": sample.get("on_ground"),
                "simulation_rate": sample.get("simulation_rate"),
                "custom_fuel": unreliable,
            }
            if STATE._fuel_report_due:
                integrity_ping["fuel_report"] = STATE._fuel_report_due
            STATE._live_last = now_t

    if integrity_abort:
        abort_active_flight(integrity_abort)
        return
    if integrity_ping:
        try:
            STATE.api.tracking(integrity_ping)
            if integrity_ping.get("fuel_report"):
                STATE._fuel_report_due = None
        except ServerRejectedError as e:
            # D8: the server's verdict ends the flight – with its reason, never
            # translated into a local "teleport".
            server_ended_flight(str(e))
            return
        except RetryableError as e:
            STATE.log(f"Tracking: Server kurz weg, Flug läuft weiter ({e})")
        except Exception as e:
            STATE.log(f"Tracking-Ping: {e}")

    STATE._prefs_tick += 1
    if STATE.api.token and STATE._prefs_tick % 120 == 0:
        try:
            prefs = STATE.api.get_preferences()
            loc = str(prefs.get("locale") or "").strip().lower()
            if loc in ("de", "en") and loc != STATE.config.locale:
                STATE.config.set_locale(loc)
        except Exception:
            pass

    # Reconcile: if local monitoring but server has no in_progress flight, clear UI desync
    if (
        STATE.monitoring
        and STATE.active_flight_id
        and STATE.api.token
        and STATE._prefs_tick % 45 == 0
    ):
        try:
            active = STATE.api.get_active()
            if active:
                STATE._no_server_flight_hits = 0
            else:
                # One odd answer (proxy, restart, replica lag) must not throw
                # away a running flight together with its local track.
                STATE._no_server_flight_hits += 1
            if not active and STATE._no_server_flight_hits >= 2:
                STATE.log(
                    "Server meldet keinen aktiven Flug – lokale Session wird bereinigt."
                )
                _clear_flight_runtime()
                notify_windows(
                    t_ui("FlyConomy", "FlyConomy"),
                    t_ui(
                        "Kein aktiver Server-Flug mehr – Client-Session bereinigt.",
                        "No active server flight – client session cleared.",
                    ),
                    code="FC-SESSION-CLEARED",
                    kind="info",
                )
                return
        except Exception:
            pass

    STATE._icao_tick += 1
    if STATE._icao_tick % 5 == 0:
        info = STATE.api.get_nearby(
            float(raw.get("latitude") or 0),
            float(raw.get("longitude") or 0),
        )
        if info:
            STATE.current_icao = info.get("icao")
            STATE.current_airport_name = info.get("name") or ""
    if STATE.api.token and STATE._icao_tick % 600 == 0:
        try:
            STATE.api.refresh_session()
        except Exception:
            pass
    if STATE.api.token and STATE._icao_tick % 4 == 0:
        try:
            STATE.api.crew_presence(
                {
                    "sim_connected": bool(STATE.sim.connected),
                    "sim_title": str((STATE.last_sample or {}).get("aircraft_type") or ""),
                    "icao": STATE.current_icao or "",
                    "registration": STATE.economy_registration or STATE.registration or "",
                }
            )
        except Exception:
            pass
    if (
        STATE.api.token
        and not STATE.active_flight_id
        and STATE._icao_tick % 5 == 0
        and STATE.restore_state not in ("pending", "unknown")
    ):
        try:
            pending = STATE.api.get_active()
            if pending and pending.get("id") and pending.get("session_key"):
                adopt_server_flight(pending)
        except Exception:
            pass
    STATE._mf_tick += 1
    if STATE.api.token and STATE._mf_tick % 8 == 0:
        try:
            mf = STATE.api.get_myflight()
            ingest_myflight(mf)
        except Exception:
            pass
    if STATE._save_tick and STATE._save_tick % 15 == 0:
        save_flight_session()
    if do_atc:
        if STATE.economy_registration:
            STATE.sim.set_atc_id(STATE.economy_registration)
    elif STATE.economy_registration and STATE._icao_tick % 8 == 0:
        STATE.sim.set_atc_id(STATE.economy_registration)
    # After start load: never touch fuel/payload again. Only GPS/AP gates if missing.
    if STATE.monitoring and (not STATE.has_gps or not STATE.has_autopilot):
        if STATE._equip_tick % 4 == 0:
            STATE.sim.enforce_equipment_gates(STATE.has_gps, STATE.has_autopilot)
    if STATE.monitoring:
        STATE._hours_tick += 1
        if STATE._hours_tick % 60 == 0:
            hrs = STATE.api.get_hours_remaining()
            if hrs is not None:
                STATE.hours_remaining = hrs
        if parked_ready and sample:
            dest = STATE.current_icao
            if not dest:
                info = STATE.api.get_nearby(
                    sample["latitude"], sample["longitude"]
                )
                if info:
                    dest = info.get("icao")
                    STATE.current_icao = dest
                    STATE.current_airport_name = info.get("name") or ""
            if left_origin():
                from flight_log_ui import start_flight_log_ui

                where = dest or "?"
                STATE.log(
                    f"Parkbremse an {where} (Start {STATE.from_icao or '?'}) – Flug wird geloggt…"
                )
                try:
                    # D2: start_flight_log_ui sets _auto_ending only once its
                    # worker really runs.
                    start_flight_log_ui()
                except Exception as e:
                    STATE._auto_ending = False
                    STATE._park_ticks = 0
                    STATE._auto_end_cooldown_until = time.time() + AUTO_END_FAIL_COOLDOWN_SEC
                    STATE.log(f"Auto-Ende UI: {e}")


def monitor_loop() -> None:
    """Sample Sim locally during flight – contact server only on start/end."""
    while True:
        try:
            monitor_tick()
        except Exception as e:
            print("Monitor:", e)
        time.sleep(SAMPLE_SEC)


def bridge_origin_verdict(origin: Optional[str], sec_fetch_site: Optional[str]) -> tuple:
    """(allowed, value for Access-Control-Allow-Origin or None) – I3/D11.

    CORS alone does not protect us: a "simple" POST (text/plain, exactly what the
    panel sends) runs on the bridge even if the browser hides the answer. Browsers
    always send an http(s) Origin (or "null") together with Sec-Fetch-Site for a
    website's request to 127.0.0.1. So (review S8, no bet on the exact string the
    MSFS engine sends):
      - our own pages (http://127.0.0.1:17474, http://localhost:17474) → yes
      - any non-http scheme (coui://, file://, vfs://, coherent://, …) → yes: no
        website can have such an origin – it is the MSFS panel engine
      - Sec-Fetch-Site cross-site / same-site → no (another website)
      - any other http(s) origin → no
      - "null" or no Origin without such a Sec-Fetch-Site → yes (updater, Python,
        panel engine without Origin; a sandboxed iframe always says cross-site)
    """
    o = str(origin or "").strip()
    sfs = str(sec_fetch_site or "").strip().lower()
    browser_cross = sfs not in ("", "same-origin", "none")
    low = o.lower()
    if low and low in BRIDGE_SELF_ORIGINS:
        return (True, o)
    if low and low != "null":
        scheme = low.split(":", 1)[0] if ":" in low else ""
        if scheme in ("http", "https"):
            return (False, None)
        return (True, o)  # never a website – browsers only send http(s) or "null"
    if browser_cross:
        return (False, None)
    if low == "null":
        return (True, "null")
    return (True, "*")


BRIDGE_HOST_NAMES = ("127.0.0.1", "localhost", "[::1]")


def bridge_host_ok(host_header: Optional[str], port: int) -> bool:
    """DNS rebinding (review S8): a foreign name that resolves to 127.0.0.1 sends its
    own name as Host – and for a same-origin GET neither Origin nor Sec-Fetch-*.
    Only requests addressed to 127.0.0.1 / localhost are answered. A missing Host
    (HTTP/1.0 tools) is tolerated."""
    host = str(host_header or "").strip().lower()
    if not host:
        return True
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
        port_txt = rest[1:] if rest.startswith(":") else ""
    else:
        name, _, port_txt = host.partition(":")
    if name not in BRIDGE_HOST_NAMES:
        return False
    if port_txt and port_txt != str(port):
        return False
    return True


# Rejected origins: one log line per origin and 10 minutes, at most 32 different
# ones – a hostile page must not flood the log or the panel status (review S8).
_REJECTED_ORIGINS: Dict[str, float] = {}
_REJECTED_ORIGINS_MAX = 32
_BRIDGE_PATHS = (
    "/", "/status", "/flight-busy", "/sample", "/logs", "/fuel-desk", "/apply-load", "/login",
    "/connect-sim", "/live-share", "/onboarding", "/start", "/position-choice", "/fuel-mode",
    "/fuel-action", "/gsx", "/refuel", "/end", "/report-problem", "/cancel", "/hotkey", "/spawn",
    "/livery-request", "/detach", "/locale", "/myflight", "/open-web-login", "/web-login", "/logout",
)


def _bridge_path_class(path: str) -> str:
    p = str(path or "")
    if p == "/phone" or p.startswith("/phone/"):
        return "/phone"
    return p if p in _BRIDGE_PATHS else "(andere)"


def _note_rejected_origin(origin: str, path: str, sec_fetch_site: str = "", why: str = "origin") -> None:
    label = (str(origin or "") or (f"Sec-Fetch-Site {sec_fetch_site}" if sec_fetch_site else "ohne Origin"))[:80]
    if not getattr(STATE, "_panel_origin_rejected", None):
        # first rejected request for the problem report (beta: what does MSFS send?)
        STATE._panel_origin_rejected = {
            "origin": str(origin or "")[:80],
            "sec_fetch_site": str(sec_fetch_site or "")[:20],
            "path": _bridge_path_class(path),
            "why": why,
        }
    now = time.time()
    last = _REJECTED_ORIGINS.get(label)
    if last is not None and now - last < 600:
        return
    if last is None and len(_REJECTED_ORIGINS) >= _REJECTED_ORIGINS_MAX:
        return
    _REJECTED_ORIGINS[label] = now
    STATE.log_only(f"Bridge: Anfrage von fremder Seite abgelehnt ({label})")


def _public_user(user: Any) -> Optional[dict]:
    """HTTP /status gets no e-mail, IP, Discord id or token version (review S8)."""
    if not isinstance(user, dict):
        return None
    keep = ("id", "name", "level", "pro_tier", "pro_expires_at", "avatar_url")
    return {k: user.get(k) for k in keep if k in user}


BRIDGE_MAX_BODY = 1024 * 1024


class BridgeHandler(BaseHTTPRequestHandler):
    _cors_origin: Optional[str] = "*"

    def _origin_ok(self) -> bool:
        """Decide once per request; sets the CORS value for the answer."""
        origin = self.headers.get("Origin")
        sfs = self.headers.get("Sec-Fetch-Site")
        path = urlparse(self.path).path
        if feature_enabled("bridge_host_check"):
            try:
                port = int(self.server.server_address[1])
            except Exception:
                port = BRIDGE_PORT
            if not bridge_host_ok(self.headers.get("Host"), port):
                self._cors_origin = None
                _note_rejected_origin(f"Host {str(self.headers.get('Host') or '')[:60]}", path, str(sfs or ""), "host")
                return False
        if not feature_enabled("bridge_origin_check"):
            self._cors_origin = "*"
            return True
        ok, cors = bridge_origin_verdict(origin, sfs)
        self._cors_origin = cors
        if ok and origin and not getattr(STATE, "_panel_origin_seen", None):
            STATE._panel_origin_seen = str(origin)[:80]  # shows in diagnostics (beta)
        if not ok:
            _note_rejected_origin(str(origin or ""), path, str(sfs or ""))
        return ok

    def _cors(self) -> None:
        cors = self._cors_origin
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            if cors != "*":
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-FlyConomy-Panel")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _forbidden(self) -> None:
        self._json(403, {"ok": False, "error": "forbidden origin", "code": "FC-BRIDGE-ORIGIN"})

    def _json(self, code: int, payload: dict) -> None:
        def _default(o: Any) -> Any:
            if isinstance(o, bytes):
                return o.decode("utf-8", "replace").replace("\x00", "").strip()
            if hasattr(o, "isoformat"):
                try:
                    return o.isoformat()
                except Exception:
                    return str(o)
            return str(o)

        try:
            body = json.dumps(payload, default=_default).encode("utf-8")
        except Exception:
            body = json.dumps({"ok": False, "error": "unserializable"}).encode("utf-8")
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self._forbidden()
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _serve_phone(self, url_path: str) -> None:
        rel = url_path[len("/phone") :].lstrip("/") or "index.html"
        rel = rel.replace("\\", "/")
        if ".." in rel.split("/"):
            self._json(403, {"error": "forbidden"})
            return
        base = os.path.normcase(os.path.normpath(os.path.join(ui_dir(), "phone")))
        if not os.path.isdir(base):
            self._json(404, {"error": "not found"})
            return
        full = os.path.normcase(os.path.normpath(os.path.join(base, *rel.split("/"))))
        if os.path.commonpath([base, full]) != base:
            self._json(403, {"error": "forbidden"})
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif full.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._origin_ok():
            self._forbidden()
            return
        try:
            if path == "/phone" or path.startswith("/phone/"):
                self._serve_phone(path)
                return
            self._note_panel()
            if path in ("/", "/status"):
                try:
                    snap = STATE.snapshot()
                except Exception as e:
                    STATE.log(f"Bridge snapshot: {e}")
                    snap = {
                        "ok": True,
                        "logged_in": bool(STATE.api.token),
                        "user": _public_user(STATE.api.user),
                        "client_version": CLIENT_VERSION,
                        "sim_connected": bool(STATE.sim.connected),
                        "last_error": str(e),
                        "last_message": str(getattr(STATE, "last_message", "") or e),
                    }
                # I3: the Discord JWT never leaves the process over HTTP. The
                # desktop UI still gets it from snapshot() via pywebview.
                snap = {k: v for k, v in snap.items() if k != "access_token"}
                snap["user"] = _public_user(snap.get("user"))
                self._json(200, snap)
            elif path == "/flight-busy":
                self._json(200, flight_busy_status())
            elif path == "/sample":
                raw = STATE.sim.read() if STATE.sim.connected else None
                self._json(200, {"ok": True, "sample": raw})
            elif path == "/logs":
                self._json(200, {"ok": True, "logs": STATE.log_lines[-50:]})
            elif path == "/fuel-desk":
                try:
                    self._json(200, action_fuel_desk())
                except Exception as e:
                    self._json(400, {"ok": False, "error": str(e)})
            elif path == "/apply-load":
                # D11/I3: GET may change tanks – never during a flight (the old
                # 1.4.30 panel falls back to GET after a POST timeout).
                if STATE.monitoring or STATE.active_flight_id:
                    self._json(
                        409,
                        {
                            "ok": False,
                            "error": t_ui(
                                "„Ins EFB laden“ ist während des Flugs gesperrt.",
                                "“Load to EFB” is locked during the flight.",
                            ),
                            "code": "FC-APPLY-LOAD-LOCKED",
                        },
                    )
                    return
                try:
                    result = action_apply_load()
                    self._json(200, {"ok": True, **result})
                except Exception as e:
                    self._json(400, {"ok": False, "error": str(e)})
            else:
                self._json(404, {"ok": False, "error": "not found"})
        except Exception as e:
            STATE.log(f"Bridge GET {path}: {e}")
            try:
                self._json(500, {"ok": False, "error": str(e)})
            except Exception:
                pass

    def _panel_version_of_request(self) -> str:
        qs = parse_qs(urlparse(self.path).query)
        ver = (qs.get("panel") or [""])[0].strip()
        if not ver:
            ver = (self.headers.get("X-FlyConomy-Panel") or "").strip()
        return ver

    def _note_panel(self) -> None:
        ver = self._panel_version_of_request()
        if ver:
            note_panel_version(ver)

    def do_POST(self) -> None:  # noqa: N802
        if not self._origin_ok():
            self._forbidden()
            return
        self._note_panel()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = -1
        if length < 0 or length > BRIDGE_MAX_BODY:
            # review S8: -1 used to block a handler thread until the caller closed
            self._json(400, {"ok": False, "error": "bad Content-Length"})
            return
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception:
            body = {}

        path = urlparse(self.path).path
        try:
            if path == "/login":
                token = (
                    body.get("password")
                    or body.get("access_token")
                    or body.get("token")
                    or ""
                )
                ok, msg = STATE.api.login(body.get("username"), token)
                self._json(
                    200 if ok else 400,
                    {
                        "ok": ok,
                        "message": msg,
                        "user": STATE.api.user,
                        "needs_onboarding": False,
                        "mode": "end_of_flight",
                        "api_url": DEFAULT_API_URL,
                    },
                )
            elif path == "/connect-sim":
                ok, msg = STATE.sim.connect()
                self._json(200 if ok else 400, {"ok": ok, "message": msg})
            elif path in ("/live-share", "/onboarding"):
                self._json(
                    200,
                    {
                        "ok": True,
                        "message": "Live-Map vorerst deaktiviert – Prüfung nur beim Flugende.",
                        "share_live_position": False,
                    },
                )
            elif path == "/start":
                try:
                    result = action_start()
                except VersionGateError as e:
                    self._json(409, {"ok": False, "error": str(e), **(e.info or {})})
                    return
                self._json(200, {"ok": True, "result": result})
            elif path == "/position-choice":
                choice = str(body.get("choice") or "").strip().lower()
                if choice not in ("reposition", "continue", "cancel"):
                    self._json(400, {"ok": False, "error": "choice"})
                elif not STATE._position_mismatch:
                    self._json(409, {"ok": False, "error": t_ui("Keine offene Frage", "Nothing to choose")})
                elif choice == "cancel":
                    # review S8: cancelling goes through the same confirmation as /cancel
                    self._json(200, bridge_cancel(body, self._panel_version_of_request()))
                else:
                    STATE._mismatch_choice = choice
                    self._json(200, {"ok": True, "choice": choice})
            elif path == "/fuel-mode":
                mode = str(body.get("mode") or body.get("fuel_mode") or "auto")
                STATE.config.set_fuel_mode(mode)
                self._json(
                    200,
                    {
                        "ok": True,
                        "fuel_mode": STATE.config.fuel_mode,
                        "message": t_ui("Tanken gespeichert", "Fuel mode saved"),
                    },
                )
            elif path == "/fuel-action":
                result = action_fuel(str(body.get("action") or body.get("kind") or ""))
                self._json(200, {"ok": True, **result})
            elif path == "/gsx":
                result = action_gsx(str(body.get("action") or body.get("kind") or ""))
                self._json(200, {"ok": True, **result})
            elif path == "/refuel":
                result = action_economy_refuel(body)
                self._json(200, {"ok": True, **result})
            elif path == "/apply-load":
                result = action_apply_load()
                self._json(200, {"ok": True, **result})
            elif path == "/end":
                from flight_log_ui import start_flight_log_ui

                blocked = local_end_block()
                if blocked:
                    code, msg = blocked
                    STATE.last_error = msg
                    STATE.log(msg)
                    notify_windows(
                        t_ui("FlyConomy – Flug läuft weiter", "FlyConomy – Flight continues"),
                        msg,
                        code=code,
                        kind="error",
                    )
                    self._json(
                        409,
                        {
                            "ok": False,
                            "keep_flying": True,
                            "error": msg,
                            "code": code,
                        },
                    )
                else:
                    result = start_flight_log_ui()
                    self._json(200, {"ok": True, "logging": True, "result": result})
            elif path == "/report-problem":
                from client_notice import open_report

                open_report(
                    code=str(body.get("code") or ""),
                    title=str(body.get("title") or ""),
                    message=str(body.get("message") or ""),
                )
                self._json(
                    200,
                    {
                        "ok": True,
                        "message": t_ui(
                            "Fenster zum Melden ist geöffnet.",
                            "Report window is open.",
                        ),
                    },
                )
            elif path == "/cancel":
                self._json(200, bridge_cancel(body, self._panel_version_of_request()))
            elif path == "/hotkey":
                result = action_hotkey(body)
                self._json(200, {"ok": True, **result})
            elif path == "/spawn":
                result = action_spawn(str(body.get("kind") or body.get("action") or ""))
                self._json(200, {"ok": True, **result})
            elif path == "/livery-request":
                result = action_request_livery()
                self._json(200, {"ok": True, "result": result, "message": result.get("message")})
            elif path == "/detach":
                result = action_detach_phone()
                self._json(200, result)
            elif path == "/locale":
                loc = str(body.get("locale") or "").strip().lower()
                if loc not in ("de", "en"):
                    self._json(400, {"ok": False, "error": "locale must be de or en"})
                else:
                    STATE.config.set_locale(loc)
                    # Best-effort sync to account when logged in
                    if STATE.api.token:
                        try:
                            requests.put(
                                f"{STATE.config.api_url}/users/preferences",
                                json={"locale": loc},
                                headers=STATE.api._headers(),
                                timeout=8,
                            )
                        except Exception:
                            pass
                    self._json(200, {"ok": True, "locale": loc})
            elif path == "/myflight":
                data = STATE.api.get_myflight()
                self._json(200, data)
            elif path in ("/open-web-login", "/web-login"):
                result = start_discord_claim_login()
                self._json(200 if result.get("ok") else 400, result)
            elif path == "/logout":
                STATE.api.token = None
                STATE.api.user = None
                try:
                    STATE.config.set_access_token("", "")
                except Exception:
                    pass
                STATE.log("Abgemeldet (Bridge)")
                self._json(200, {"ok": True, "message": "Abgemeldet"})
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            STATE.log(f"Bridge error: {e}")
            self._json(400, {"ok": False, "error": str(e)})

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def flight_busy_status() -> dict:
    """For the updater: never replace the client while anything flight-related is
    open – also a session file the server has not confirmed yet (D1.4)."""
    fid = STATE.active_flight_id
    pending_end = bool(STATE.pending_end) or file_or_bak_exists(PENDING_END_PATH)
    pending_start = bool(STATE.pending_start) or file_or_bak_exists(PENDING_START_PATH)
    session = file_or_bak_exists(FLIGHT_SESSION_PATH)
    monitoring = bool(STATE.monitoring)
    return {
        "ok": True,
        "busy": bool(fid or monitoring or pending_end or pending_start or session),
        "active_flight_id": fid,
        "monitoring": monitoring,
        "pending_end": pending_end,
        "pending_start": pending_start,
        "session_file": session,
        "restore_state": STATE.restore_state,
    }


def flight_may_be_open() -> bool:
    """Anything flight-related in memory or on disk – also while the restore after a
    restart has not decided yet (review S8: then memory alone says "no flight")."""
    if STATE.active_flight_id or STATE.monitoring:
        return True
    if STATE.restore_state in ("pending", "unknown"):
        return True
    return bool(flight_busy_status().get("busy"))


def bridge_cancel(body: dict, panel_version: str) -> dict:
    """/cancel over the bridge (D11). Panel 1.4.31+ already asked twice; any other
    caller (old panel, phone page) gets a Windows question first."""
    if not flight_may_be_open():
        return {"ok": True, "result": action_cancel()}
    confirmed = str((body or {}).get("confirm") or "") == "two-tap" and version_tuple(
        panel_version
    ) >= version_tuple(PANEL_TWO_TAP_CANCEL)
    if confirmed:
        return {"ok": True, "result": action_cancel()}
    ask_cancel_confirmation()
    return {
        "ok": True,
        "pending_confirm": True,
        "message": t_ui(
            "Bitte im FlyConomy-Fenster bestätigen – der Flug läuft weiter, bis du „Ja“ drückst.",
            "Please confirm in the FlyConomy window – the flight continues until you press “Yes”.",
        ),
    }


def _open_flight_id() -> Any:
    if STATE.active_flight_id is not None:
        return STATE.active_flight_id
    data = read_flight_session_file()
    return (data or {}).get("flight_id")


def ask_cancel_confirmation() -> None:
    fid = _open_flight_id()

    def on_choice(key: str) -> None:
        if key != "cancel":
            STATE.log(t_ui("Abbruch nicht bestätigt – Flug läuft weiter.", "Cancel not confirmed – flight continues."))
            return
        cur = _open_flight_id()
        if fid is not None and cur is not None and str(cur) != str(fid):
            return  # a different flight by now – never cancel that one

        def run() -> None:
            try:
                action_cancel()
            except Exception as e:
                STATE.log(f"Abbruch: {e}")

        threading.Thread(target=run, daemon=True).start()

    msg = t_ui(
        "Das Panel möchte den laufenden Flug abbrechen. Ein abgebrochener Flug wird nicht bezahlt.",
        "The panel wants to cancel the running flight. A cancelled flight is not paid.",
    )
    try:
        from client_notice import ask_choice

        ask_choice(
            t_ui("FlyConomy – Flug abbrechen?", "FlyConomy – Cancel flight?"),
            msg,
            [
                ("keep", t_ui("Nein, weiterfliegen", "No, keep flying"), "primary"),
                ("cancel", t_ui("Ja, Flug abbrechen", "Yes, cancel flight"), "danger"),
            ],
            on_choice,
            code="FC-CANCEL-CONFIRM",
        )
    except Exception as e:
        STATE.log(f"Abbruch-Rückfrage: {e}")


# ---------------------------------------------------------------------------
# D1: after a client/PC restart, look for the open flight in the background.
# ---------------------------------------------------------------------------


_RESTORE_LOCK = threading.Lock()


def restore_open_flight() -> str:
    """'restored' | 'adopted' | 'server_only' | 'stale_removed' | 'none' | 'unknown' | 'busy' | 'retry'.

    'unknown' (network, 5xx, 401) keeps the session file untouched – unlike the
    old bootstrap, which deleted it whenever the server was briefly away."""
    with _RESTORE_LOCK:
        return _restore_open_flight()


def _restore_open_flight() -> str:
    if STATE.monitoring and STATE.session_key:
        return "busy"
    if not ensure_api_token():
        return "unknown"
    data = read_flight_session_file()
    try:
        active = STATE.api.get_active()
    except Exception:
        return "unknown"
    # Review S8: apply the answer only while no start and no flight end runs. A
    # stored end sent by the retry loop at the same moment may book this very
    # flight – then the session must not come back to life ("retry" = try again
    # in a moment, the session file stays untouched).
    if not _START_LOCK.acquire(blocking=False):
        return "retry"
    try:
        if not _END_LOCK.acquire(blocking=False):
            return "retry"
        try:
            return _apply_restore(data, active)
        finally:
            _END_LOCK.release()
    finally:
        _START_LOCK.release()


def _apply_restore(data: Optional[dict], active: Optional[dict]) -> str:
    if STATE.monitoring and STATE.session_key:
        return "busy"  # a start or adopt armed a flight while we asked the server
    if data:
        # re-read: a flush may have removed it (flight logged) while we asked
        fresh = read_flight_session_file()
        if not fresh or str(fresh.get("session_key")) != str(data.get("session_key")):
            data = fresh
    if data and session_ended(data.get("session_key")):
        remove_file_and_bak(FLIGHT_SESSION_PATH)
        data = None
    if active and active.get("id") and flight_ended_locally(active.get("id")):
        active = None  # logged/cancelled here a moment ago – the GET answer was older
    if active and active.get("id"):
        if data and str(data.get("flight_id")) == str(active.get("id")):
            apply_flight_session(data, restored=True)
            if not STATE.flight_started_at:
                STATE.flight_started_at = _parse_started_at(active.get("started_at"))
            STATE.log(
                t_ui(
                    "Offener Flug wiederhergestellt – die Aufzeichnung läuft weiter, sobald der Sim "
                    "wieder Daten liefert. Nichts wird automatisch abgebrochen.",
                    "Open flight restored – recording continues as soon as the sim reports data. "
                    "Nothing is cancelled automatically.",
                )
            )
            STATE.set_ui_status("Flug wiederhergestellt", "Flight restored")
            _push_presence()
            return "restored"
        if data:
            STATE.log(
                t_ui(
                    "Lokale Flug-Sitzung gehört zu einem anderen Flug – entfernt.",
                    "Local flight session belongs to another flight – removed.",
                )
            )
            remove_file_and_bak(FLIGHT_SESSION_PATH)
        if active.get("session_key"):
            adopt_server_flight(active)
            return "adopted"
        if STATE.active_flight_id is not None and str(STATE.active_flight_id) != str(active.get("id")):
            return "busy"
        STATE.active_flight_id = active.get("id")
        STATE.monitoring = False
        msg = t_ui(
            "Auf dem Server läuft ein Flug ohne lokale Sitzung. „Flug starten“ übernimmt ihn, "
            "wenn die Sitzung noch auf diesem PC liegt – sonst „Abbrechen“.",
            "The server has a running flight without a local session. “Start flight” takes it "
            "over if the session is still on this PC – otherwise “Cancel”.",
        )
        STATE.log(msg)
        STATE.set_ui_status(msg, msg)
        return "server_only"
    if data:
        # The server confirmed: no flight. A stored flight end (if any) stays queued.
        remove_file_and_bak(FLIGHT_SESSION_PATH)
        STATE.log(
            t_ui(
                "Alte Flug-Sitzung ohne Server-Flug entfernt.",
                "Old flight session without a server flight removed.",
            )
        )
        return "stale_removed"
    return "none"


def restore_worker() -> None:
    """Runs once the token is known; retries while the answer is 'unknown'."""
    delay = 10.0
    STATE.restore_state = "pending"
    while True:
        if not ensure_api_token():
            time.sleep(2.0)
            continue
        try:
            outcome = restore_open_flight()
        except Exception as e:
            STATE.log(f"Wiederherstellung: {e}")
            outcome = "unknown"
        if outcome == "retry":
            # a start or flight end is running right now – look again shortly
            STATE.restore_state = "unknown"
            time.sleep(2.0)
            continue
        if outcome != "unknown":
            STATE.restore_state = outcome
            return
        STATE.restore_state = "unknown"
        time.sleep(delay)
        delay = min(60.0, delay * 1.5)


def action_detach_phone() -> dict:
    """Open the in-game phone as a normal OS window (other monitor)."""
    url = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/phone/"
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    args_extra = [f"--app={url}", "--new-window", "--window-size=420,860"]
    for exe in candidates:
        if exe and os.path.isfile(exe):
            subprocess.Popen([exe, *args_extra])
            STATE.log("Handy als eigenes Fenster geöffnet")
            return {"ok": True, "url": url, "app": exe}
    webbrowser.open(url)
    STATE.log("Handy im Browser geöffnet")
    return {"ok": True, "url": url, "app": "browser"}


def ensure_msfs_loopback() -> None:
    """Store-MSFS (AppContainer) darf sonst nicht auf 127.0.0.1 zugreifen."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "system32", "CheckNetIsolation.exe")
    if not os.path.isfile(exe):
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for pkg in (
        "Microsoft.Limitless_8wekyb3d8bbwe",
        "Microsoft.FlightSimulator_8wekyb3d8bbwe",
    ):
        try:
            subprocess.run(
                [exe, "LoopbackExempt", "-a", f"-n={pkg}"],
                capture_output=True,
                timeout=8,
                creationflags=flags,
            )
        except Exception:
            pass


def start_bridge() -> None:
    ensure_msfs_loopback()
    server = ThreadingHTTPServer((BRIDGE_HOST, BRIDGE_PORT), BridgeHandler)
    STATE.log(f"Bridge läuft auf http://{BRIDGE_HOST}:{BRIDGE_PORT}")
    server.serve_forever()


def ui_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "ui")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def ui_index() -> str:
    return os.path.join(ui_dir(), "index.html")


class DesktopApi:
    """JS bridge for the modern desktop shell."""

    def __init__(self) -> None:
        self._window: Any = None
        self._login_poll = False

    def bind(self, window: Any) -> None:
        self._window = window

    def get_status(self) -> dict:
        return STATE.snapshot()

    def get_logs(self) -> list:
        return list(STATE.log_lines[-80:])

    def bootstrap(self) -> dict:
        if STATE.config.access_token and not STATE.api.token:
            ok, msg = STATE.api.login(None, STATE.config.access_token)
            STATE.log(msg if ok else f"Auto-Login: {msg}")
        if STATE.api.token:
            # D1: same restore as the MiniClient – a network error keeps the
            # session file (the old code deleted it when the server was away).
            if STATE.restore_state in ("idle", "unknown"):
                try:
                    STATE.restore_state = restore_open_flight()
                except Exception as e:
                    STATE.log(f"Wiederherstellung: {e}")
            try:
                hrs = STATE.api.get_hours_remaining()
                if hrs is not None:
                    STATE.hours_remaining = hrs
            except Exception:
                pass
        if _msfs_process_running():
            ok, msg = STATE.sim.connect()
            STATE.log(msg)
        else:
            STATE.sim.close()
        return STATE.snapshot()

    def login_token(self, token: str) -> dict:
        ok, msg = STATE.api.login(None, token or "")
        if not ok:
            raise RuntimeError(msg)
        STATE.log(msg)
        return {"ok": True, "message": msg, "user": STATE.api.user}

    def logout(self) -> dict:
        STATE.api.token = None
        STATE.api.user = None
        STATE.config.set_access_token("", "")
        # Also clear legacy fields
        try:
            STATE.config.set_credentials("", "")
        except Exception:
            pass
        STATE.sim.close()
        STATE.active_flight_id = None
        STATE.monitoring = False
        STATE.log("Abgemeldet – bitte erneut mit Discord anmelden")
        return {"ok": True, "message": "Abgemeldet"}

    def connect_sim(self) -> dict:
        ok, msg = STATE.sim.connect()
        if not ok:
            raise RuntimeError(msg)
        STATE.log(msg)
        return {"ok": True, "message": msg}

    def start_flight(self) -> dict:
        try:
            STATE.log("Flugstart…")
            STATE.last_error = None
            result = action_start()
            STATE.last_error = None
            return {"ok": True, "message": STATE.last_message, "result": result}
        except Exception as e:
            STATE.last_error = str(e)
            STATE.log(f"Start fehlgeschlagen: {e}")
            raise

    def request_livery(self) -> dict:
        try:
            result = action_request_livery()
            return {"ok": True, "message": result.get("message") or "Anfrage gesendet", "result": result}
        except Exception as e:
            STATE.last_error = str(e)
            STATE.log(f"Livery-Anfrage: {e}")
            raise

    def end_flight(self) -> dict:
        try:
            from flight_log_ui import start_flight_log_ui

            blocked = local_end_block()
            if blocked:
                code, msg = blocked
                STATE.last_error = msg
                STATE.log(msg)
                notify_windows(
                    t_ui("FlyConomy – Flug läuft weiter", "FlyConomy – Flight continues"),
                    msg,
                    code=code,
                    kind="error",
                )
                return {"ok": False, "keep_flying": True, "message": msg, "code": code}
            STATE.log("Flugende…")
            result = start_flight_log_ui()
            return {"ok": True, "logging": True, "message": t_ui("Flug wird geloggt…", "Logging flight…"), "result": result}
        except Exception as e:
            STATE.log(f"Ende fehlgeschlagen: {e}")
            raise

    def cancel_flight(self) -> dict:
        try:
            result = action_cancel()
            return {"ok": True, "message": "Flug abgebrochen", "result": result}
        except Exception as e:
            STATE.log(f"Abbruch fehlgeschlagen: {e}")
            raise

    def start_web_login(self) -> dict:
        """Discord login that binds the MiniClient via claim nonce (no localhost)."""
        return start_discord_claim_login()

    def show_app(self) -> dict:
        if self._window:
            self._window.load_url(SITE_URL)
        return {"ok": True}

    def open_website(self) -> dict:
        if self._window:
            self._window.load_url(SITE_URL)
        else:
            webbrowser.open(SITE_URL)
        return {"ok": True}

    def open_web_login(self) -> dict:
        return start_discord_claim_login()

    def minimize(self) -> dict:
        if self._window:
            try:
                self._window.minimize()
            except Exception:
                pass
        return {"ok": True}


API = DesktopApi()


def _extract_token_js() -> str:
    return """
    (function(){
      try {
        var t = localStorage.getItem('token') || localStorage.getItem('access_token') || '';
        return t || '';
      } catch (e) { return ''; }
    })()
    """


def login_watchdog() -> None:
    """Keep SimConnect API token in sync with the embedded website session."""
    while True:
        time.sleep(2.0)
        if not API._window:
            continue
        try:
            url = ""
            try:
                url = API._window.get_current_url() or ""
            except Exception:
                url = ""
            if "discord.com" in url or "/api/auth/discord" in url:
                continue
            if "flyconomy.de" not in url and "localhost" not in url and "127.0.0.1" not in url:
                continue
            token = API._window.evaluate_js(_extract_token_js())
            if isinstance(token, str):
                token = token.strip().strip('"')
            else:
                token = ""
            current = STATE.api.token or ""
            if token and len(token) > 40 and token != current:
                ok, msg = STATE.api.login(None, token)
                if ok:
                    API._login_poll = False
                    STATE.log(msg)
        except Exception:
            pass


def run_gui() -> None:
    if webview is None:
        print("Bitte installieren: pip install pywebview")
        print("Headless-Bridge läuft weiter auf Port", BRIDGE_PORT)
        while True:
            time.sleep(3600)
        return

    start_url = SITE_URL
    try:
        r = requests.get(SITE_URL, timeout=6)
        if not r.ok:
            raise RuntimeError(str(r.status_code))
    except Exception:
        index = ui_index()
        if os.path.exists(index):
            start_url = Path(index).as_uri()
            STATE.log("Website nicht erreichbar – lokale Oberfläche.")
        else:
            raise SystemExit(f"UI fehlt: {index}")

    window = webview.create_window(
        f"FlyConomy {CLIENT_VERSION}",
        url=start_url,
        js_api=API,
        width=1440,
        height=920,
        min_size=(1024, 700),
        background_color="#F3F6FB",
        text_select=True,
    )
    API.bind(window)
    threading.Thread(target=login_watchdog, daemon=True).start()

    def on_started() -> None:
        try:
            API.bootstrap()
        except Exception as e:
            STATE.log(f"Bootstrap: {e}")

    # Persist cookies so the embedded website stays signed in after Discord login.
    webview.start(private_mode=False, debug=False, func=on_started)


def run_mini() -> None:
    """Tray-only MiniClient: Discord login window, then system tray bridge."""
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except Exception as e:
        raise SystemExit(f"tkinter fehlt: {e}") from e
    try:
        import pystray
        from PIL import Image
    except ImportError:
        raise SystemExit("Bitte installieren: pip install pystray pillow") from None

    def icon_image() -> "Image.Image":
        candidates = [
            Path(ui_dir()) / "tray_icon.png",
            Path(__file__).resolve().parent / "tray_icon.png",
            Path(__file__).resolve().parent / "logo.png",
        ]
        for p in candidates:
            if p.exists():
                try:
                    return Image.open(p).convert("RGBA")
                except Exception:
                    pass
        img = Image.new("RGBA", (64, 64), (30, 116, 213, 255))
        return img

    stop = threading.Event()
    tray_holder: Dict[str, Any] = {"icon": None}

    def ensure_session() -> bool:
        if STATE.api.token:
            return True
        tok = STATE.config.access_token
        if tok:
            ok, _msg = STATE.api.login(None, tok)
            return bool(ok)
        return False

    def open_discord_login() -> None:
        start_discord_claim_login()

    def open_site() -> None:
        webbrowser.open(SITE_URL)

    def connect_sim() -> None:
        try:
            ok, msg = STATE.sim.connect()
            STATE.log(msg if ok else f"Sim: {msg}")
        except Exception as e:
            STATE.log(f"Sim: {e}")

    def quit_all(icon=None, _item=None) -> None:
        stop.set()
        try:
            DISCORD_PRESENCE.stop()
        except Exception:
            pass
        try:
            if icon:
                icon.stop()
        except Exception:
            pass
        try:
            root.after(0, root.destroy)
        except Exception:
            pass

    def start_tray() -> None:
        if tray_holder.get("icon"):
            return
        menu = pystray.Menu(
            pystray.MenuItem("Website oeffnen", lambda: open_site()),
            pystray.MenuItem("MSFS verbinden", lambda: connect_sim()),
            pystray.MenuItem("Discord / Login", lambda: open_discord_login()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", quit_all),
        )
        icon = pystray.Icon(
            "FlyConomyMiniClient",
            icon_image(),
            f"FlyConomy MiniClient {CLIENT_VERSION}",
            menu,
        )
        tray_holder["icon"] = icon

        def run() -> None:
            icon.run()

        threading.Thread(target=run, daemon=True).start()
        STATE.log("MiniClient im System-Tray")

    root = tk.Tk()
    STATE.tk_root = root
    root.title(f"FlyConomy MiniClient {CLIENT_VERSION}")
    root.geometry("420x320")
    root.configure(bg="#F3F8FD")
    root.resizable(False, False)

    frame = tk.Frame(root, bg="#F3F8FD", padx=28, pady=24)
    frame.pack(fill="both", expand=True)

    title_font = tkfont.Font(family="Segoe UI", size=18, weight="bold")
    body_font = tkfont.Font(family="Segoe UI", size=10)

    tk.Label(frame, text="FlyConomy", font=title_font, bg="#F3F8FD", fg="#0F1B2D").pack(
        anchor="w"
    )
    tk.Label(
        frame,
        text="MiniClient – Bridge fuer Website & Panel",
        font=body_font,
        bg="#F3F8FD",
        fg="#6B7C90",
    ).pack(anchor="w", pady=(2, 16))

    status = tk.StringVar(value="Nicht angemeldet")
    tk.Label(frame, textvariable=status, font=body_font, bg="#F3F8FD", fg="#3A4A5E").pack(
        anchor="w", pady=(0, 14)
    )

    tk.Label(
        frame,
        text="Mit Discord anmelden. Danach liegt der MiniClient im System-Tray und verbindet Website, MSFS und Panel.",
        font=body_font,
        bg="#F3F8FD",
        fg="#3A4A5E",
        wraplength=360,
        justify="left",
    ).pack(anchor="w", pady=(0, 18))

    btn = tk.Button(
        frame,
        text="Mit Discord anmelden",
        command=open_discord_login,
        bg="#5865F2",
        fg="#FFFFFF",
        activebackground="#4752C4",
        activeforeground="#FFFFFF",
        relief="flat",
        font=("Segoe UI", 11, "bold"),
        padx=16,
        pady=10,
        cursor="hand2",
    )
    btn.pack(anchor="w")

    tk.Label(
        frame,
        text=f"Bridge http://127.0.0.1:{BRIDGE_PORT}  ·  v{CLIENT_VERSION}",
        font=("Segoe UI", 8),
        bg="#F3F8FD",
        fg="#90A4AE",
    ).pack(anchor="w", pady=(20, 0))

    logged_ui = {"done": False}

    def hide_to_tray() -> None:
        if logged_ui["done"]:
            return
        logged_ui["done"] = True
        status.set("Angemeldet – im System-Tray")
        start_tray()
        try:
            root.withdraw()
        except Exception:
            pass
        STATE.log("MiniClient bereit (Tray)")

    def tick() -> None:
        if stop.is_set():
            return
        if ensure_session():
            user = (STATE.api.user or {}).get("name") or (STATE.api.user or {}).get(
                "username"
            ) or "Pilot"
            status.set(f"Angemeldet als {user}")
            hide_to_tray()
        else:
            status.set("Nicht angemeldet – Discord-Login")
        root.after(1500, tick)

    def on_close() -> None:
        if ensure_session():
            hide_to_tray()
        else:
            quit_all(tray_holder.get("icon"))

    root.protocol("WM_DELETE_WINDOW", on_close)

    if ensure_session():
        status.set("Sitzung gefunden…")
        root.after(400, hide_to_tray)
    root.after(800, tick)
    root.mainloop()
    stop.set()
    try:
        ic = tray_holder.get("icon")
        if ic:
            ic.stop()
    except Exception:
        pass


def main() -> None:
    threading.Thread(target=start_bridge, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    threading.Thread(target=retry_loop, daemon=True).start()
    threading.Thread(target=updater_push_loop, daemon=True).start()
    DISCORD_PRESENCE.start(discord_presence_snapshot)
    ensure_gateway()
    STATE.pending_end = load_pending_end()
    if STATE.pending_end:
        STATE.log("Es liegt ein nicht gesendetes Flugende in der Warteschlange – wird nachgesendet.")
    STATE.pending_start = load_pending_start()
    if STATE.pending_start:
        STATE.log("Ein Flugstart liegt in der Warteschlange – wird nachgeholt.")
    reset_queue_timers()
    # D1: both clients look for the open flight as soon as the token is known.
    threading.Thread(target=restore_worker, daemon=True, name="fc-restore").start()
    mini = any(a in ("--mini", "-m", "mini") for a in sys.argv[1:])
    if mini:
        STATE.log(f"FlyConomy MiniClient {CLIENT_VERSION} gestartet")
        run_mini()
    else:
        STATE.log(f"FlyConomy Client {CLIENT_VERSION} gestartet")
        run_gui()


if __name__ == "__main__":
    main()
