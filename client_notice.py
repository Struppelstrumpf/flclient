"""Foreground FlyConomy notices + problem-report window (MiniClient)."""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

BG = "#0B1929"
CARD = "#10233A"
INK = "#F4F8FC"
MUTED = "#9BB0C7"
BLUE = "#1A73E8"
BLUE_HOVER = "#1557B0"
GREEN = "#2EC27F"
RED = "#E24B4A"
RED_HOVER = "#C13A3A"
AMBER = "#E6B422"
LINE = "#1E3A5F"
CHIP = "#16324F"

REPORT_TOPICS = [
    ("abort_wrong", "Flug wurde falsch abgebrochen"),
    ("log_fail", "Loggen geht nicht / falsche Meldung"),
    ("pause", "Pause, Zeit oder Track stimmt nicht"),
    ("slew", "Versetzen erkannt, obwohl ich nicht versetzt habe"),
    ("panel", "Panel / Handy zeigt etwas Falsches"),
    ("client", "MiniClient, Verbindung oder Login"),
    ("fuel", "Tanken, Beladung oder Gewicht"),
    ("livery", "Livery oder Kennzeichen"),
    ("website", "Website / Live-Status"),
    ("pay", "Bezahlung oder Wertung wirkt falsch"),
    ("other", "Etwas anderes"),
]

_notice_win = None
_report_win = None
_lock = threading.Lock()


def t_ui(de: str, en: str) -> str:
    try:
        from flyconomy_client import t_ui as _t

        return _t(de, en)
    except Exception:
        return de


def error_code(raw: str) -> str:
    s = str(raw or "").strip().upper().replace(" ", "-").replace("_", "-")
    if not s:
        return "FC-GENERIC"
    if s.startswith("FC-"):
        return s[:48]
    return f"FC-{s[:40]}"


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _clock():
    from flyconomy_client import SERVER_CLOCK

    return SERVER_CLOCK


def build_diagnostics() -> dict[str, Any]:
    from flyconomy_client import CLIENT_VERSION, STATE

    sample = dict(STATE.last_sample) if STATE.last_sample else {}
    for drop in ("access_token",):
        sample.pop(drop, None)
    user = STATE.api.user if isinstance(STATE.api.user, dict) else {}
    return {
        "ts": int(time.time()),
        "client_version": CLIENT_VERSION,
        "panel_version": STATE.panel_version_live or STATE.panel_version_disk,
        "locale": getattr(STATE.config, "locale", "de"),
        "sim_connected": bool(STATE.sim.connected),
        "monitoring": bool(STATE.monitoring),
        "tracking_paused": bool(getattr(STATE, "_sim_paused", False) or STATE._sim_gap_since),
        "sim_paused": bool(getattr(STATE, "_sim_paused", False)),
        "active_flight_id": STATE.active_flight_id,
        "from_icao": STATE.from_icao,
        "to_icao": STATE.to_icao,
        "current_icao": STATE.current_icao,
        "registration": STATE.registration or STATE.economy_registration,
        "aircraft_label": STATE.aircraft_label,
        "last_error": STATE.last_error,
        "last_message": STATE.last_message or STATE.ui_status,
        "user_id": user.get("id"),
        "user_name": user.get("name") or user.get("username"),
        # 2.11.13 diagnostics (D5/D9/D11): clock offset, SimConnect read times,
        # what the panel engine sends as Origin, restore and queue states.
        "clock_offset_sec": _safe(lambda: round(_clock().raw_ms / 1000.0, 1) if _clock().known else None),
        "sim_read_ms": _safe(lambda: dict(STATE.sim.read_stats)),
        "panel_origin_seen": getattr(STATE, "_panel_origin_seen", None),
        # first request the bridge refused (Origin / Sec-Fetch-Site / Host) – beta check
        "panel_origin_rejected": getattr(STATE, "_panel_origin_rejected", None),
        "flight_unrecorded": bool(STATE.active_flight_id and not STATE.monitoring),
        "restore_state": getattr(STATE, "restore_state", None),
        "position_mismatch": bool(getattr(STATE, "_position_mismatch", False)),
        "mismatch_kind": getattr(STATE, "_mismatch_kind", ""),
        "pending_end": bool(getattr(STATE, "pending_end", None)),
        "pending_start": bool(getattr(STATE, "pending_start", None)),
        "max_sim_rate": getattr(STATE, "max_sim_rate", None),
        "last_sample": {
            "latitude": sample.get("latitude"),
            "longitude": sample.get("longitude"),
            "altitude": sample.get("altitude"),
            "alt_agl": sample.get("alt_agl"),
            "ground_speed": sample.get("ground_speed"),
            "on_ground": sample.get("on_ground"),
            "paused": sample.get("paused"),
            "is_slew": sample.get("is_slew"),
            "simulation_rate": sample.get("simulation_rate"),
            "aircraft_type": sample.get("aircraft_type"),
        },
        "logs": list(STATE.log_lines[-80:]),
    }


