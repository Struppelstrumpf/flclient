"""Foreground FlyConomy window while a flight is being logged."""
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
GREEN_DIM = "#1A7A52"
RED = "#E24B4A"
RED_HOVER = "#C13A3A"
AMBER = "#E8A33D"
LINE = "#1E3A5F"

_busy = False  # a worker is sending right now (only one action_end, D2)
_cancel = threading.Event()
_win = None
_lock = threading.Lock()
SUCCESS_AUTO_CLOSE_MS = 20_000


def t_ui(de: str, en: str) -> str:
    try:
        from flyconomy_client import t_ui as _t

        return _t(de, en)
    except Exception:
        return de


def _window_alive(win) -> bool:
    try:
        return bool(win is not None and win.win.winfo_exists())
    except Exception:
        return False


def start_flight_log_ui() -> dict:
    """Open the logging window and submit the flight in the background.

    D2: never two windows or two senders. A window that still shows a failed
    attempt is reused and simply tries again.
    """
    global _busy
    from flyconomy_client import STATE, local_end_block_reason

    blocked = local_end_block_reason()
    if blocked:
        STATE.last_error = blocked
        raise RuntimeError(blocked)
    with _lock:
        if _busy:
            return {"ok": True, "logging": True, "message": t_ui("Wird bereits geloggt…", "Already logging…")}
        if not STATE.monitoring and not STATE.active_flight_id:
            raise RuntimeError(t_ui("Kein aktiver Flug.", "No active flight."))
        existing = _win
        _busy = True
        STATE._log_ui_open = True
        _cancel.clear()
    if existing is not None:
        # close() resets _win, so a window here still exists; after() hands the
        # retry to the UI thread (a destroyed window raises and we open a new one).
        try:
            existing.win.after(0, existing.retry)
            return {"ok": True, "logging": True}
        except Exception:
            pass

    root = getattr(STATE, "tk_root", None)
    if root is not None:
        try:
            root.after(0, lambda: _open_window(root))
            return {"ok": True, "logging": True}
        except Exception:
            pass
    try:
        threading.Thread(target=_standalone, daemon=True).start()
    except Exception:
        _release()
        raise
    return {"ok": True, "logging": True}


def request_cancel_log() -> None:
    _cancel.set()


def _standalone() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    _open_window(root)
    root.mainloop()


def _open_window(master) -> None:
    global _win
    if _win is not None and _window_alive(_win):
        # The old check asked the wrapper object (no winfo_exists) and opened a
        # second window every time (D2).
        try:
            _win.restore()
            if _win._phase != "logging":
                _win.retry()
            return
        except Exception:
            pass
    try:
        _win = FlightLogWindow(master)
    except Exception:
        _release()
        raise


