"""Discord Rich Presence for FlyConomy Client / MiniClient."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

# Same application as FlyConomy Discord OAuth (Developer Portal → General → Application ID).
DISCORD_APP_ID = "1536150112412110848"

PresenceStateFn = Callable[[], dict[str, Any]]


class DiscordPresence:
    """Keeps Discord Rich Presence in sync while the client runs."""

    def __init__(self, app_id: str = DISCORD_APP_ID) -> None:
        self.app_id = str(app_id)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state_fn: Optional[PresenceStateFn] = None
        self._log: Optional[Callable[[str], None]] = None
        self._connected_once = False

    def bind_logger(self, fn: Callable[[str], None]) -> None:
        self._log = fn

    def _log_msg(self, msg: str) -> None:
        if not self._log:
            return
        try:
            self._log(msg)
        except Exception:
            pass

    def start(self, state_fn: PresenceStateFn) -> None:
        if self._thread and self._thread.is_alive():
            self._state_fn = state_fn
            return
        self._state_fn = state_fn
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="flyconomy-discord-rpc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        rpc: Any = None
        backoff = 5.0
        while not self._stop.is_set():
            try:
                from pypresence import Presence  # type: ignore
            except ImportError:
                self._log_msg("Discord Rich Presence: pypresence fehlt (pip install pypresence)")
                return

            try:
                if rpc is None:
                    rpc = Presence(self.app_id)
                    rpc.connect()
                    backoff = 5.0
                    if not self._connected_once:
                        self._connected_once = True
                        self._log_msg("Discord Rich Presence aktiv")

                snap = (self._state_fn or (lambda: {}))()
                details = str(snap.get("details") or "FlyConomy.de")[:128]
                state = str(snap.get("state") or "").strip()[:128] or None
                kwargs: dict[str, Any] = {
                    "details": details,
                    "state": state,
                    "large_image": snap.get("large_image") or "logo",
                    "large_text": str(snap.get("large_text") or "FlyConomy.de")[:128],
                }
                start_ts = snap.get("start")
                if isinstance(start_ts, (int, float)) and start_ts > 0:
                    kwargs["start"] = int(start_ts)
                buttons = snap.get("buttons")
                if isinstance(buttons, list) and buttons:
                    kwargs["buttons"] = buttons
                try:
                    rpc.update(**kwargs)
                except Exception:
                    kwargs.pop("buttons", None)
                    rpc.update(**kwargs)
            except Exception:
                rpc = None
                time.sleep(backoff)
                backoff = min(60.0, backoff * 1.5)
                continue

            for _ in range(15):
                if self._stop.is_set():
                    break
                time.sleep(1)

        try:
            if rpc is not None:
                rpc.close()
        except Exception:
            pass