def show_notice(
    title: str,
    message: str,
    *,
    code: str = "",
    kind: str = "error",
) -> None:
    """Open/replace the FlyConomy notice window on the UI thread."""
    payload = {
        "title": str(title or "FlyConomy"),
        "message": str(message or ""),
        "code": error_code(code) if code else "",
        "kind": kind if kind in ("ok", "error", "abort", "info") else "error",
    }

    def open_it() -> None:
        _open_notice(payload)

    _on_ui(open_it)


def open_report(*, code: str = "", title: str = "", message: str = "") -> None:
    payload = {
        "code": error_code(code) if code else "",
        "title": str(title or ""),
        "message": str(message or ""),
    }

    def open_it() -> None:
        _open_report(payload)

    _on_ui(open_it)


def ask_choice(
    title: str,
    message: str,
    choices: list,
    on_choice,
    *,
    code: str = "",
) -> None:
    """Non-blocking question with 2–3 buttons; on_choice(key) runs on the UI thread
    ("" when the window is closed without a choice). choices: [(key, label, style)]
    with style primary | secondary | danger."""
    payload = {
        "title": str(title or "FlyConomy"),
        "message": str(message or ""),
        "code": error_code(code) if code else "",
        "choices": [tuple(c) for c in (choices or [])][:3],
        "on_choice": on_choice,
    }

    def open_it() -> None:
        master = _master()
        if master is None:
            return
        ChoiceWindow(master, payload)

    _on_ui(open_it)


def _master():
    try:
        from flyconomy_client import STATE

        master = getattr(STATE, "tk_root", None)
    except Exception:
        master = None
    if master is None:
        import tkinter as tk

        master = tk._default_root  # type: ignore[attr-defined]
    return master


def _on_ui(fn) -> None:
    try:
        from flyconomy_client import STATE

        root = getattr(STATE, "tk_root", None)
        if root is not None:
            root.after(0, fn)
            return
    except Exception:
        pass
    threading.Thread(target=_standalone, args=(fn,), daemon=True).start()


def _standalone(fn) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        from flyconomy_client import STATE

        if getattr(STATE, "tk_root", None) is None:
            STATE.tk_root = root
    except Exception:
        pass
    fn()
    root.mainloop()


def _bring(win) -> None:
    try:
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.after(350, lambda: win.attributes("-topmost", True))
        win.focus_force()
    except Exception:
        pass


def _open_notice(payload: dict) -> None:
    global _notice_win
    with _lock:
        existing = _notice_win
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.apply(payload)
                existing.restore()
                return
        except Exception:
            pass
    try:
        from flyconomy_client import STATE

        master = getattr(STATE, "tk_root", None)
    except Exception:
        master = None
    if master is None:
        import tkinter as tk

        master = tk._default_root  # type: ignore[attr-defined]
    if master is None:
        return
    win = NoticeWindow(master, payload)
    with _lock:
        _notice_win = win


def _open_report(payload: dict) -> None:
    global _report_win
    with _lock:
        existing = _report_win
    if existing is not None:
        try:
            if existing.win.winfo_exists():
                existing.apply_prefill(payload)
                existing.restore()
                return
        except Exception:
            pass
    try:
        from flyconomy_client import STATE

        master = getattr(STATE, "tk_root", None)
    except Exception:
        master = None
    if master is None:
        import tkinter as tk

        master = tk._default_root  # type: ignore[attr-defined]
    if master is None:
        return
    win = ReportWindow(master, payload)
    with _lock:
        _report_win = win


