"""Best-effort macOS sleep prevention during long-running generation work."""

from __future__ import annotations

import atexit
import subprocess

_caffeinate_process: subprocess.Popen[bytes] | None = None


def prevent_sleep() -> None:
    """Start ``caffeinate`` once when generation begins processing jobs."""

    global _caffeinate_process
    if _caffeinate_process is not None and _caffeinate_process.poll() is None:
        return

    try:
        _caffeinate_process = subprocess.Popen(
            ["caffeinate", "-i", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(allow_sleep)
    except FileNotFoundError:
        _caffeinate_process = None


def allow_sleep() -> None:
    """Stop ``caffeinate`` and restore normal system sleep behavior."""

    global _caffeinate_process
    if _caffeinate_process is None:
        return

    if _caffeinate_process.poll() is None:
        _caffeinate_process.terminate()
        try:
            _caffeinate_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _caffeinate_process.kill()
            _caffeinate_process.wait(timeout=2)
    _caffeinate_process = None
