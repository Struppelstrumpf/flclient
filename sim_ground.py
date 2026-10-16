"""SimConnect ground objects + hardware hotkeys for the MiniClient.

Spawns Asobo ground vehicles / marshallers relative to the user aircraft and
maps yoke/throttle/keyboard buttons so Start/End work with the panel closed.
"""

from __future__ import annotations

import math
import os
import re
import struct
import tempfile
import threading
import time
import wave
from ctypes import POINTER, Structure, byref, c_uint, cast, sizeof
from enum import Enum
from typing import Any, Callable, Iterable, Optional

from SimConnect.Constants import SIMCONNECT_GROUP_PRIORITY_HIGHEST, SIMCONNECT_UNUSED
from SimConnect.Enum import (
    SIMCONNECT_DATA_INITPOSITION,
    SIMCONNECT_DATA_WAYPOINT,
    SIMCONNECT_DATATYPE,
    SIMCONNECT_RECV_ASSIGNED_OBJECT_ID,
    SIMCONNECT_RECV_EVENT,
    SIMCONNECT_RECV_ID,
    SIMCONNECT_STATE,
    SIMCONNECT_WAYPOINT_FLAGS,
)

CAPTURE_GROUP = 7
HOTKEY_GROUP = 8
NOTE_GROUP = 9
EVENT_CAPTURE = "FC_INPUT_CAPTURE"
EVENT_START = "FC_INPUT_START"
EVENT_END = "FC_INPUT_END"

MODIFIERS = {
    "VK_LCONTROL": "Ctrl",
    "VK_RCONTROL": "Ctrl",
    "VK_LSHIFT": "Shift",
    "VK_RSHIFT": "Shift",
    "VK_LMENU": "Alt",
    "VK_RMENU": "Alt",
}

