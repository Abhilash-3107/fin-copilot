"""One running copy per data folder.

Two copies of the app on the same ledger would each reap, claim and run the
other's sorting jobs (a second one failing to bind its port still runs startup
first). An operating-system file lock settles it before anything touches the
database: the second copy cannot take the lock and stops with a plain message.
The OS drops the lock when the process ends however it ends, so a crash never
leaves a stale lock behind.

The lock is per data folder: the app's lifespan locks the folder the database
lives in, the launcher locks the default data folder, and the two are the same
folder unless someone points the database elsewhere on purpose.
"""
from __future__ import annotations

import contextlib
import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_WINDOWS = os.name == "nt"

# The errors that mean "another copy holds the lock": a non-blocking flock says
# EWOULDBLOCK/EAGAIN; msvcrt.locking says EACCES (or EDEADLK after its retries).
_HELD_POSIX = {errno.EWOULDBLOCK, errno.EAGAIN}
_HELD_WINDOWS = {errno.EACCES, errno.EDEADLK}

LOCK_NAME = "finance.lock"

# Locks this process holds, by lock-file path, so the launcher and the app's
# own startup (same process) can both ask without blocking each other.
_held: dict[Path, object] = {}


class DataFolderInUse(RuntimeError):
    def __init__(self, folder: Path):
        super().__init__(
            f"Finance Copilot is already running on {folder}. "
            "Use that window, or stop it before starting another on the same folder."
        )
        self.folder = folder


def _try_lock(handle) -> bool:
    """Whether this process may go on: True when it took the lock, and also
    when the platform or filesystem cannot lock at all (warned, and it starts
    without one: a copy that cannot lock must not lock the person out of their
    own ledger). False only when another copy holds it."""
    try:
        if _WINDOWS:
            import msvcrt

            handle.seek(0)  # the lock covers the byte at the current position
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (_HELD_WINDOWS if _WINDOWS else _HELD_POSIX):
            return False
        logger.warning("Could not lock the data folder (%s); starting without the lock.", e)
    return True


def _release(handle) -> None:
    if _WINDOWS:
        import msvcrt

        # Windows releases on close too, but only once the process is done with
        # the byte it locked; unlock it explicitly first.
        with contextlib.suppress(OSError):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def lock_data_folder(folder: str | Path) -> bool:
    """Take the folder's lock for this process. Returns True when this call took
    it and False when this process already held it. Raises DataFolderInUse when
    another process has it."""
    folder = Path(folder).resolve()
    path = folder / LOCK_NAME
    if path in _held:
        return False
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - held open for the life of the lock
    if not _try_lock(handle):
        handle.close()
        raise DataFolderInUse(folder)
    _held[path] = handle
    return True


def unlock_data_folder(folder: str | Path) -> None:
    """Give the lock back (closing the file releases it on every platform)."""
    handle = _held.pop(Path(folder).resolve() / LOCK_NAME, None)
    if handle is not None:
        _release(handle)
