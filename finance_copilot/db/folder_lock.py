"""One running copy per data folder.

Two copies of the app on the same ledger would each reap, claim and run the
other's sorting jobs (a second one failing to bind its port still runs startup
first). An operating-system file lock settles it before anything touches the
database: the second copy cannot take the lock and stops with a plain message.
The OS drops the lock when the process ends however it ends, so a crash never
leaves a stale lock behind.
"""
from __future__ import annotations

import os
from pathlib import Path

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
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


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
        handle.close()