SPAWN_CATALOG: dict[str, dict[str, Any]] = {
    "marshaller": {
        "titles": [
            "Marshaller_Male_Summer_Caucasian",
            "Marshaller_Male_Summer_African",
            "Marshaller_Male_Summer_Asian",
            "Marshaller_Male_Summer_Hispanic",
            "Marshaller_Male_Casual_Caucasian",
            "Tarmac_Male_Summer_Caucasian",
        ],
        "spawn": (-2.0, 14.0),
        "park": (16.0, 8.0),
        "hdg": 180.0,
        "drive": False,
        "label": "Einwinker",
    },
    "tarmac": {
        "titles": [
            "Tarmac_Male_Summer_Caucasian",
            "Tarmac_Male_Summer_African",
            "Tarmac_Female_Summer_Caucasian",
        ],
        "spawn": (8.0, -10.0),
        "park": (10.0, -8.0),
        "hdg": 180.0,
        "drive": False,
        "label": "Ramp-Personal",
    },
    "fuel": {
        "titles": [
            "ASO_FuelTruck01_White",
            "ASO_FuelTruck01_Black",
            "ASO_FuelTruck02_White",
            "ASO_FuelTruck02_Black",
            "ASO_FuelTruck01",
            "ASO_Airport_FuelTruck_White",
            "FuelTruck",
            "FuelTruck01",
            "VEH_air_FuelTruck",
            "VEH_air_FuelTruck01",
            "VEH_FuelTruck01",
            "VEH_Airport_Fuel_Truck",
        ],
        "spawn": (-32.0, 20.0),
        "park": (-7.0, 12.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 12.0,
        "label": "Tankwagen",
    },
    "baggage": {
        "titles": ["ASO_Baggage_Cart01", "ASO_Baggage_Cart02", "ASO_BaggageTruck01", "VEH_air_bagcart1"],
        "spawn": (-45.0, -22.0),
        "park": (-10.0, -14.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 10.0,
        "label": "Gepäckwagen",
    },
    "loader": {
        "titles": ["Aso_Baggage_Loader_01", "aso_loadercab"],
        "spawn": (-40.0, -24.0),
        "park": (-12.0, -16.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 8.0,
        "label": "Loader",
    },
    "stairs": {
        "titles": [
            "ASO_Boarding_Stairs",
            "ASO_Boarding_Stairs_Red",
            "ASO_Boarding_Stairs_Yellow",
        ],
        "spawn": (30.0, -22.0),
        "park": (6.0, -12.0),
        "hdg": 90.0,
        "drive": True,
        "speed": 6.0,
        "label": "Treppe",
    },
    "catering": {
        "titles": ["ASO_Catering_Truck_01"],
        "spawn": (40.0, 28.0),
        "park": (4.0, 18.0),
        "hdg": 270.0,
        "drive": True,
        "speed": 8.0,
        "label": "Catering",
    },
    "gpu": {
        "titles": ["ASO_Ground_Power_Unit"],
        "spawn": (12.0, 12.0),
        "park": (10.0, 10.0),
        "hdg": 180.0,
        "drive": False,
        "label": "GPU",
    },
    "pushback": {
        "titles": ["ASO_Pushback_Blue", "ASO_Pushback_White", "ASO_Tug01_White"],
        "spawn": (-40.0, 8.0),
        "park": (-20.0, 0.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 8.0,
        "label": "Pushback",
    },
    "fire": {
        "titles": ["ASO_Firetruck01", "ASO_Firetruck02", "FireTruck"],
        "spawn": (-55.0, 30.0),
        "park": (-28.0, 18.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 16.0,
        "label": "Feuerwehr",
    },
    "shuttle": {
        "titles": ["ASO_Shuttle_01_Yellow", "ASO_Shuttle_01_Gray"],
        "spawn": (-50.0, 20.0),
        "park": (-22.0, 14.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 12.0,
        "label": "Shuttle",
    },
    "tug": {
        "titles": ["ASO_Tug01_White", "ASO_Tug02_White"],
        "spawn": (-35.0, 6.0),
        "park": (-18.0, 4.0),
        "hdg": 0.0,
        "drive": True,
        "speed": 8.0,
        "label": "Tug",
    },
    "caddy": {
        "titles": ["ASO_Aircraft_Caddy"],
        "spawn": (8.0, 14.0),
        "park": (6.0, 12.0),
        "hdg": 180.0,
        "drive": False,
        "label": "Caddy",
    },
}


def heading_deg(val: Any) -> float:
    try:
        h = float(val or 0)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= h <= math.tau + 0.05:
        h = math.degrees(h)
    return h % 360.0


class _JoyInfoEx(Structure):
    _fields_ = [
        ("dwSize", c_uint),
        ("dwFlags", c_uint),
        ("dwXpos", c_uint),
        ("dwYpos", c_uint),
        ("dwZpos", c_uint),
        ("dwRpos", c_uint),
        ("dwUpos", c_uint),
        ("dwVpos", c_uint),
        ("dwButtons", c_uint),
        ("dwButtonNumber", c_uint),
        ("dwPOV", c_uint),
        ("dwReserved1", c_uint),
        ("dwReserved2", c_uint),
    ]


def _input_aliases(defn: str) -> list[str]:
    raw = (defn or "").strip()
    if not raw:
        return []
    aliases = [raw]
    m = re.match(r"joystick:(\d+):button:(\d+)$", raw, re.I)
    if m:
        j, b = int(m.group(1)), int(m.group(2))
        aliases.extend(
            [
                f"Joystick {j} Button {b}",
                f"Joystick {j} Button {b + 1}",
                f"Joy{j + 1} Button{b + 1}",
            ]
        )
    out: list[str] = []
    seen: set[str] = set()
    for a in aliases:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def offset_ll(lat: float, lon: float, heading: float, forward_m: float, right_m: float) -> tuple[float, float]:
    h = math.radians(heading)
    north = forward_m * math.cos(h) - right_m * math.sin(h)
    east = forward_m * math.sin(h) + right_m * math.cos(h)
    dlat = north / 111320.0
    dlon = east / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def lerp_ll(lat0: float, lon0: float, lat1: float, lon1: float, t: float) -> tuple[float, float]:
    t = max(0.0, min(1.0, t))
    return lat0 + (lat1 - lat0) * t, lon0 + (lon1 - lon0) * t


def _wp_flag(name: str) -> int:
    flag = getattr(SIMCONNECT_WAYPOINT_FLAGS, name)
    return int(getattr(flag, "value", flag))


DRIVE_FLAGS = (
    _wp_flag("SIMCONNECT_WAYPOINT_ON_GROUND")
    | _wp_flag("SIMCONNECT_WAYPOINT_SPEED_REQUESTED")
    | _wp_flag("SIMCONNECT_WAYPOINT_ALTITUDE_IS_AGL")
)


def _input_catalog() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = [
        ("VK_LCONTROL", "Ctrl"),
        ("VK_RCONTROL", "Ctrl"),
        ("VK_LSHIFT", "Shift"),
        ("VK_RSHIFT", "Shift"),
        ("VK_LMENU", "Alt"),
        ("VK_RMENU", "Alt"),
    ]
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        items.append((ch, ch))
    for d in "0123456789":
        items.append((d, d))
    for i in range(1, 13):
        items.append((f"VK_F{i}", f"F{i}"))
    extras = [
        ("VK_SPACE", "Leertaste"),
        ("VK_RETURN", "Enter"),
        ("VK_TAB", "Tab"),
        ("VK_INSERT", "Einfg"),
        ("VK_DELETE", "Entf"),
        ("VK_HOME", "Pos1"),
        ("VK_END", "Ende"),
        ("VK_PRIOR", "Bild↑"),
        ("VK_NEXT", "Bild↓"),
        ("VK_ADD", "Num+"),
        ("VK_SUBTRACT", "Num-"),
        ("VK_MULTIPLY", "Num*"),
        ("VK_DIVIDE", "Num/"),
        ("VK_DECIMAL", "Num."),
        ("VK_OEM_MINUS", "-"),
        ("VK_PLUS", "+"),
        ("VK_COMMA", ","),
        ("VK_PERIOD", "."),
        ("VK_SLASH", "/"),
        ("VK_SEMICOLON", ";"),
        ("VK_LBRACKET", "["),
        ("VK_RBRACKET", "]"),
        ("VK_BACKSLASH", "\\"),
        ("VK_QUOTE", "'"),
        ("VK_TILDE", "`"),
    ]
    items.extend(extras)
    for i in range(10):
        items.append((f"VK_NUMPAD{i}", f"Num{i}"))
    for joy in range(16):
        for btn in range(64):
            items.append((f"joystick:{joy}:button:{btn}", f"Joystick {joy + 1} Taste {btn + 1}"))
    return items


INPUT_CATALOG = _input_catalog()
INPUT_INDEX = {defn: i + 1 for i, (defn, _lab) in enumerate(INPUT_CATALOG)}
INDEX_INPUT = {i + 1: (defn, lab) for i, (defn, lab) in enumerate(INPUT_CATALOG)}


def _vk_map() -> dict[str, int]:
    vk: dict[str, int] = {
        "VK_LCONTROL": 0xA2,
        "VK_RCONTROL": 0xA3,
        "VK_LSHIFT": 0xA0,
        "VK_RSHIFT": 0xA1,
        "VK_LMENU": 0xA4,
        "VK_RMENU": 0xA5,
        "VK_SPACE": 0x20,
        "VK_RETURN": 0x0D,
        "VK_TAB": 0x09,
        "VK_INSERT": 0x2D,
        "VK_DELETE": 0x2E,
        "VK_HOME": 0x24,
        "VK_END": 0x23,
        "VK_PRIOR": 0x21,
        "VK_NEXT": 0x22,
        "VK_ADD": 0x6B,
        "VK_SUBTRACT": 0x6D,
        "VK_MULTIPLY": 0x6A,
        "VK_DIVIDE": 0x6F,
        "VK_DECIMAL": 0x6E,
        "VK_OEM_MINUS": 0xBD,
        "VK_PLUS": 0xBB,
        "VK_COMMA": 0xBC,
        "VK_PERIOD": 0xBE,
        "VK_SLASH": 0xBF,
        "VK_SEMICOLON": 0xBA,
        "VK_LBRACKET": 0xDB,
        "VK_RBRACKET": 0xDD,
        "VK_BACKSLASH": 0xDC,
        "VK_QUOTE": 0xDE,
        "VK_TILDE": 0xC0,
    }
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        vk[ch] = ord(ch)
    for i in range(1, 13):
        vk[f"VK_F{i}"] = 0x6F + i
    for i in range(10):
        vk[f"VK_NUMPAD{i}"] = 0x60 + i
    return vk


VK_CODES = _vk_map()
KEY_DEFS = [defn for defn, _lab in INPUT_CATALOG if defn in VK_CODES]


def _key_down(vk: int) -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)
    except Exception:
        return False


def _joy_buttons(only: Optional[Iterable[int]] = None) -> dict[int, int]:
    """Button masks per gamepad slot; `only` limits the poll to known devices."""
    out: dict[int, int] = {}
    try:
        import ctypes

        winmm = ctypes.windll.winmm
    except Exception:
        return out
    info = _JoyInfoEx()
    info.dwSize = sizeof(_JoyInfoEx)
    info.dwFlags = 0xFF
    slots = range(16) if only is None else sorted({int(i) for i in only})
    for joy in slots:
        if winmm.joyGetPosEx(joy, byref(info)) == 0:
            out[joy] = int(info.dwButtons)
    return out


def _sim_focused() -> bool:
    """True when Flight Simulator or our own window owns the foreground.

    Keyboard binds must not fire while the user types in a browser or Discord.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = (buf.value or "").lower()
    except Exception:
        return False
    return "flight simulator" in title or "flyconomy" in title or title.startswith("microsoft flight")


def label_for_definition(defn: str) -> str:
    if "+" in defn:
        parts = defn.split("+")
        pretty = [MODIFIERS.get(p, p.replace("VK_", "")) for p in parts]
        return "+".join(pretty)
    for d, lab in INPUT_CATALOG:
        if d == defn:
            return lab
    return defn


def write_chime(path: str, volume: float) -> None:
    sr = 22050
    ms = 110
    n = int(sr * ms / 1000)
    amp = int(28000 * max(0.0, min(1.0, volume)) * 0.22)
    fade = int(sr * 0.018)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            env = 1.0
            if i < fade:
                env = i / max(1, fade)
            elif i > n - fade:
                env = max(0.0, (n - i) / max(1, fade))
            t = i / sr
            v = math.sin(2 * math.pi * 784 * t) * 0.65 + math.sin(2 * math.pi * 1175 * t) * 0.35
            frames += struct.pack("<h", int(amp * env * v))
        w.writeframes(frames)


class SimGround:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sm = None
        self._orig_dispatch = None
        self._orig_id_event = None
        self._events: dict[str, Any] = {}
        self._wp_def = None
        self._pos_def = None
        self._pending_req: Optional[int] = None
        self._pending_obj: Optional[int] = None
        self._objects: list[dict[str, Any]] = []
        self._queue: list[tuple] = []
        self._listen_slot: Optional[str] = None
        self._listen_first: Optional[str] = None
        self._listen_at: float = 0.0
        self._last_fire: dict[str, float] = {}
        self._joy_prev: dict[int, int] = {}
        self._joy_up: dict[int, int] = {}
        self._listen_held: set[str] = set()
        self._watch_started = False
        self._watch_down = False
        self._wav = os.path.join(tempfile.gettempdir(), "flyconomy_chime.wav")
        self._wav_vol: Optional[float] = None
        self._hooked = False
        self.fire_hook: Optional[Callable[[str], None]] = None
        self.volume = 0.35
        self.start_def = ""
        self.end_def = ""

    def attach(self, sm) -> None:
        with self._lock:
            if sm is None:
                return
            if self._sm is sm and self._hooked:
                return
            self._sm = sm
            self._hooked = False
        self._install_hooks()
        self._ensure_events()
        self.apply_hotkeys()

    def detach(self) -> None:
        with self._lock:
            sm = self._sm
            orig = self._orig_dispatch
            orig_id = self._orig_id_event
            self._sm = None
            self._hooked = False
            self._orig_dispatch = None
            self._orig_id_event = None
            self._events = {}
            self._wp_def = None
            self._pos_def = None
            self._objects = []
            self._joy_prev = {}
        if sm is not None and orig is not None:
            try:
                sm.my_dispatch_proc = orig
            except Exception:
                pass
        if sm is not None and orig_id is not None:
            try:
                sm.handle_id_event = orig_id
            except Exception:
                pass

    def _install_hooks(self) -> None:
        sm = self._sm
        if sm is None:
            return
        if self._orig_dispatch is None:
            self._orig_dispatch = sm.my_dispatch_proc
        orig = self._orig_dispatch
        kit = self

        def hooked(pData, cbData, pContext):
            try:
                dw_id = pData.contents.dwID
                if dw_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_ASSIGNED_OBJECT_ID:
                    rec = cast(pData, POINTER(SIMCONNECT_RECV_ASSIGNED_OBJECT_ID)).contents
                    kit._on_assigned(int(rec.dwRequestID), int(rec.dwObjectID))
                elif dw_id == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EVENT:
                    evt = cast(pData, POINTER(SIMCONNECT_RECV_EVENT)).contents
                    kit._on_event(int(evt.uEventID), int(evt.dwData))
            except Exception:
                pass
            return orig(pData, cbData, pContext)

        sm.my_dispatch_proc = hooked
        try:
            sm.my_dispatch_proc_rd = sm.dll.DispatchProc(hooked)
        except Exception:
            pass
        self._hooked = True

    def _ensure_events(self) -> None:
        sm = self._sm
        if sm is None:
            return
        for name in (EVENT_CAPTURE, EVENT_START, EVENT_END):
            if name in self._events:
                continue
            try:
                names = [m.name for m in sm.dll.EventID] + [name]
                sm.dll.EventID = Enum(sm.dll.EventID.__name__, names)
                ev = list(sm.dll.EventID)[-1]
                sm.dll.MapClientEventToSimEvent(sm.hSimConnect, ev.value, b"")
                try:
                    sm.dll.AddClientEventToNotificationGroup(sm.hSimConnect, NOTE_GROUP, ev, False)
                except Exception:
                    sm.dll.AddClientEventToNotificationGroup(sm.hSimConnect, 0, ev, False)
                self._events[name] = ev
            except Exception as e:
                print("hotkey event", name, e)

    def _on_assigned(self, req_id: int, obj_id: int) -> None:
        with self._lock:
            if self._pending_req is None or req_id == self._pending_req or self._pending_obj is None:
                self._pending_obj = obj_id

    def _on_event(self, event_id: int, data: int) -> None:
        cap = self._events.get(EVENT_CAPTURE)
        start = self._events.get(EVENT_START)
        end = self._events.get(EVENT_END)
        if cap is not None and event_id == getattr(cap, "value", cap):
            self._handle_capture(data)
            return
        if start is not None and event_id == getattr(start, "value", start):
            self.fire("start")
            return
        if end is not None and event_id == getattr(end, "value", end):
            self.fire("end")

    def fire(self, slot: str) -> bool:
        """Queue a Start/Log trigger, ignoring repeats within the debounce window."""
        if self._listen_slot:
            return False
        now = time.time()
        if now - self._last_fire.get(slot, 0) < 0.8:
            return False
        self._last_fire[slot] = now
        self.play_chime()
        hook = self.fire_hook
        if hook is not None:
            threading.Thread(target=hook, args=(slot,), daemon=True).start()
        else:
            with self._lock:
                self._queue.append(("fire", slot))
        return True

    def _handle_capture(self, data: int) -> None:
        info = INDEX_INPUT.get(int(data))
        if not info or not self._listen_slot:
            return
        defn, _lab = info
        # The sim replays queued input right after the group goes live, so ignore
        # anything that arrives before the user could react or is not held now.
        if time.time() - self._listen_at < 0.45:
            return
        vk = VK_CODES.get(defn)
        if vk is not None and not _key_down(vk):
            return
        if defn in self._listen_held:
            return
        self._accept_capture(defn)

    def _accept_capture(self, defn: str) -> None:
        if defn in MODIFIERS:
            self._listen_first = defn
            self._listen_at = time.time()
            return
        if self._listen_first and time.time() - self._listen_at < 2.2:
            self._commit_capture(f"{self._listen_first}+{defn}")
            return
        self._commit_capture(defn)

    def _commit_capture(self, defn: str) -> None:
        slot = self._listen_slot or "start"
        self._listen_slot = None
        self._listen_first = None
        self._listen_held = set()
        self.set_bind(slot, defn)
        with self._lock:
            self._queue.append(("captured", slot, defn))

    def drain(self) -> list[tuple]:
        self._poll_joystick_listen()
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
        if self._listen_slot and self._listen_first and time.time() - self._listen_at > 2.2:
            slot = self._listen_slot
            first = self._listen_first
            self._listen_slot = None
            self._listen_first = None
            self._listen_held = set()
            self.set_bind(slot, first)
            items.append(("captured", slot, first))
        return items

    def begin_listen(self, slot: str) -> str:
        slot = "end" if slot == "end" else "start"
        self._listen_first = None
        self._listen_at = time.time()
        # Everything that is already held down (mouse click, held switch on the
        # throttle) must be released first, otherwise it would count as the bind.
        self._listen_held = {defn for defn in KEY_DEFS if _key_down(VK_CODES[defn])}
        self._joy_up = {}
        self._prime_joysticks()
        self._listen_slot = slot
        self._map_capture()
        threading.Thread(target=self._listen_loop, daemon=True).start()
        return slot

    def _listen_loop(self) -> None:
        deadline = time.time() + 20.0
        while self._listen_slot and time.time() < deadline:
            self._poll_key_listen()
            self._poll_joystick_listen()
            time.sleep(0.03)
        if self._listen_slot and time.time() >= deadline:
            self.cancel_listen()

    def _poll_key_listen(self) -> None:
        """Own keyboard capture – the sim input group alone is not reliable."""
        if not self._listen_slot:
            return
        if time.time() - self._listen_at < 0.15:
            return
        for defn in KEY_DEFS:
            down = _key_down(VK_CODES[defn])
            if not down:
                self._listen_held.discard(defn)
                continue
            if defn in self._listen_held:
                continue
            self._listen_held.add(defn)
            self._accept_capture(defn)
            if not self._listen_slot:
                return

    def cancel_listen(self) -> None:
        self._listen_slot = None
        self._listen_first = None
        self._listen_held = set()
        self._clear_group(CAPTURE_GROUP)

    def listening(self) -> Optional[str]:
        return self._listen_slot

    def _clear_group(self, group: int) -> None:
        sm = self._sm
        if sm is None:
            return
        try:
            sm.dll.ClearInputGroup(sm.hSimConnect, group)
        except Exception:
            pass

    def _map_capture(self) -> None:
        sm = self._sm
        ev = self._events.get(EVENT_CAPTURE)
        if sm is None or ev is None:
            return
        self._clear_group(CAPTURE_GROUP)
        unused = SIMCONNECT_UNUSED
        for defn, _lab in INPUT_CATALOG:
            if defn.lower().startswith("joystick"):
                continue
            idx = INPUT_INDEX[defn]
            try:
                sm.dll.MapInputEventToClientEvent(
                    sm.hSimConnect,
                    CAPTURE_GROUP,
                    defn.encode("ascii"),
                    ev,
                    idx,
                    unused,
                    0,
                    False,
                )
            except Exception:
                continue
        try:
            sm.dll.SetInputGroupPriority(sm.hSimConnect, CAPTURE_GROUP, SIMCONNECT_GROUP_PRIORITY_HIGHEST)
            sm.dll.SetInputGroupState(sm.hSimConnect, CAPTURE_GROUP, SIMCONNECT_STATE.SIMCONNECT_STATE_ON)
        except Exception:
            pass

    def apply_hotkeys(self) -> None:
        self.start_watch()
        sm = self._sm
        if sm is None:
            return
        self._ensure_events()
        self._clear_group(HOTKEY_GROUP)
        unused = SIMCONNECT_UNUSED
        mapped: set[str] = set()
        mapping = (("start", self.start_def, EVENT_START), ("end", self.end_def, EVENT_END))
        for _slot, defn, ev_name in mapping:
            ev = self._events.get(ev_name)
            if not defn or ev is None or defn in mapped:
                continue
            mapped.add(defn)
            for alias in _input_aliases(defn):
                try:
                    sm.dll.MapInputEventToClientEvent(
                        sm.hSimConnect,
                        HOTKEY_GROUP,
                        alias.encode("ascii"),
                        ev,
                        0,
                        unused,
                        0,
                        False,
                    )
                except Exception as e:
                    print("map hotkey", alias, e)
        try:
            sm.dll.SetInputGroupPriority(sm.hSimConnect, HOTKEY_GROUP, SIMCONNECT_GROUP_PRIORITY_HIGHEST)
            sm.dll.SetInputGroupState(sm.hSimConnect, HOTKEY_GROUP, SIMCONNECT_STATE.SIMCONNECT_STATE_ON)
        except Exception:
            pass

    def _definition_down(self, defn: str) -> Optional[bool]:
        """True/False when the bind can be read locally, None when it cannot."""
        parts = [p for p in (defn or "").split("+") if p]
        if not parts:
            return None
        joy_ids = {
            int(m.group(1))
            for m in (re.match(r"joystick:(\d+):button:(\d+)$", p, re.I) for p in parts)
            if m
        }
        joy_state: Optional[dict[int, int]] = None
        for part in parts:
            m = re.match(r"joystick:(\d+):button:(\d+)$", part, re.I)
            if m:
                if joy_state is None:
                    joy_state = _joy_buttons(joy_ids)
                if not (joy_state.get(int(m.group(1)), 0) & (1 << int(m.group(2)))):
                    return False
                continue
            vk = VK_CODES.get(part)
            if vk is None:
                return None
            if not _key_down(vk):
                return False
        return True

    def _needs_focus(self, defn: str) -> bool:
        return any(p in VK_CODES for p in (defn or "").split("+") if p)

    def start_watch(self) -> None:
        if self._watch_started:
            return
        self._watch_started = True
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def _watch_loop(self) -> None:
        """Watch the bind ourselves so it also works with the panel closed."""
        warm = 0
        seen = ""
        while True:
            try:
                defn = (self.start_def or self.end_def or "").strip()
                if defn != seen:
                    seen = defn
                    warm = 3
                if not defn or self._listen_slot:
                    self._watch_down = False
                    time.sleep(0.15)
                    continue
                state = self._definition_down(defn)
                if state is None:
                    self._watch_down = False
                    time.sleep(0.3)
                    continue
                if warm > 0:
                    warm -= 1
                    self._watch_down = bool(state) or self._watch_down
                    time.sleep(0.08)
                    continue
                if state and not self._watch_down:
                    if not self._needs_focus(defn) or _sim_focused():
                        self.fire("start")
                self._watch_down = state
                time.sleep(0.06)
            except Exception:
                self._watch_down = False
                time.sleep(0.5)

    def set_bind(self, slot: str, defn: str) -> str:
        defn = (defn or "").strip()
        self.start_def = defn
        self.end_def = defn
        self._watch_down = self._definition_down(defn) is True
        self._clear_group(CAPTURE_GROUP)
        self.apply_hotkeys()
        self.start_watch()
        return label_for_definition(defn) if defn else ""

    def clear_bind(self, slot: str) -> None:
        self.set_bind(slot, "")

    def play_chime(self) -> None:
        vol = max(0.0, min(1.0, float(self.volume)))
        if vol <= 0.01:
            return

        def _go() -> None:
            try:
                import winsound

                if self._wav_vol != vol:
                    write_chime(self._wav, vol)
                    self._wav_vol = vol
                winsound.PlaySound(
                    self._wav,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
            except Exception:
                pass

        threading.Thread(target=_go, daemon=True).start()

    def snapshot(self) -> dict:
        return {
            "start": self.start_def,
            "start_label": label_for_definition(self.start_def) if self.start_def else "",
            "end": self.end_def,
            "end_label": label_for_definition(self.end_def) if self.end_def else "",
            "volume": int(round(self.volume * 100)),
            "listening": self._listen_slot or "",
        }

    def _user_pose(self) -> Optional[tuple[float, float, float, float]]:
        sm = self._sm
        if sm is None:
            return None
        aq = getattr(sm, "_fc_aq", None)
        # SimReader stores aq separately; caller passes pose.
        return None

    def spawn(self, kind: str, lat: float, lon: float, heading: float, alt: float = 0.0) -> dict:
        return self._spawn(kind, lat, lon, heading)

    def _spawn(self, kind: str, lat: float, lon: float, heading: float, with_crew: bool = False) -> dict:
        spec = SPAWN_CATALOG.get(kind)
        if not spec:
            raise RuntimeError("Unbekanntes Objekt")
        if self._sm is None:
            raise RuntimeError("MSFS nicht verbunden")
        heading = heading_deg(heading)
        s_fwd, s_right = spec["spawn"]
        p_fwd, p_right = spec["park"]
        park_lat, park_lon = offset_ll(lat, lon, heading, p_fwd, p_right)
        drive = bool(spec.get("drive"))
        if drive:
            spawn_lat, spawn_lon = offset_ll(lat, lon, heading, s_fwd, s_right)
            hdg = bearing_deg(spawn_lat, spawn_lon, park_lat, park_lon)
        else:
            spawn_lat, spawn_lon = park_lat, park_lon
            hdg = (heading + float(spec.get("hdg") or 0)) % 360.0
        obj_id = None
        used = ""
        speed_kts = max(4.0, float(spec.get("speed") or 10.0))
        for title in spec["titles"]:
            obj_id = self._create_object(
                title, spawn_lat, spawn_lon, hdg, speed=speed_kts if drive else 0.0
            )
            if obj_id:
                used = title
                break
        if not obj_id:
            raise RuntimeError(f"{spec['label']} konnte nicht gespawnt werden (Titel im Sim nicht gefunden).")
        dist = math.hypot(float(s_fwd) - float(p_fwd), float(s_right) - float(p_right))
        speed_ms = max(2.0, speed_kts * 0.514)
        duration = max(8.0, dist / speed_ms) if drive else 0.0
        now = time.time()
        rec = {
            "id": obj_id,
            "kind": kind,
            "title": used,
            "from": (spawn_lat, spawn_lon),
            "to": (park_lat, park_lon),
            "hdg": hdg,
            "t0": now,
            "duration": duration,
            "drive": drive,
            "speed": speed_kts,
            "leave_at": 0.0,
        }
        if drive:
            self._drive(obj_id, spawn_lat, spawn_lon, park_lat, park_lon, speed_kts)
            time.sleep(0.25)
            self._drive(obj_id, spawn_lat, spawn_lon, park_lat, park_lon, speed_kts)
        else:
            self._release(obj_id)
            self._set_pose(obj_id, spawn_lat, spawn_lon, hdg)
        with self._lock:
            self._objects.append(rec)
        print("spawn", spec["label"], used, "id", obj_id, "drive", drive)
        return {
            "ok": True,
            "id": obj_id,
            "title": used,
            "label": spec["label"],
            "eta": duration,
        }

    def spawn_fuel_truck(self, lat: float, lon: float, heading: float, alt: float = 0.0) -> dict:
        return self.spawn("fuel", lat, lon, heading, alt)

    def send_away(self, obj_id: int, lat: float, lon: float, heading: float, alt: float = 0.0) -> None:
        heading = heading_deg(heading)
        leave_lat, leave_lon = offset_ll(lat, lon, heading, -90.0, 40.0)
        now = time.time()
        rec = None
        with self._lock:
            for row in self._objects:
                if row["id"] == obj_id:
                    rec = row
                    row["from"] = row.get("to") or row.get("from")
                    row["to"] = (leave_lat, leave_lon)
                    row["t0"] = now
                    row["duration"] = 16.0
                    row["drive"] = True
                    row["leave_at"] = now + 22.0
                    row["hdg"] = heading
                    break
        if rec:
            frm = rec.get("from") or (lat, lon)
            self._drive(
                int(obj_id),
                float(frm[0]),
                float(frm[1]),
                leave_lat,
                leave_lon,
                float(rec.get("speed") or 12.0),
            )

    def clear_all(self) -> int:
        sm = self._sm
        n = 0
        with self._lock:
            ids = [r["id"] for r in self._objects]
            self._objects = []
        if sm is None:
            return 0
        for oid in ids:
            try:
                rq = sm.new_request_id()
                sm.dll.AIRemoveObject(sm.hSimConnect, int(oid), rq.value)
                n += 1
            except Exception:
                pass
        return n

    def tick_cleanup(self) -> None:
        now = time.time()
        due: list[int] = []
        with self._lock:
            keep = []
            for rec in self._objects:
                if rec.get("leave_at") and now >= rec["leave_at"]:
                    due.append(rec["id"])
                else:
                    keep.append(rec)
            self._objects = keep
        sm = self._sm
        if sm is None:
            return
        for oid in due:
            try:
                rq = sm.new_request_id()
                sm.dll.AIRemoveObject(sm.hSimConnect, int(oid), rq.value)
            except Exception:
                pass

    def tick_motion(self) -> None:
        return

    def _drive(self, obj_id: int, lat0: float, lon0: float, lat1: float, lon1: float, speed_kts: float) -> None:
        sm = self._sm
        if sm is None or not obj_id:
            return
        brg = bearing_deg(lat0, lon0, lat1, lon1)
        nose_lat, nose_lon = offset_ll(lat0, lon0, brg, 8.0, 0.0)
        mid_lat, mid_lon = lerp_ll(lat0, lon0, lat1, lon1, 0.55)
        points = [
            (nose_lat, nose_lon, max(4.0, speed_kts)),
            (mid_lat, mid_lon, max(4.0, speed_kts * 0.8)),
            (lat1, lon1, 3.0),
        ]
        n = len(points)
        arr = (SIMCONNECT_DATA_WAYPOINT * n)()
        for i, (wlat, wlon, spd) in enumerate(points):
            arr[i].Latitude = float(wlat)
            arr[i].Longitude = float(wlon)
            arr[i].Altitude = 0.0
            arr[i].Flags = DRIVE_FLAGS
            arr[i].ktsSpeed = float(spd)
            arr[i].percentThrottle = 0.0
        try:
            if self._wp_def is None:
                self._wp_def = sm.new_def_id()
                sm.dll.AddToDataDefinition(
                    sm.hSimConnect,
                    self._wp_def.value,
                    b"AI WAYPOINT LIST",
                    b"number",
                    SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_WAYPOINT,
                    0,
                    SIMCONNECT_UNUSED,
                )
            hr = sm.dll.SetDataOnSimObject(
                sm.hSimConnect,
                self._wp_def.value,
                int(obj_id),
                0,
                n,
                sizeof(SIMCONNECT_DATA_WAYPOINT),
                byref(arr),
            )
            print("drive", obj_id, "wps", n, "hr", hr)
        except Exception as e:
            print("drive", e)

    def _create_object(
        self,
        title: str,
        lat: float,
        lon: float,
        hdg: float,
        alt: float = 0.0,
        speed: float = 0.0,
    ) -> Optional[int]:
        sm = self._sm
        if sm is None:
            return None
        try:
            rq = sm.new_request_id()
        except Exception:
            return None
        with self._lock:
            self._pending_req = int(getattr(rq, "value", rq))
            self._pending_obj = None
        try:
            sm.createSimulatedObject(
                title, lat, lon, rq, hdg=hdg, gnd=1, alt=0, speed=int(max(0.0, speed))
            )
        except Exception as e:
            print("spawn", title, e)
            return None
        deadline = time.time() + 2.5
        while time.time() < deadline:
            with self._lock:
                oid = self._pending_obj
            if oid:
                return oid
            time.sleep(0.05)
        print("spawn timeout", title)
        return None

    def _release(self, obj_id: int) -> None:
        sm = self._sm
        if sm is None or not obj_id:
            return
        try:
            rq = sm.new_request_id()
            sm.dll.AIReleaseControl(sm.hSimConnect, int(obj_id), rq.value)
        except Exception:
            pass

    def _set_pose(self, obj_id: int, lat: float, lon: float, hdg: float) -> None:
        sm = self._sm
        if sm is None or not obj_id:
            return
        try:
            init = SIMCONNECT_DATA_INITPOSITION()
            init.Altitude = 0
            init.Latitude = lat
            init.Longitude = lon
            init.Pitch = 0
            init.Bank = 0
            init.Heading = hdg
            init.OnGround = 1
            init.Airspeed = 0
            if self._pos_def is None:
                self._pos_def = sm.new_def_id()
                sm.dll.AddToDataDefinition(
                    sm.hSimConnect,
                    self._pos_def.value,
                    b"Initial Position",
                    b"",
                    SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_INITPOSITION,
                    0,
                    SIMCONNECT_UNUSED,
                )
            sm.dll.SetDataOnSimObject(
                sm.hSimConnect,
                self._pos_def.value,
                int(obj_id),
                0,
                0,
                sizeof(SIMCONNECT_DATA_INITPOSITION),
                byref(init),
            )
        except Exception as e:
            print("pose", e)

    def _prime_joysticks(self) -> None:
        """Baseline held buttons.

        The first poll of a device returns 0 before the driver delivers a state,
        so read twice and keep every button that showed up in either pass.
        """
        prev: dict[int, int] = {}
        for attempt in range(3):
            for joy, buttons in _joy_buttons().items():
                prev[joy] = prev.get(joy, 0) | buttons
            if attempt < 2:
                time.sleep(0.06)
        self._joy_prev = prev

    def _poll_joystick_listen(self) -> None:
        if not self._listen_slot:
            return
        warmup = time.time() - self._listen_at < 0.8
        for joy, buttons in _joy_buttons().items():
            prev = int(self._joy_prev.get(joy) or 0)
            if warmup:
                self._joy_prev[joy] = prev | buttons
                continue
            # Only buttons we have seen released can count as a fresh press, so
            # switches that sit in the ON position never bind themselves.
            up = int(self._joy_up.get(joy) or 0) | (~buttons & 0xFFFFFFFF)
            self._joy_up[joy] = up
            self._joy_prev[joy] = buttons
            changed = buttons & ~prev & up
            if not changed:
                continue
            for bit in range(32):
                if changed & (1 << bit):
                    self._commit_capture(f"joystick:{joy}:button:{bit}")
                    return


GROUND = SimGround()
