"""One place that runs child processes quietly: no console window flashes on Windows, and the child never reads the caller's stdin."""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run(args, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run with creationflags=CREATE_NO_WINDOW (Windows only) and stdin=DEVNULL unless `input` or `stdin` is given."""
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    if "input" not in kwargs and "stdin" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    return subprocess.run(args, **kwargs)
