"""Find a unique Linux process by its canonical executable, without path walks."""
import os


def unique_executable_pid(executable, proc_root='/proc'):
    """Return one exact match or None; the caller supplies a canonical path.

    Linux /proc/PID/exe already gives the canonical executable path. Resolving
    every component again is unnecessary. Keep the full-path check: process
    names can change, and unrelated applications may share a basename.
    """
    expected = os.fspath(executable)
    match = None
    with os.scandir(proc_root) as entries:
        for entry in entries:
            if not entry.name.isascii() or not entry.name.isdigit():
                continue
            try:
                target = os.readlink(os.path.join(entry.path, 'exe'))
            except OSError:
                # Permission restrictions and processes exiting during a scan.
                continue
            if target != expected:
                continue
            if match is not None:
                return None
            match = int(entry.name)
    return match
