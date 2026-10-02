"""PID lock: refuse to run a second daemon/check against the same config.

Uses an exclusive-create lock file holding the owner's PID. Stale locks
(crashed/killed processes) are detected via signal 0 and taken over.
Best-effort on platforms without process signalling: falls back to refusing
when the file exists and is fresh (< 60s old is treated as live).
"""

from __future__ import annotations

import errno
import os
import time


class LockHeldError(RuntimeError):
    def __init__(self, pid: str):
        super().__init__(f"Another Echelon process is already running (pid {pid}).")
        self.pid = pid


def default_lock_path(config_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(config_path)) or "."
    return os.path.join(directory, "echelon.lock")


def _pid_alive(pid: int) -> bool | None:
    """True if alive, False if definitely dead, None if unknown."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, we just cannot signal it
    except OSError:
        return None
    else:
        return True


class PidLock:
    def __init__(self, path: str):
        self.path = path
        self._held = False

    def acquire(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            self._takeover_or_raise()
            return
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        self._held = True

    def _takeover_or_raise(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                old_pid = fh.read().strip()
        except OSError:
            old_pid = ""
        try:
            pid_num = int(old_pid)
        except (TypeError, ValueError):
            pid_num = -1
        alive: bool | None = None
        if pid_num > 0:
            alive = _pid_alive(pid_num)
        if alive is False or (alive is None and self._is_stale()):
            # Dead owner (or unreadable state with a stale file): take over.
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.acquire()
            return
        raise LockHeldError(old_pid or "unknown")

    def _is_stale(self, max_age: float = 3600.0) -> bool:
        try:
            return (time.time() - os.path.getmtime(self.path)) > max_age
        except OSError:
            return True

    def release(self) -> None:
        if not self._held:
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                owner = fh.read().strip()
        except OSError:
            owner = ""
        # Only remove our own lock (never another process's).
        if owner == str(os.getpid()):
            try:
                os.remove(self.path)
            except OSError:
                pass
        self._held = False

    def __enter__(self) -> PidLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