class FlightLogWindow:
    def __init__(self, master) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self.tk = tk
        self.master = master
        self.win = tk.Toplevel(master)
        self.win.title(t_ui("FlyConomy – Flug loggen", "FlyConomy – Log flight"))
        self.win.configure(bg=BG)
        self.win.geometry("460x360")
        self.win.minsize(420, 320)
        self.win.resizable(True, True)
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass
        # X: minimise while sending, close once a result is shown (D2 – it did nothing).
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)
        self._phase = "logging"
        self._result_ok = False
        self._result_kind = ""
        self._session_key = ""
        self._anim = 0.0
        self._alive = True
        self._worker_alive = False
        self._confirm = None

        title_f = tkfont.Font(family="Segoe UI", size=16, weight="bold")
        sub_f = tkfont.Font(family="Segoe UI", size=10)
        btn_f = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        small_f = tkfont.Font(family="Segoe UI", size=8)

        outer = tk.Frame(self.win, bg=BG, padx=22, pady=18)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="FLYCONOMY",
            font=small_f,
            bg=BG,
            fg=BLUE,
        ).pack(anchor="w")
        self.title_var = tk.StringVar(value=t_ui("Der Flug wird geloggt…", "Logging the flight…"))
        tk.Label(outer, textvariable=self.title_var, font=title_f, bg=BG, fg=INK, wraplength=400, justify="left").pack(
            anchor="w", pady=(6, 4)
        )
        self.sub_var = tk.StringVar(
            value=t_ui(
                "Bitte warten. Die Flugdaten werden an den Server gesendet.",
                "Please wait. Flight data is being sent to the server.",
            )
        )
        tk.Label(
            outer,
            textvariable=self.sub_var,
            font=sub_f,
            bg=BG,
            fg=MUTED,
            wraplength=400,
            justify="left",
        ).pack(anchor="w")

        self.canvas = tk.Canvas(outer, height=18, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", pady=(22, 18))

        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill="x", pady=(8, 0))
        self.btn_min = tk.Button(
            btns,
            text=t_ui("Minimieren", "Minimize"),
            command=self.minimize,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            font=btn_f,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.btn_min.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self.btn_abort = tk.Button(
            btns,
            text=t_ui("Abbrechen", "Cancel"),
            command=self.ask_abort,
            bg=CARD,
            fg=INK,
            activebackground=RED_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            highlightthickness=1,
            highlightbackground=LINE,
            bd=0,
            font=btn_f,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.btn_abort.pack(side="left", expand=True, fill="x", padx=(8, 0))

        self.btn_retry = tk.Button(
            outer,
            text=t_ui("Erneut versuchen", "Retry"),
            command=self.retry,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            font=btn_f,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.btn_ok = tk.Button(
            outer,
            text="OK",
            command=self.close,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            font=btn_f,
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.btn_report = tk.Button(
            outer,
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
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self._err_code = ""

        self._tick_bar()
        self._start_worker()
        self._bring_front()

    def _refresh_session_key(self) -> None:
        """Review S8: remember WHICH flight this window logs – the retry loop may book
        it in between; then the window shows that result instead of an error."""
        try:
            from flyconomy_client import current_end_session_key

            self._session_key = current_end_session_key() or getattr(self, "_session_key", "")
        except Exception:
            pass

    def _start_worker(self) -> None:
        try:
            from flyconomy_client import STATE as _st

            # D2: "auto ending" only while a worker really runs
            _st._auto_ending = True
        except Exception:
            pass
        self._refresh_session_key()
        self._worker_alive = True
        threading.Thread(target=self._worker_guarded, daemon=True).start()

    def _worker_guarded(self) -> None:
        try:
            self._worker()
        except Exception as e:
            self._finish(False, str(e) or t_ui("Unbekannter Fehler.", "Unknown error."))
        finally:
            self._worker_alive = False

    def on_close(self) -> None:
        if self._phase == "logging":
            self.minimize()
        else:
            self.close()

    def _bring_front(self) -> None:
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.attributes("-topmost", True)
            self.win.after(400, lambda: self.win.attributes("-topmost", True))
            self.win.focus_force()
        except Exception:
            pass

    def minimize(self) -> None:
        if self._phase != "logging":
            return
        try:
            self.win.attributes("-topmost", False)
            self.win.iconify()
        except Exception:
            pass

    def restore(self) -> None:
        self._bring_front()

    def ask_abort(self) -> None:
        if self._phase != "logging":
            return
        if self._confirm is not None:
            try:
                if self._confirm.winfo_exists():
                    self._confirm.lift()
                    return
            except Exception:
                pass
        tk = self.tk
        dlg = tk.Toplevel(self.win)
        self._confirm = dlg
        dlg.title(t_ui("Flug abbrechen?", "Cancel flight?"))
        dlg.configure(bg=BG)
        dlg.geometry("420x240")
        dlg.resizable(False, False)
        try:
            dlg.transient(self.win)
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        dlg.grab_set()
        pad = tk.Frame(dlg, bg=BG, padx=22, pady=18)
        pad.pack(fill="both", expand=True)
        tk.Label(
            pad,
            text=t_ui("Flug wirklich abbrechen?", "Really cancel the flight?"),
            font=("Segoe UI", 14, "bold"),
            bg=BG,
            fg=INK,
            wraplength=360,
            justify="left",
        ).pack(anchor="w")
        tk.Label(
            pad,
            text=t_ui(
                "Wenn du abbrichst, wird der Flug nicht geloggt – auch nicht auf dem Server.",
                "If you cancel, the flight will not be logged — including on the server.",
            ),
            font=("Segoe UI", 10),
            bg=BG,
            fg=MUTED,
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(8, 16))
        row = tk.Frame(pad, bg=BG)
        row.pack(fill="x")

        def yes() -> None:
            dlg.destroy()
            self._confirm = None
            _cancel.set()
            self.sub_var.set(t_ui("Abbruch wird ausgeführt…", "Cancelling…"))

        def no() -> None:
            dlg.destroy()
            self._confirm = None
            self._bring_front()

        tk.Button(
            row,
            text=t_ui("Ja, Flug abbrechen", "Yes, cancel flight"),
            command=yes,
            bg=RED,
            fg="#FFFFFF",
            activebackground=RED_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=10,
            cursor="hand2",
        ).pack(side="left", expand=True, fill="x", padx=(0, 8))
        tk.Button(
            row,
            text=t_ui("Nein, Flug loggen", "No, log flight"),
            command=no,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=10,
            cursor="hand2",
        ).pack(side="left", expand=True, fill="x", padx=(8, 0))
        dlg.protocol("WM_DELETE_WINDOW", no)

    def _tick_bar(self) -> None:
        if not self._alive:
            return
        c = self.canvas
        try:
            w = max(c.winfo_width(), 40)
        except Exception:
            w = 400
        h = 18
        c.delete("all")
        c.create_rectangle(0, 4, w, h - 2, fill=CARD, outline=LINE, width=1)
        if self._phase == "logging":
            span = max(70, int(w * 0.28))
            x = int((self._anim % 1.0) * (w + span) - span)
            c.create_rectangle(max(1, x), 5, min(w - 1, x + span), h - 3, fill=GREEN, outline="")
            self._anim += 0.018
        elif self._result_ok:
            c.create_rectangle(1, 5, w - 1, h - 3, fill=GREEN, outline="")
        elif self._result_kind == "unknown":
            c.create_rectangle(1, 5, w - 1, h - 3, fill=AMBER, outline="")
        else:
            c.create_rectangle(1, 5, w - 1, h - 3, fill=RED, outline="")
        self.win.after(16, self._tick_bar)

    def _logged_meanwhile(self) -> Optional[dict]:
        """The server's answer if this window's flight was logged in the meantime
        (by the background retry loop) – review S8."""
        try:
            from flyconomy_client import ended_result_for

            key = getattr(self, "_session_key", "")
            return ended_result_for(key) if key else None
        except Exception:
            return None

    def _worker(self) -> None:
        from flyconomy_client import (
            RetryableError,
            action_cancel,
            action_end,
            STATE,
        )

        last_err = ""
        while not _cancel.is_set():
            done = self._logged_meanwhile()
            if done is not None:
                self._finish(True, _logged_text(done))
                return
            try:
                result = action_end(notify=False, persist_retry=False)
            except RetryableError as e:
                last_err = str(e)
                self._set_sub(
                    t_ui(
                        "Server nicht erreichbar – neuer Versuch in ein paar Sekunden…",
                        "Server unreachable — retrying in a few seconds…",
                    )
                )
                self._sleep_or_cancel(4.0)
                continue
            except Exception as e:
                last_err = str(e)
                low = last_err.lower()
                if any(x in low for x in ("nicht erreichbar", "timeout", "timed out", "connection", "verbindung", "503", "502", "504")):
                    self._set_sub(
                        t_ui(
                            "Server nicht erreichbar – neuer Versuch in ein paar Sekunden…",
                            "Server unreachable — retrying in a few seconds…",
                        )
                    )
                    self._sleep_or_cancel(4.0)
                    continue
                done = self._logged_meanwhile()
                if done is not None:
                    self._finish(True, _logged_text(done))
                    return
                self._finish(False, last_err or t_ui("Flug abgebrochen.", "Flight cancelled."))
                return
            if result.get("retry"):
                if result.get("busy"):
                    self._set_sub(
                        t_ui(
                            "Der Server bucht den Flug gerade – das Ergebnis kommt gleich…",
                            "The server is booking the flight right now – the result follows…",
                        )
                    )
                else:
                    self._set_sub(
                        t_ui(
                            "Server nicht erreichbar – neuer Versuch in ein paar Sekunden…",
                            "Server unreachable — retrying in a few seconds…",
                        )
                    )
                try:
                    wait = float(result.get("retry_after_seconds") or 4.0)
                except (TypeError, ValueError):
                    wait = 4.0
                self._sleep_or_cancel(max(4.0, min(wait, 15.0)))
                continue
            if result.get("rejected"):
                done = self._logged_meanwhile()
                if done is not None:
                    self._finish(True, _logged_text(done))
                    return
                if result.get("unknown"):
                    # "Kein aktiver Flug" from the old server after a resend: most
                    # likely paid already – never shown as "cancelled" (review S8).
                    self._finish(
                        False,
                        str(result.get("message") or ""),
                        code="FC-END-UNKNOWN",
                        kind="unknown",
                    )
                    return
                self._finish(False, str(result.get("message") or t_ui("Flug abgebrochen.", "Flight cancelled.")))
                return
            if result.get("keep_flying"):
                self._finish(
                    False,
                    str(result.get("message") or t_ui("Flug läuft weiter.", "Flight continues.")),
                    code=str(result.get("code") or "FC-KEEP-FLYING"),
                )
                return
            if result.get("queued"):
                self._set_sub(str(result.get("message") or ""))
                self._sleep_or_cancel(4.0)
                continue
            self._finish(True, _logged_text(result))
            return

        try:
            cancelled = action_cancel()
        except Exception:
            cancelled = {}
        if isinstance(cancelled, dict) and cancelled.get("code") in ("CANCEL_UNCONFIRMED", "END_BUSY"):
            # RL-C05: the server did not confirm – the stored landing stays and is resent later.
            self._finish(
                False,
                str(cancelled.get("error") or ""),
                code="FC-CANCEL-UNCONFIRMED",
                kind="unknown",
            )
            return
        self._finish(
            False,
            t_ui(
                "Flug abgebrochen. Der Flug wurde nicht geloggt.",
                "Flight cancelled. The flight was not logged.",
            ),
        )

    def _sleep_or_cancel(self, sec: float) -> None:
        end = time.time() + sec
        while time.time() < end and not _cancel.is_set():
            time.sleep(0.15)

    def _set_sub(self, text: str) -> None:
        try:
            self.win.after(0, lambda: self.sub_var.set(text))
        except Exception:
            pass

    def _finish(self, ok: bool, message: str, code: str = "", kind: str = "") -> None:
        # D2: the sender is done – free the flags right away (the window may stay
        # open with the result). The auto-end waits until the window is closed.
        _worker_done()

        def apply() -> None:
            self._phase = "done"
            self._result_ok = ok
            self._result_kind = kind or ""
            self._err_code = code or ""
            still = False
            try:
                from flyconomy_client import STATE as _st

                still = bool(_st.active_flight_id) and not ok
            except Exception:
                still = False
            if ok:
                self.title_var.set(t_ui("Flug geloggt", "Flight logged"))
            elif kind == "unknown":
                self.title_var.set(
                    t_ui("Status unklar – bitte unter „Flüge“ prüfen", "Status unclear – please check “Flights”")
                )
            elif still:
                self.title_var.set(t_ui("Flug läuft weiter", "Flight still active"))
            else:
                self.title_var.set(t_ui("Flug abgebrochen", "Flight cancelled"))
            shown = message
            if not ok and self._err_code:
                shown = f"{message}\n\n{t_ui('Fehlercode', 'Error code')}  {self._err_code}"
            self.sub_var.set(shown)
            try:
                self.btn_min.pack_forget()
                self.btn_abort.pack_forget()
            except Exception:
                pass
            try:
                if not ok and still:
                    self.btn_retry.pack(fill="x", pady=(8, 0))
                if not ok:
                    self.btn_report.pack(fill="x", pady=(8, 0))
                self.btn_ok.pack(fill="x", pady=(8, 0))
            except Exception:
                pass
            self.restore()
            if ok:
                # D2: a success closes by itself after 20 s
                try:
                    self.win.after(SUCCESS_AUTO_CLOSE_MS, self._auto_close)
                except Exception:
                    pass

        try:
            self.win.after(0, apply)
        except Exception:
            pass

    def _auto_close(self) -> None:
        if self._phase == "done" and self._result_ok:
            self.close()

    def retry(self) -> None:
        global _busy
        with _lock:
            if self._phase == "logging" and self._worker_alive:
                return
            _busy = True
            try:
                from flyconomy_client import STATE as _st

                _st._log_ui_open = True
            except Exception:
                pass
        _cancel.clear()
        self._phase = "logging"
        self._result_ok = False
        self._result_kind = ""
        self._err_code = ""
        self.title_var.set(t_ui("Der Flug wird geloggt…", "Logging the flight…"))
        self.sub_var.set(
            t_ui(
                "Bitte warten. Die Flugdaten werden an den Server gesendet.",
                "Please wait. Flight data is being sent to the server.",
            )
        )
        try:
            self.btn_ok.pack_forget()
            self.btn_report.pack_forget()
            self.btn_retry.pack_forget()
        except Exception:
            pass
        try:
            self.btn_min.pack(side="left", expand=True, fill="x", padx=(0, 8))
            self.btn_abort.pack(side="left", expand=True, fill="x", padx=(8, 0))
        except Exception:
            pass
        self._start_worker()
        self._bring_front()

    def report(self) -> None:
        try:
            from client_notice import open_report

            open_report(
                code=self._err_code or "FC-LOG",
                title=self.title_var.get(),
                message=self.sub_var.get(),
            )
        except Exception:
            pass

    def close(self) -> None:
        self._alive = False
        try:
            from flyconomy_client import AUTO_END_FAIL_COOLDOWN_SEC, STATE

            STATE._auto_end_cooldown_until = time.time() + AUTO_END_FAIL_COOLDOWN_SEC
            STATE._park_ticks = 0
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        _release()


def _logged_text(result: dict) -> str:
    """Net result and undelivered jobs (D12)."""
    from flyconomy_client import end_result_lines

    return "\n".join(end_result_lines(result or {}))


def _worker_done() -> None:
    global _busy
    with _lock:
        _busy = False
    try:
        from flyconomy_client import AUTO_END_FAIL_COOLDOWN_SEC, STATE

        STATE._auto_ending = False
        STATE._park_ticks = 0
        STATE._auto_end_cooldown_until = max(
            float(STATE._auto_end_cooldown_until or 0), time.time() + AUTO_END_FAIL_COOLDOWN_SEC
        )
    except Exception:
        pass


def _release() -> None:
    global _busy, _win
    with _lock:
        _busy = False
        _win = None
    try:
        from flyconomy_client import STATE

        STATE._log_ui_open = False
        STATE._auto_ending = False
    except Exception:
        pass
