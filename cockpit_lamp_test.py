"""Aircraft-specific cockpit lamp / annunciator test after FlyConomy start or log.

Each addon wires warning-light tests differently (MSFS default events, Black Square
L:vars, Fenix overhead switches, …). We pick a profile from the sim TITLE and hold
the test state for a few seconds in a background thread.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

DEFAULT_HOLD_SEC = 3.0

_log_fn: Optional[Callable[[str], None]] = None
_lamp_thread: Optional[threading.Thread] = None
_lamp_lock = threading.Lock()


@dataclass(frozen=True)
class _LvarHold:
    lvar: str
    on: float
    off: Optional[float] = None  # None → restore previous value


@dataclass(frozen=True)
class _EventHold:
    on_event: str
    off_event: str


@dataclass(frozen=True)
class LampTestProfile:
    profile_id: str
    label: str
    patterns: tuple[str, ...]
    methods: tuple[Any, ...]


# Order matters: first matching profile wins.
PROFILES: tuple[LampTestProfile, ...] = (
    LampTestProfile(
        "black_square_caravan",
        "Black Square Caravan Professional",
        (
            r"caravan\s*professional",
            r"analog\s*caravan",
            r"bksq.*caravan",
            r"black\s*square.*caravan",
            r"caravan.*bksq",
        ),
        (
            _LvarHold("L:var_LampTestButton_IsDown", 1.0, 0.0),
            _LvarHold("L:var_AnnunciatorTestButton_IsDown", 1.0, 0.0),
            _LvarHold("L:var_LampTestButton", 1.0, 0.0),
            _LvarHold("L:var_AnnunciatorTestButton", 1.0, 0.0),
        ),
    ),
    LampTestProfile(
        "black_square",
        "Black Square",
        (r"black\s*square", r"\bbksq\b", r"blacksquare"),
        (
            _LvarHold("L:var_AnnunciatorTestButton_IsDown", 1.0, 0.0),
            _LvarHold("L:var_LampTestButton_IsDown", 1.0, 0.0),
            _LvarHold("L:var_AnnunciatorTestButton", 1.0, 0.0),
        ),
    ),
    LampTestProfile(
        "tristar",
        "L-1011 Tristar",
        (r"tristar", r"l-?1011", r"l1011", r"lockheed"),
        (
            _LvarHold("L:var_AnnunciatorTestButton_IsDown", 1.0, 0.0),
            _LvarHold("L:var_LampTestButton_IsDown", 1.0, 0.0),
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
            _EventHold("MASTER_CAUTION_ON", "MASTER_CAUTION_OFF"),
        ),
    ),
    LampTestProfile(
        "fenix",
        "Fenix A320",
        (
            r"\bfenix\b",
            r"\bfnx\b",
            r"fnx_32",
            r"fnx-32",
            r"fnx320",
            r"fenix.*a320",
            r"a320.*fenix",
        ),
        (
            # ANN LT sw: TEST detent (Fenix builds use 1 or 2).
            _LvarHold("L:S_OH_INTLT_ANNLT", 2.0, 0.0),
            _LvarHold("L:S_OH_INTLT_ANNLT", 1.0, 0.0),
            _LvarHold("L:I_OH_INTLT_ANNLT", 2.0, 0.0),
            _LvarHold("L:I_OH_INTLT_ANNLT", 1.0, 0.0),
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
        ),
    ),
    LampTestProfile(
        "fbw_a32nx",
        "FlyByWire A32NX",
        (r"a32nx", r"flybywire", r"fly-by-wire", r"fbw"),
        (
            _LvarHold("L:A32NX_OVHD_INTLT_ANNLT", 2.0, 0.0),
            _LvarHold("L:LIGHTING_ANNLT", 2.0, 0.0),
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
        ),
    ),
    LampTestProfile(
        "pmdg",
        "PMDG",
        (r"\bpmdg\b",),
        (
            _EventHold("MASTER_CAUTION_ON", "MASTER_CAUTION_OFF"),
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
        ),
    ),
    LampTestProfile(
        "toliss",
        "Toliss",
        (r"toliss",),
        (
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
            _EventHold("MASTER_CAUTION_ON", "MASTER_CAUTION_OFF"),
        ),
    ),
    LampTestProfile(
        "inibuilds",
        "iniBuilds",
        (r"inibuilds", r"ini_builds", r"a300", r"a310"),
        (
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
        ),
    ),
    LampTestProfile(
        "justflight",
        "Just Flight",
        (r"just\s*flight", r"justflight", r"\bjf\b"),
        (
            _LvarHold("L:var_AnnunciatorTestButton_IsDown", 1.0, 0.0),
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
        ),
    ),
    LampTestProfile(
        "default_msfs",
        "MSFS Standard",
        (r".*",),
        (
            _EventHold("ANNUNCIATOR_SWITCH_ON", "ANNUNCIATOR_SWITCH_OFF"),
        ),
    ),
)


def bind_logger(log_fn: Callable[[str], None]) -> None:
    global _log_fn
    _log_fn = log_fn


def _log(msg: str) -> None:
    if _log_fn:
        _log_fn(msg)


def resolve_profile(title: str) -> LampTestProfile:
    blob = (title or "").lower()
    for profile in PROFILES:
        if profile.profile_id == "default_msfs":
            continue
        for pat in profile.patterns:
            if re.search(pat, blob):
                return profile
    return PROFILES[-1]


def _hold_lvar(sim: Any, spec: _LvarHold, hold_sec: float) -> bool:
    prev = sim._lvar_get(spec.lvar)
    if not sim._lvar_set(spec.lvar, spec.on):
        return False
    # Some aircraft reset the var immediately unless IsDown is used — brief settle.
    time.sleep(0.05)
    cur = sim._lvar_get(spec.lvar)
    if cur is not None and abs(cur - spec.on) > 0.35:
        return False
    time.sleep(max(0.0, hold_sec))
    restore = spec.off if spec.off is not None else (prev if prev is not None else 0.0)
    sim._lvar_set(spec.lvar, restore)
    return True


def _hold_event(sim: Any, spec: _EventHold, hold_sec: float) -> bool:
    if not sim._fire_event(spec.on_event):
        return False
    time.sleep(max(0.0, hold_sec))
    sim._fire_event(spec.off_event)
    return True


def run_lamp_test(sim: Any, title: str, *, hold_sec: float, reason: str) -> bool:
    if not sim or not getattr(sim, "connected", False):
        return False
    profile = resolve_profile(title)
    for method in profile.methods:
        try:
            ok = False
            if isinstance(method, _LvarHold):
                ok = _hold_lvar(sim, method, hold_sec)
            elif isinstance(method, _EventHold):
                ok = _hold_event(sim, method, hold_sec)
            if ok:
                _log(
                    f"Cockpit Lamp Test ({profile.label}, {reason}) "
                    f"{hold_sec:.0f}s — OK"
                )
                return True
        except Exception as e:
            _log(f"Cockpit Lamp Test ({profile.label}): {e}")
    _log(
        f"Cockpit Lamp Test ({profile.label}, {reason}): "
        "keine Methode — Flugzeug nutzt evtl. eigenes System"
    )
    return False


def _worker(sim: Any, title: str, hold_sec: float, reason: str) -> None:
    try:
        run_lamp_test(sim, title, hold_sec=hold_sec, reason=reason)
    finally:
        global _lamp_thread
        with _lamp_lock:
            _lamp_thread = None


def schedule_lamp_test(
    sim: Any,
    title: str,
    *,
    hold_sec: float = DEFAULT_HOLD_SEC,
    reason: str = "ok",
    enabled: bool = True,
) -> bool:
    """Fire lamp test in a daemon thread (non-blocking). Returns False if skipped."""
    global _lamp_thread
    if not enabled:
        return False
    if not sim or not getattr(sim, "connected", False):
        return False
    hold_sec = max(0.5, min(10.0, float(hold_sec or DEFAULT_HOLD_SEC)))
    with _lamp_lock:
        if _lamp_thread and _lamp_thread.is_alive():
            return False
        _lamp_thread = threading.Thread(
            target=_worker,
            args=(sim, title or "", hold_sec, reason),
            daemon=True,
            name="flyconomy-lamp-test",
        )
        _lamp_thread.start()
    return True