class NoticeWindow:
    def __init__(self, master, payload: dict) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.tk = tk
        self.win = tk.Toplevel(master)
        self.win.title("FlyConomy")
        self.win.configure(bg=BG)
        self.win.geometry("480x420")
        self.win.minsize(440, 360)
        self.win.resizable(True, True)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._payload = payload
        self._kind = payload.get("kind") or "error"

        title_f = tkfont.Font(family="Segoe UI", size=16, weight="bold")
        sub_f = tkfont.Font(family="Segoe UI", size=10)
        btn_f = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        small_f = tkfont.Font(family="Segoe UI", size=8)
        code_f = tkfont.Font(family="Consolas", size=11, weight="bold")

        outer = tk.Frame(self.win, bg=BG, padx=22, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(outer, text="FLYCONOMY", font=small_f, bg=BG, fg=BLUE).pack(anchor="w")
        self.kicker = tk.StringVar()
        tk.Label(outer, textvariable=self.kicker, font=small_f, bg=BG, fg=AMBER).pack(anchor="w", pady=(2, 0))
        self.title_var = tk.StringVar()
        tk.Label(
            outer,
            textvariable=self.title_var,
            font=title_f,
            bg=BG,
            fg=INK,
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(6, 8))

        self.code_frame = tk.Frame(outer, bg=CARD, padx=10, pady=6)
        self.code_var = tk.StringVar()
        tk.Label(self.code_frame, textvariable=self.code_var, font=code_f, bg=CARD, fg=AMBER).pack(anchor="w")
        self.code_frame.pack(anchor="w", pady=(0, 10))

        self.msg_var = tk.StringVar()
        tk.Label(
            outer,
            textvariable=self.msg_var,
            font=sub_f,
            bg=BG,
            fg=MUTED,
            wraplength=420,
            justify="left",
        ).pack(anchor="w")

        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(22, 0), side="bottom")
        self.btn_report = tk.Button(
            btns,
            text=t_ui("Problem melden", "Report a problem"),
            command=self.report,
            bg=CARD,
            fg=INK,
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            bd=0,
            font=btn_f,
            padx=14,
            pady=10,
            cursor="hand2",
        )
        self.btn_report.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.btn_ok = tk.Button(
            btns,
            text=t_ui("Schließen", "Close"),
            command=self.close,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            font=btn_f,
            padx=14,
            pady=10,
            cursor="hand2",
        )
        self.btn_ok.pack(side="left", expand=True, fill="x", padx=(8, 0))
        self.apply(payload)
        self.restore()

    def apply(self, payload: dict) -> None:
        self._payload = payload
        kind = payload.get("kind") or "error"
        kickers = {
            "ok": t_ui("Alles in Ordnung", "All good"),
            "info": t_ui("Hinweis", "Notice"),
            "abort": t_ui("Flug abgebrochen", "Flight cancelled"),
            "error": t_ui("Es ist etwas schiefgelaufen", "Something went wrong"),
        }
        self.kicker.set(kickers.get(kind, kickers["error"]))
        self.title_var.set(payload.get("title") or "FlyConomy")
        self.msg_var.set(payload.get("message") or "")
        code = str(payload.get("code") or "")
        if code:
            if kind in ("info", "ok"):
                self.code_var.set(code)
            else:
                self.code_var.set(t_ui(f"Fehlercode  {code}", f"Error code  {code}"))
            try:
                self.code_frame.pack(anchor="w", pady=(0, 10))
            except Exception:
                pass
        else:
            self.code_var.set("")
            try:
                self.code_frame.pack_forget()
            except Exception:
                pass
        if kind == "ok":
            try:
                self.btn_ok.configure(bg=GREEN, activebackground="#1F9A5B")
            except Exception:
                pass

    def report(self) -> None:
        p = self._payload or {}
        open_report(code=str(p.get("code") or ""), title=str(p.get("title") or ""), message=str(p.get("message") or ""))

    def restore(self) -> None:
        _bring(self.win)

    def close(self) -> None:
        global _notice_win
        try:
            self.win.destroy()
        except Exception:
            pass
        with _lock:
            if _notice_win is self:
                _notice_win = None


