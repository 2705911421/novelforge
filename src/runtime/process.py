"""Small Host-owned helpers for launching declared argv commands.

Windows command shims such as npm-generated ``.cmd`` files are discoverable
through ``PATH`` but a bare command name is not always resolvable by
``CreateProcess``.  Resolve that name before handing the argv to the
supervisor; never turn the command into a shell string.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Sequence


def resolve_executable_argv(command: Sequence[str]) -> tuple[str, ...]:
    """Return an argv tuple whose executable can be launched by the Host.

    The function preserves every argument as a separate argv item.  On
    Windows it resolves bare names such as ``gemini`` to the npm-generated
    ``gemini.cmd`` shim, and explicitly invokes a declared PowerShell script
    through PowerShell without enabling a shell command string.
    """

    if isinstance(command, (str, bytes)):
        raise TypeError("process command must be an argv sequence")
    argv = tuple(str(item) for item in command)
    if not argv or not argv[0].strip():
        raise ValueError("process command must include an executable")

    executable = argv[0].strip()
    suffix = Path(executable).suffix.casefold()

    if suffix == ".ps1" and os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"
        return (powershell, "-NoProfile", "-File", executable, *argv[1:])

    resolved = shutil.which(executable)
    if resolved:
        executable = resolved
    return (executable, *argv[1:])
