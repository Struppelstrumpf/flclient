#!/usr/bin/env python3
"""FlyConomy MiniClient entrypoint (system-tray bridge)."""
import os
import sys
import traceback


def _crash_log(exc: BaseException) -> None:
    path = os.path.join(os.path.expanduser("~"), ".flyconomy_miniclient_crash.log")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{type(exc).__name__}: {exc}\n\n")
            traceback.print_exc(file=f)
    except Exception:
        pass


# Force mini mode even if launched without flags.
sys.argv = [sys.argv[0], "--mini", *sys.argv[1:]]

try:
    from flyconomy_client import main  # noqa: E402
except Exception as e:
    _crash_log(e)
    raise

if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            _crash_log(e)
        raise
    except Exception as e:
        _crash_log(e)
        raise