class ChoiceWindow:
    """Question with up to three answers (cancel confirmation, D1 position choice)."""

    def __init__(self, master, payload: dict) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.tk = tk
        self._callback = payload.get("on_choice")
        self._done = False
        self.win = tk.Toplevel(master)
        self.win.title("FlyConomy")
        self.win.configure(bg=BG)
        self.win.geometry("500x380")
        self.win.minsize(440, 320)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", lambda: self.pick(""))
        title_f = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        sub_f = tkfont.Font(family="Segoe UI", size=10)
        btn_f = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        small_f = tkfont.Font(family="Segoe UI", size=8)
        outer = tk.Frame(self.win, bg=BG, padx=22, pady=18)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="FLYCONOMY", font=small_f, bg=BG, fg=BLUE).pack(anchor="w")
        tk.Label(
            outer,
            text=payload.get("title") or "FlyConomy",
            font=title_f,
            bg=BG,
            fg=INK,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(6, 8))
        if payload.get("code"):
            tk.Label(outer, text=payload["code"], font=small_f, bg=BG, fg=AMBER).pack(anchor="w")
        tk.Label(
            outer,
            text=payload.get("message") or "",
            font=sub_f,
            bg=BG,
            fg=MUTED,
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(18, 0), side="bottom")
        colors = {
            "primary": (BLUE, BLUE_HOVER, "#FFFFFF"),
            "secondary": (CARD, BLUE_HOVER, INK),
            "danger": (RED, RED_HOVER, "#FFFFFF"),
        }
        for key, label, style in payload.get("choices") or []:
            bg, active, fg = colors.get(style, colors["secondary"])
            tk.Button(
                btns,
                text=label,
                command=lambda k=key: self.pick(k),
                bg=bg,
                fg=fg,
                activebackground=active,
                activeforeground="#FFFFFF",
                relief="flat",
                bd=0,
                font=btn_f,
                padx=12,
                pady=10,
                cursor="hand2",
                wraplength=150,
            ).pack(side="left", expand=True, fill="x", padx=4)
        _bring(self.win)

    def pick(self, key: str) -> None:
        if self._done:
            return
        self._done = True
        try:
            self.win.destroy()
        except Exception:
            pass
        cb = self._callback
        if cb is not None:
            try:
                cb(str(key or ""))
            except Exception:
                pass


