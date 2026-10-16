"""Payware-specific payload / PFB sync (iniBuilds, …).

MSFS default payload stations are capped (often 3 on the L-1011). Writing one
170 lb «person» per station makes the PFB show 3 passengers. iniBuilds aircraft
expect compartment weights via L:INI_* LVars instead.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional

_log_fn: Optional[Callable[[str], None]] = None

# Default Tristar compartment capacity shares (forum: 16+20+91+91 ≈ 218 seats).
_TRISTAR_ZONE_SHARES = (16, 20, 91, 91)

# iniBuilds A350-style compartment weight LVars (kg).
_A350_PAX_KG = (
    ("L:INI_PAX_BUSSINESS", 0.0492),
    ("L:INI_PAX_ECONOMY_1", 0.1300),
    ("L:INI_PAX_ECONOMY_2", 0.1585),
)
_A350_CARGO_KG = (
    ("L:INI_CARGO_FWD_BAGGAGE_KG", 0.3356),
    ("L:INI_CARGO_AFT_BAGGAGE_KG", 0.275),
    ("L:INI_CARGO_BULK_BAGGAGE_KG", 0.0517),
)

# Tristar / L-1011 — candidate compartment vars (first group that responds wins).
_TRISTAR_PAX_CANDIDATES = (
    (
        "L:INI_PAX_ZONE_1",
        "L:INI_PAX_ZONE_2",
        "L:INI_PAX_ZONE_3",
        "L:INI_PAX_ZONE_4",
    ),
    (
        "L:INI_PAX_COMPARTMENT_1",
        "L:INI_PAX_COMPARTMENT_2",
        "L:INI_PAX_COMPARTMENT_3",
        "L:INI_PAX_COMPARTMENT_4",
    ),
    (
        "L:INI_PAX_FWD",
        "L:INI_PAX_MID_FWD",
        "L:INI_PAX_MID_AFT",
        "L:INI_PAX_AFT",
    ),
    (
        "L:INI_PAX_ECONOMY_1",
        "L:INI_PAX_ECONOMY_2",
        "L:INI_PAX_ECONOMY_3",
        "L:INI_PAX_ECONOMY_4",
    ),
)

_TRISTAR_PAX_COUNT_CANDIDATES = (
    "L:INI_PAX_TOTAL",
    "L:INI_PAX_COUNT",
    "L:INI_PAX_COUNT_TOTAL",
    "L:INI_TOTAL_PAX",
)

_INSTANT_LOAD_CANDIDATES = (
    "L:INI_LOAD_INSTANT",
    "L:INI_PFB_INSTANT_LOAD",
    "L:INI_INSTANT_LOAD",
    "L:INI_APPLY_LOAD",
)


def bind_logger(log_fn: Callable[[str], None]) -> None:
    global _log_fn
    _log_fn = log_fn


def _log(msg: str) -> None:
    if _log_fn:
        _log_fn(msg)


def _parse_passengers(load: dict) -> int:
    passengers = int(load.get("passengers") or 0)
    if passengers <= 0:
        seats_used = int(load.get("seats_used") or 0)
        if seats_used > 1:
            passengers = seats_used - 1
        else:
            for a in load.get("assignments") or []:
                units = str(a.get("units") or "").lower()
                amt = int(float(a.get("amount") or 0))
                if units.startswith("pass") or units in ("pax", "pax."):
                    passengers += max(0, amt)
    return max(0, passengers)


def _payload_kg(load: dict, passengers: int, crew_kg: float) -> tuple[float, float, float]:
    payload_kg = float(load.get("payload_kg") or 0)
    if payload_kg <= 0 and load.get("payload_lb"):
        payload_kg = float(load["payload_lb"]) / 2.20462262
    cargo_kg = float(load.get("cargo_kg") or 0)
    pax_kg = max(0.0, passengers * crew_kg)
    if payload_kg > 0:
        pax_kg = min(pax_kg, max(0.0, payload_kg - cargo_kg))
        if cargo_kg <= 0:
            cargo_kg = max(0.0, payload_kg - pax_kg)
    return payload_kg, pax_kg, cargo_kg


def _is_tristar(title: str) -> bool:
    blob = (title or "").lower()
    return bool(re.search(r"tristar|l-?1011|l1011|lockheed", blob))


def _is_inibuilds(title: str) -> bool:
    blob = (title or "").lower()
    return "inibuilds" in blob or "ini builds" in blob or "ini_" in blob


def _lvar_writable(sim: Any, name: str) -> bool:
    if sim._lvar_get(name) is not None:
        return True
    if sim._lvar_set(name, 0.0):
        time.sleep(0.02)
        return sim._lvar_get(name) is not None
    return False


def _write_shares(sim: Any, names: tuple[str, ...], shares: tuple[int, ...], total_kg: float) -> int:
    if total_kg <= 0 or not names:
        return 0
    total_share = sum(shares) or 1
    ok = 0
    for name, share in zip(names, shares):
        if not _lvar_writable(sim, name):
            continue
        val = total_kg * (share / total_share)
        if sim._lvar_set(name, val):
            ok += 1
    return ok


def _try_pax_count(sim: Any, passengers: int) -> bool:
    for name in _TRISTAR_PAX_COUNT_CANDIDATES:
        if not _lvar_writable(sim, name):
            continue
        if sim._lvar_set(name, float(passengers)):
            return True
    return False


def _try_instant_load(sim: Any) -> bool:
    for name in _INSTANT_LOAD_CANDIDATES:
        if not _lvar_writable(sim, name):
            continue
        if sim._lvar_set(name, 1.0):
            time.sleep(0.15)
            sim._lvar_set(name, 0.0)
            return True
    return False


def _apply_tristar(sim: Any, load: dict, crew_kg: float) -> dict[str, Any]:
    passengers = _parse_passengers(load)
    payload_kg, pax_kg, cargo_kg = _payload_kg(load, passengers, crew_kg)
    details: list[str] = [f"pax={passengers} pax_kg={pax_kg:.0f} cargo_kg={cargo_kg:.0f}"]

    fuel_gal = float(load.get("fuel_gallons") or 0)
    if fuel_gal > 0 and _lvar_writable(sim, "L:INI_FUEL_REQ"):
        fuel_kg = fuel_gal * 3.0
        sim._lvar_set("L:INI_FUEL_REQ", fuel_kg)
        details.append(f"fuel_req={fuel_kg:.0f}kg")

    wrote = 0
    if passengers > 0:
        total_share = sum(_TRISTAR_ZONE_SHARES)
        for group in _TRISTAR_PAX_CANDIDATES:
            active = [n for n in group if _lvar_writable(sim, n)]
            if len(active) < 2:
                continue
            shares = _TRISTAR_ZONE_SHARES[: len(active)]
            share_sum = sum(shares) or 1
            zone_ok = 0
            for name, share in zip(active, shares):
                count = round(passengers * share / share_sum)
                if sim._lvar_set(name, float(count)):
                    zone_ok += 1
            if zone_ok >= 2:
                wrote = zone_ok
                details.append(f"zone_pax={zone_ok} ({active[0]})")
                break

    if wrote == 0 and pax_kg > 0:
        for group in _TRISTAR_PAX_CANDIDATES:
            active = [n for n in group if _lvar_writable(sim, n)]
            if len(active) < 2:
                continue
            shares = _TRISTAR_ZONE_SHARES[: len(active)]
            zone_ok = _write_shares(sim, tuple(active), tuple(shares), pax_kg)
            if zone_ok:
                wrote = zone_ok
                details.append(f"zone_kg={zone_ok} ({active[0]})")
                break

    if wrote == 0 and pax_kg > 0:
        # A350-style fallback (some iniBuilds share naming).
        for name, ratio in _A350_PAX_KG:
            if _lvar_writable(sim, name):
                sim._lvar_set(name, pax_kg * ratio)
                wrote += 1
        if wrote:
            details.append(f"a350_pax_vars={wrote}")

    cargo_wrote = 0
    total_mass = pax_kg + cargo_kg
    if cargo_kg > 0 and total_mass > 0:
        for name, ratio in _A350_CARGO_KG:
            if _lvar_writable(sim, name):
                sim._lvar_set(name, total_mass * ratio)
                cargo_wrote += 1
        if cargo_wrote:
            details.append(f"cargo_vars={cargo_wrote}")

    if _try_pax_count(sim, passengers):
        details.append("pax_count=ok")

    if _try_instant_load(sim):
        details.append("instant_load=ok")

    ok = wrote > 0 or cargo_wrote > 0 or passengers <= 0
    return {"ok": ok, "details": details, "passengers": passengers}


def _apply_inibuilds_generic(sim: Any, load: dict, crew_kg: float) -> dict[str, Any]:
    passengers = _parse_passengers(load)
    payload_kg, pax_kg, cargo_kg = _payload_kg(load, passengers, crew_kg)
    details: list[str] = [f"pax={passengers} pax_kg={pax_kg:.0f} cargo_kg={cargo_kg:.0f}"]
    total = pax_kg + cargo_kg
    wrote = 0
    if total > 0:
        for name, ratio in _A350_PAX_KG + _A350_CARGO_KG:
            if _lvar_writable(sim, name):
                sim._lvar_set(name, total * ratio)
                wrote += 1
    fuel_gal = float(load.get("fuel_gallons") or 0)
    if fuel_gal > 0 and _lvar_writable(sim, "L:INI_FUEL_REQ"):
        sim._lvar_set("L:INI_FUEL_REQ", fuel_gal * 3.0)
        details.append("fuel_req=ok")
    if _try_instant_load(sim):
        details.append("instant_load=ok")
    return {"ok": wrote > 0 or passengers <= 0, "details": details, "passengers": passengers}


def apply_payware_payload(sim: Any, load: dict, *, title: str = "") -> dict[str, Any]:
    if not sim or not getattr(sim, "connected", False) or not isinstance(load, dict):
        return {"ok": False, "details": ["not_connected"]}

    blob = f"{title or ''} {getattr(sim, 'sim_title', lambda: '')()}".strip()
    if not _is_inibuilds(blob) and not _is_tristar(blob):
        return {"ok": False, "details": ["not_inibuilds"]}

    crew_kg = float(load.get("crew_kg") or 77)
    if _is_tristar(blob):
        result = _apply_tristar(sim, load, crew_kg)
    else:
        result = _apply_inibuilds_generic(sim, load, crew_kg)

    if result.get("ok"):
        _log(
            f"iniBuilds/PFB Beladung: {result.get('passengers', 0)} Pax — "
            + "; ".join(str(x) for x in (result.get("details") or []))
        )
    else:
        _log(
            "iniBuilds/PFB Beladung: keine LVar-Reaktion — "
            + "; ".join(str(x) for x in (result.get("details") or []))
        )
    return result


def split_pax_cargo_lb(
    payload_lb: float,
    passengers: int,
    person_lb: float,
) -> tuple[float, float]:
    """Split a payload into people and freight, in pounds.

    Shared so callers can report the same split that gets written to the
    stations instead of recomputing it and drifting apart.
    """
    passengers = max(0, int(passengers or 0))
    payload_lb = float(payload_lb or 0)
    pax_lb = passengers * max(person_lb, 170.0)
    cargo_lb = max(0.0, payload_lb - pax_lb)
    if passengers <= 0 and payload_lb > 0:
        return 0.0, payload_lb
    return pax_lb, cargo_lb


def distribute_payload_stations(
    n_stations: int,
    payload_lb: float,
    passengers: int,
    person_lb: float,
) -> list[float]:
    """Spread pax+cargo across MSFS stations (not 1 person per station)."""
    n_stations = max(1, min(int(n_stations or 1), 20))
    weights = [0.0] * n_stations
    pax_lb, cargo_lb = split_pax_cargo_lb(payload_lb, passengers, person_lb)

    if pax_lb > 0:
        pax_slots = n_stations if cargo_lb <= 0 else max(1, n_stations - 1)
        each = pax_lb / pax_slots
        for i in range(pax_slots):
            weights[i] += each
    if cargo_lb > 0:
        start = 0 if pax_lb <= 0 else max(0, n_stations - max(1, n_stations // 3))
        slots = list(range(start, n_stations)) or [n_stations - 1]
        each = cargo_lb / len(slots)
        for idx in slots:
            weights[idx] += each
    return weights