class ReportWindow:
    def __init__(self, master, payload: dict) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.tk = tk
        self.win = tk.Toplevel(master)
        self.win.title(t_ui("FlyConomy – Problem melden", "FlyConomy – Report a problem"))
        self.win.configure(bg=BG)
        self.win.geometry("560x720")
        self.win.minsize(500, 620)
        self.win.resizable(True, True)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self._payload = payload
        self._busy = False
        self._checks: dict[str, Any] = {}

        title_f = tkfont.Font(family="Segoe UI", size=16, weight="bold")
        sub_f = tkfont.Font(family="Segoe UI", size=10)
        btn_f = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        small_f = tkfont.Font(family="Segoe UI", size=8)
        lab_f = tkfont.Font(family="Segoe UI", size=9, weight="bold")

        outer = tk.Frame(self.win, bg=BG, padx=20, pady=16)
        outer.pack(fill="both", expand=True)

        self._head = tk.Frame(outer, bg=BG)
        self._head.pack(fill="x")
        tk.Label(self._head, text="FLYCONOMY", font=small_f, bg=BG, fg=BLUE).pack(anchor="w")
        self.title_var = tk.StringVar(value=t_ui("Problem melden", "Report a problem"))
        tk.Label(self._head, textvariable=self.title_var, font=title_f, bg=BG, fg=INK).pack(anchor="w", pady=(4, 2))
        self.sub_var = tk.StringVar(
            value=t_ui(
                "Wähle, was nicht stimmt – du kannst mehrere Punkte ankreuzen – und beschreibe kurz, was passiert ist. Die letzten Minuten (Log, Flug, Sim) gehen mit.",
                "Pick what went wrong — you can select several — and briefly describe it. The last minutes (log, flight, sim) are sent with it.",
            )
        )
        tk.Label(
            self._head,
            textvariable=self.sub_var,
            font=sub_f,
            bg=BG,
            fg=MUTED,
            wraplength=500,
            justify="left",
        ).pack(anchor="w")

        self.code_var = tk.StringVar()
        self.code_lab = tk.Label(self._head, textvariable=self.code_var, font=small_f, bg=BG, fg=AMBER)
        self.code_lab.pack(anchor="w", pady=(8, 0))

        self._form = tk.Frame(outer, bg=BG)
        self._form.pack(fill="both", expand=True, pady=(12, 0))

        tk.Label(
            self._form,
            text=t_ui("Was ist passiert?", "What happened?"),
            font=lab_f,
            bg=BG,
            fg=INK,
        ).pack(anchor="w")

        grid = tk.Frame(self._form, bg=BG)
        grid.pack(fill="x", pady=(6, 10))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        for i, (key, label) in enumerate(REPORT_TOPICS):
            var = tk.BooleanVar(value=False)
            self._checks[key] = var
            cell = tk.Frame(grid, bg=CARD, padx=8, pady=4)
            r, c = divmod(i, 2)
            cell.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
            cb = tk.Checkbutton(
                cell,
                text=t_ui(label, label),
                variable=var,
                bg=CARD,
                fg=INK,
                selectcolor=CHIP,
                activebackground=CARD,
                activeforeground=INK,
                highlightthickness=0,
                bd=0,
                font=("Segoe UI", 9),
                anchor="w",
                cursor="hand2",
            )
            cb.pack(fill="x")

        tk.Label(
            self._form,
            text=t_ui("Eigenes Thema (optional)", "Own topic (optional)"),
            font=lab_f,
            bg=BG,
            fg=INK,
        ).pack(anchor="w", pady=(8, 0))
        self.custom = tk.Entry(
            self._form,
            bg=CARD,
            fg=INK,
            insertbackground=INK,
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.custom.pack(fill="x", ipady=7, pady=(4, 8))

        tk.Label(
            self._form,
            text=t_ui("Beschreibung", "Description"),
            font=lab_f,
            bg=BG,
            fg=INK,
        ).pack(anchor="w")
        self.desc = tk.Text(
            self._form,
            height=7,
            bg=CARD,
            fg=INK,
            insertbackground=INK,
            relief="flat",
            font=("Segoe UI", 10),
            wrap="word",
        )
        self.desc.pack(fill="both", expand=True, pady=(4, 8))

        self.status_var = tk.StringVar(value="")
        tk.Label(self._form, textvariable=self.status_var, font=small_f, bg=BG, fg=AMBER, wraplength=500, justify="left").pack(
            anchor="w"
        )

        self._thanks = tk.Frame(outer, bg=BG)
        thanks_title = tk.StringVar(
            value=t_ui("Vielen Dank – wir schauen uns das an.", "Thank you — we will look into it.")
        )
        tk.Label(self._thanks, textvariable=thanks_title, font=title_f, bg=BG, fg=INK, wraplength=500, justify="left").pack(
            anchor="w", pady=(24, 8)
        )
        tk.Label(
            self._thanks,
            text=t_ui(
                "Deine Meldung ist bei uns angekommen. Du kannst dieses Fenster schließen und weiterfliegen.",
                "Your report reached us. You can close this window and keep flying.",
            ),
            font=sub_f,
            bg=BG,
            fg=MUTED,
            wraplength=500,
            justify="left",
        ).pack(anchor="w")

        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(12, 0), side="bottom")
        self.btn_send = tk.Button(
            btns,
            text=t_ui("Senden", "Send"),
            command=self.send,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            font=btn_f,
            padx=14,
            pady=11,
            cursor="hand2",
        )
        self.btn_send.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.btn_close = tk.Button(
            btns,
            text=t_ui("Abbrechen", "Cancel"),
            command=self.close,
            bg=CARD,
            fg=INK,
            activebackground=RED_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            bd=0,
            font=btn_f,
            padx=14,
            pady=11,
            cursor="hand2",
        )
        self.btn_close.pack(side="left", expand=True, fill="x", padx=(8, 0))

        self.apply_prefill(payload)
        self.restore()

    def apply_prefill(self, payload: dict) -> None:
        self._payload = payload or {}
        code = str(self._payload.get("code") or "")
        if code:
            self.code_var.set(t_ui(f"Fehlercode  {code}", f"Error code  {code}"))
        else:
            self.code_var.set(t_ui("Ohne Fehlercode – frei gemeldet", "No error code — reported freely"))
        msg = str(self._payload.get("message") or "").strip()
        if msg and not self.desc.get("1.0", "end").strip():
            self.desc.insert("1.0", msg)
        code_u = code.upper()
        mapping = {
            "SLEW": "slew",
            "TELEPORT": "slew",
            "NOT-ON-GROUND": "log_fail",
            "KEEP": "log_fail",
            "PAUSE": "pause",
            "SIMRATE": "abort_wrong",
            "FUEL": "fuel",
            "WEIGHT": "fuel",
        }
        for needle, key in mapping.items():
            if needle in code_u and key in self._checks:
                self._checks[key].set(True)

    def restore(self) -> None:
        _bring(self.win)

    def send(self) -> None:
        if self._busy:
            return
        cats = [k for k, var in self._checks.items() if var.get()]
        custom = self.custom.get().strip()[:120]
        desc = self.desc.get("1.0", "end").strip()
        if not cats and not custom:
            self.status_var.set(
                t_ui("Bitte mindestens ein Thema ankreuzen oder ein eigenes Thema eintragen.", "Select at least one topic or enter your own.")
            )
            return
        if len(desc) < 8:
            self.status_var.set(t_ui("Bitte kurz beschreiben, was passiert ist (mind. 8 Zeichen).", "Please describe what happened (at least 8 characters)."))
            return
        self._busy = True
        self.status_var.set(t_ui("Wird gesendet…", "Sending…"))
        self.btn_send.configure(state="disabled")
        diag = build_diagnostics()
        payload = {
            "code": str(self._payload.get("code") or "")[:64],
            "title": str(self._payload.get("title") or "")[:160],
            "categories": cats,
            "custom_topic": custom,
            "description": desc[:4000],
            "diagnostics": diag,
            "client_version": diag.get("client_version"),
            "panel_version": diag.get("panel_version"),
            "flight_id": diag.get("active_flight_id"),
        }

        def worker() -> None:
            err = ""
            try:
                from flyconomy_client import STATE, ensure_api_token

                if not ensure_api_token():
                    err = t_ui(
                        "Nicht angemeldet. Im MiniClient-Tray „Mit Discord anmelden“, dann erneut senden.",
                        "Not signed in. Use Discord login in the MiniClient tray, then send again.",
                    )
                else:
                    STATE.api.submit_problem_report(payload)
            except Exception as e:
                err = str(e)
            def done() -> None:
                self._busy = False
                try:
                    self.btn_send.configure(state="normal")
                except Exception:
                    return
                if err:
                    self.status_var.set(err[:240])
                    return
                self._show_thanks()

            try:
                self.win.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _show_thanks(self) -> None:
        try:
            self._form.pack_forget()
            self.btn_send.pack_forget()
        except Exception:
            pass
        self.title_var.set(t_ui("Meldung gesendet", "Report sent"))
        self.sub_var.set("")
        self.code_var.set("")
        self._thanks.pack(fill="both", expand=True, pady=(8, 0))
        try:
            self.btn_close.configure(
                text=t_ui("Schließen", "Close"),
                bg=GREEN,
                fg="#FFFFFF",
                activebackground="#1F9A5B",
                highlightthickness=0,
            )
            self.btn_close.pack(fill="x")
        except Exception:
            pass
        self.restore()

    def close(self) -> None:
        global _report_win
        try:
            self.win.destroy()
        except Exception:
            pass
        with _lock:
            if _report_win is self:
                _report_win = None
